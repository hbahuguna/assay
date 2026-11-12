"""Self-serve signup, free allowance, plan selection, and signed entitlements.

The OSS core stays account-free: this module provides the deterministic,
local-first API that a cloud deployment can expose for onboarding.  A signup
creates exactly one workspace, one owner key, and one entitlement.  Replaying
the same request id returns the original result instead of creating duplicates.

Entitlements are HMAC-SHA256 signed with a local signing key and can be
verified offline.  They encode the selected tier, limits, workspace, and
effective billing period; changing any field or using an expired entitlement
fails verification.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .auth import AuthStore, SCOPE_ADMIN, SCOPE_EXPORT, SCOPE_READ, SCOPE_RUN
from .determinism import format_timestamp, parse_timestamp
from .errors import EntitlementError, PlanDowngradeError, SignupError
from .models import new_id, utc_now
from .quota import (
    RESOURCES,
    QuotaPlan,
    QuotaStore,
    coerce_limit,
    coerce_plan,
    quota_period,
)


SCHEMA_VERSION = "quality-harness.signup/v1"
ENTITLEMENT_SCHEMA_VERSION = "quality-harness.entitlement/v1"
DEFAULT_STORE = Path(".harness") / "cloud" / "signup.json"
DEFAULT_SIGNING_KEY = Path(".harness") / "cloud" / "entitlement-signing.key"
DEFAULT_LOCK = Path(".harness") / "cloud" / "signup.lock"

_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


class EntitlementVerification(dict):
    """A dict result that is truthy only when ``valid`` is true."""

    def __bool__(self) -> bool:
        return bool(self.get("valid", False))


def _safe_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".{}-".format(path.name), dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".{}-".format(path.name), dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = str(path.resolve())
    with _LOCK_REGISTRY_GUARD:
        lock = _LOCK_REGISTRY.setdefault(key, threading.Lock())
    with lock:
        yield


def _load_plan(plan: Any) -> QuotaPlan:
    if isinstance(plan, QuotaPlan):
        return plan
    if plan is None:
        return QuotaPlan(id="default", version="1", period="monthly", limits={})
    if isinstance(plan, (str, Path)):
        return QuotaPlan.load(plan)
    if isinstance(plan, Mapping):
        return QuotaPlan.from_dict(plan)
    raise SignupError("A quota plan must be a QuotaPlan, mapping, or path.")


def _coerce_now(now: Any) -> str:
    if now is None:
        return format_timestamp(utc_now())
    return format_timestamp(now)


_PERIOD_RE = __import__("re").compile(r"^(\d{4})-(\d{2})$")


def _period_for(value: Any) -> Dict[str, Any]:
    text = str(value or "").strip()
    match = _PERIOD_RE.match(text)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        return {
            "key": text,
            "start": format_timestamp(start),
            "end": format_timestamp(end),
        }
    return quota_period(value)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _load_signing_key(path: Path) -> bytes:
    if path.exists():
        data = path.read_bytes()
        if not data:
            raise EntitlementError("The entitlement signing key is empty.")
        return data
    key = secrets.token_bytes(32)
    _atomic_write_bytes(path, key)
    return key


def _signature(signing_key: bytes, payload: Mapping[str, Any]) -> str:
    digest = hmac.new(
        signing_key, _canonical(payload).encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class SignupStore:
    """Self-serve onboarding state, plan selection, and signed entitlements."""

    def __init__(
        self,
        root: Path,
        *,
        signing_key: Optional[Union[bytes, str, Path]] = None,
        store_path: Optional[Union[str, Path]] = None,
        signing_key_path: Optional[Union[str, Path]] = None,
        lock_path: Optional[Union[str, Path]] = None,
        auth_store: Optional[AuthStore] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.store_path = self._resolve(store_path or DEFAULT_STORE)
        self.lock_path = self._resolve(lock_path or DEFAULT_LOCK)
        self.signing_key_path = self._resolve(
            signing_key_path or DEFAULT_SIGNING_KEY
        )
        self.auth = auth_store if auth_store is not None else AuthStore(self.root)
        if signing_key is None:
            self.signing_key = _load_signing_key(self.signing_key_path)
        elif isinstance(signing_key, (str, Path)):
            path = Path(signing_key)
            if path.exists():
                self.signing_key = path.read_bytes()
            else:
                self.signing_key = str(signing_key).encode("utf-8")
        else:
            self.signing_key = bytes(signing_key)

    def _resolve(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path

    # --------------------------------------------------------------- storage
    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "requests": {},
            "workspaces": {},
        }

    def _load(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return self._empty()
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SignupError(
                "The signup store is unreadable: {}".format(exc)
            )
        if not isinstance(data, dict):
            raise SignupError("The signup store must contain a JSON object.")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise SignupError("Unsupported signup schema version.")
        data.setdefault("requests", {})
        data.setdefault("workspaces", {})
        if not isinstance(data["requests"], dict):
            data["requests"] = {}
        if not isinstance(data["workspaces"], dict):
            data["workspaces"] = {}
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        _atomic_write(
            self.store_path,
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    # --------------------------------------------------------------- signup
    def signup(
        self,
        request_id: str,
        *,
        name: str = "",
        plan: Any = None,
        effective_at: Optional[str] = None,
        now: Any = None,
        workspace_id: Optional[str] = None,
        owner_scopes: Optional[Iterable[str]] = None,
        owner_label: str = "owner",
    ) -> Dict[str, Any]:
        request_key = _safe_text(request_id)
        if not request_key:
            raise SignupError("A signup request id is required for idempotency.")
        now_text = _coerce_now(now)
        effective_text = _coerce_now(effective_at or now_text)
        with _locked(self.lock_path):
            data = self._load()
            if request_key in data["requests"]:
                record = data["requests"][request_key]
                workspace_record = data["workspaces"].get(
                    record["workspace_id"], {}
                )
                plan_obj = _load_plan(workspace_record.get("plan"))
                usage = self._usage_snapshot(
                    workspace_record["workspace_id"], plan_obj, now_text
                )
                return {
                    "schema_version": SCHEMA_VERSION,
                    "status": "replayed",
                    "replayed": True,
                    "request_id": request_key,
                    "workspace": {
                        "id": workspace_record["workspace_id"],
                        "name": workspace_record.get("name", ""),
                    },
                    "key": {
                        "id": record["key_id"],
                        "prefix": record["prefix"],
                        "scopes": list(record.get("scopes", [])),
                        "secret": None,
                    },
                    "plan": plan_obj.to_dict(),
                    "effective_at": workspace_record.get("effective_at", effective_text),
                    "entitlement": workspace_record.get("entitlement"),
                    "usage": usage,
                }

            workspace = self.auth.create_workspace(
                name or "Workspace", workspace_id=workspace_id
            )
            scopes = list(
                owner_scopes
                if owner_scopes is not None
                else (SCOPE_ADMIN, SCOPE_RUN, SCOPE_READ, SCOPE_EXPORT)
            )
            key = self.auth.create_key(
                workspace["id"], scopes=scopes, label=owner_label
            )
            plan_obj = _load_plan(plan)
            period = _period_for(effective_text)
            entitlement = self.issue_entitlement(
                workspace["id"],
                plan_obj,
                period=period["key"],
                effective_at=effective_text,
                issued_at=now_text,
                signup_request_id=request_key,
                key_id=key["id"],
            )
            quota = QuotaStore(
                self.root, plan=plan_obj, workspace=workspace["id"], enforce=True
            )
            quota.snapshot(now=effective_text)

            workspace_record = {
                "workspace_id": workspace["id"],
                "name": workspace.get("name", name or "Workspace"),
                "owner_key_id": key["id"],
                "default_key_id": key["id"],
                "plan": plan_obj.to_dict(),
                "effective_at": effective_text,
                "period": period["key"],
                "entitlement_id": entitlement["id"],
                "entitlement": entitlement,
                "created_at": now_text,
                "updated_at": now_text,
            }
            data["workspaces"][workspace["id"]] = workspace_record
            data["requests"][request_key] = {
                "request_id": request_key,
                "workspace_id": workspace["id"],
                "key_id": key["id"],
                "prefix": key["prefix"],
                "scopes": scopes,
                "plan_id": plan_obj.id,
                "entitlement_id": entitlement["id"],
                "created_at": now_text,
            }
            self._save(data)

            usage = self._usage_snapshot(workspace["id"], plan_obj, now_text)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "created",
                "replayed": False,
                "request_id": request_key,
                "workspace": {
                    "id": workspace["id"],
                    "name": workspace_record["name"],
                },
                "key": {
                    "id": key["id"],
                    "prefix": key["prefix"],
                    "scopes": scopes,
                    "secret": key["secret"],
                },
                "plan": plan_obj.to_dict(),
                "effective_at": effective_text,
                "entitlement": entitlement,
                "usage": usage,
            }

    def get_signup(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        workspace_key = _safe_text(workspace_id)
        with _locked(self.lock_path):
            data = self._load()
        record = data["workspaces"].get(workspace_key)
        return copy.deepcopy(record) if isinstance(record, dict) else None

    get_workspace = get_signup

    def delete_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        workspace_key = _safe_text(workspace_id)
        with _locked(self.lock_path):
            data = self._load()
            record = data["workspaces"].pop(workspace_key, None)
            # Remove any signup requests bound to this workspace.
            requests = data.get("requests", {})
            for request_id in [
                key for key, value in requests.items()
                if isinstance(value, dict) and value.get("workspace_id") == workspace_key
            ]:
                del requests[request_id]
            self._save(data)
        return copy.deepcopy(record) if isinstance(record, dict) else None

    def select_plan(
        self,
        workspace_id: str,
        plan: Any,
        *,
        effective_at: Optional[str] = None,
        now: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        if not workspace_key:
            raise SignupError("A workspace id is required.")
        now_text = _coerce_now(now)
        effective_text = _coerce_now(effective_at or now_text)
        with _locked(self.lock_path):
            data = self._load()
            record = data["workspaces"].get(workspace_key)
            if not isinstance(record, dict):
                raise SignupError("Workspace not found.")
            current_plan = _load_plan(record.get("plan"))
            new_plan = _load_plan(plan)
            current_usage = self._usage_snapshot(
                workspace_key, current_plan, now_text
            ).get("usage", {})
            for resource in RESOURCES:
                new_limit = new_plan.limit(resource).hard
                used = current_usage.get(resource, 0)
                if new_limit is not None and float(used) > float(new_limit) + 1e-9:
                    raise PlanDowngradeError(
                        "Plan {} would downgrade below current usage for {} "
                        "(used={}, new_limit={}).".format(
                            new_plan.id, resource, used, new_limit
                        )
                    )

            period = _period_for(effective_text)
            # Preserve usage in the current period while updating plan metadata.
            quota = QuotaStore(
                self.root, plan=new_plan, workspace=workspace_key, enforce=True
            )
            quota.snapshot(now=effective_text)

            entitlement = self.issue_entitlement(
                workspace_key,
                new_plan,
                period=period["key"],
                effective_at=effective_text,
                issued_at=now_text,
                signup_request_id=_safe_text(record.get("last_request_id", "")),
                key_id=_safe_text(record.get("default_key_id", "")),
            )
            record["plan"] = new_plan.to_dict()
            record["effective_at"] = effective_text
            record["period"] = period["key"]
            record["entitlement_id"] = entitlement["id"]
            record["entitlement"] = entitlement
            record["updated_at"] = now_text
            record.setdefault("plan_selections", []).append(
                {
                    "plan_id": new_plan.id,
                    "plan_version": new_plan.version,
                    "effective_at": effective_text,
                    "period": period["key"],
                    "selected_at": now_text,
                    "metadata": _safe_metadata(metadata),
                }
            )
            record["last_request_id"] = _safe_text(
                record.get("last_request_id", "")
            )
            data["workspaces"][workspace_key] = record
            self._save(data)

            usage = self._usage_snapshot(workspace_key, new_plan, now_text)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "plan_selected",
                "workspace": {"id": workspace_key, "name": record.get("name", "")},
                "plan": new_plan.to_dict(),
                "effective_at": effective_text,
                "period": period["key"],
                "entitlement": entitlement,
                "usage": usage,
            }

    def usage(
        self,
        workspace_id: str,
        plan: Any = None,
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        if not workspace_key:
            raise SignupError("A workspace id is required.")
        with _locked(self.lock_path):
            data = self._load()
        record = data["workspaces"].get(workspace_key)
        if not isinstance(record, dict):
            raise SignupError("Workspace not found.")
        plan_obj = _load_plan(plan if plan is not None else record.get("plan"))
        now_text = _coerce_now(now)
        snapshot = self._usage_snapshot(workspace_key, plan_obj, now_text)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "workspace": {"id": workspace_key, "name": record.get("name", "")},
            "plan": plan_obj.to_dict(),
            "effective_at": record.get("effective_at"),
            "entitlement": record.get("entitlement"),
            "quota": snapshot,
            "usage": snapshot.get("usage", {}),
            "remaining": snapshot.get("remaining", {}),
            "limits": snapshot.get("limits", {}),
            "period": snapshot.get("period"),
        }

    # ---------------------------------------------------------- entitlements
    def issue_entitlement(
        self,
        workspace_id: str,
        plan: Any,
        *,
        period: Optional[str] = None,
        effective_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        issued_at: Optional[str] = None,
        signup_request_id: str = "",
        key_id: str = "",
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        if not workspace_key:
            raise SignupError("A workspace id is required.")
        plan_obj = _load_plan(plan)
        issued_text = _coerce_now(issued_at or now)
        effective_text = _coerce_now(effective_at or issued_text)
        period_info = _period_for(period or effective_text)
        period_key = period_info["key"]
        expiry_text = _coerce_now(expires_at or period_info["end"])
        limits = {
            resource: plan_obj.limit(resource).to_dict() for resource in RESOURCES
        }
        payload = {
            "schema_version": ENTITLEMENT_SCHEMA_VERSION,
            "id": new_id("ent"),
            "workspace_id": workspace_key,
            "plan_id": plan_obj.id,
            "plan_version": plan_obj.version,
            "tier": plan_obj.id,
            "limits": limits,
            "period": period_key,
            "period_start": period_info["start"],
            "period_end": period_info["end"],
            "effective_at": effective_text,
            "issued_at": issued_text,
            "expires_at": expiry_text,
            "signup_request_id": _safe_text(signup_request_id),
            "key_id": _safe_text(key_id),
        }
        entitlement = dict(payload)
        entitlement["signature_algorithm"] = "HMAC-SHA256"
        signed_payload = {
            key: value for key, value in entitlement.items() if key != "signature"
        }
        entitlement["signature"] = _signature(self.signing_key, signed_payload)
        return entitlement

    def verify_entitlement(
        self,
        entitlement: Mapping[str, Any],
        *,
        signing_key: Optional[Union[bytes, str, Path]] = None,
        now: Any = None,
        workspace_id: Optional[str] = None,
        period: Optional[str] = None,
    ) -> EntitlementVerification:
        if not isinstance(entitlement, Mapping):
            return EntitlementVerification(
                {"valid": False, "reason": "entitlement must be a mapping"}
            )
        key = self._resolve_signing_key(signing_key)
        payload = {
            key: value for key, value in entitlement.items() if key != "signature"
        }
        expected = _signature(key, payload)
        if not hmac.compare_digest(
            str(entitlement.get("signature", "")), expected
        ):
            return EntitlementVerification(
                {"valid": False, "reason": "invalid signature", "workspace_id": entitlement.get("workspace_id")}
            )
        if workspace_id is not None and _safe_text(workspace_id) != _safe_text(
            entitlement.get("workspace_id")
        ):
            return EntitlementVerification(
                {"valid": False, "reason": "workspace mismatch", "workspace_id": entitlement.get("workspace_id")}
            )
        if period is not None and str(period) != str(entitlement.get("period")):
            return EntitlementVerification(
                {"valid": False, "reason": "period mismatch", "workspace_id": entitlement.get("workspace_id")}
            )
        expiry = entitlement.get("expires_at")
        if expiry:
            now_text = _coerce_now(now) if now is not None else _coerce_now(utc_now())
            try:
                if parse_timestamp(now_text) > parse_timestamp(expiry):
                    return EntitlementVerification(
                        {
                            "valid": False,
                            "reason": "expired",
                            "workspace_id": entitlement.get("workspace_id"),
                        }
                    )
            except (TypeError, ValueError):
                return EntitlementVerification(
                    {
                        "valid": False,
                        "reason": "invalid expiry",
                        "workspace_id": entitlement.get("workspace_id"),
                    }
                )
        return EntitlementVerification(
            {
                "valid": True,
                "reason": "ok",
                "workspace_id": entitlement.get("workspace_id"),
                "period": entitlement.get("period"),
            }
        )

    def _resolve_signing_key(
        self, signing_key: Optional[Union[bytes, str, Path]]
    ) -> bytes:
        if signing_key is None:
            return self.signing_key
        if isinstance(signing_key, (str, Path)):
            path = Path(signing_key)
            if path.exists():
                return path.read_bytes()
            return str(signing_key).encode("utf-8")
        return bytes(signing_key)

    def _usage_snapshot(
        self, workspace_id: str, plan: QuotaPlan, now: Any
    ) -> Dict[str, Any]:
        quota = QuotaStore(
            self.root, plan=plan, workspace=workspace_id, enforce=True
        )
        return quota.snapshot(now=now)

    def _safe_metadata(self, metadata: Optional[Mapping[str, Any]]) -> Dict[str, str]:
        safe: Dict[str, str] = {}
        for key, value in (metadata or {}).items():
            if value not in (None, ""):
                safe[str(key)[:80]] = str(value)[:200]
        return safe


def _safe_metadata(metadata: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    safe: Dict[str, str] = {}
    for key, value in (metadata or {}).items():
        if value not in (None, ""):
            safe[str(key)[:80]] = str(value)[:200]
    return safe


# Compatibility alias used by ledger-style callers.
SignupManager = SignupStore
EntitlementStore = SignupStore
OnboardingStore = SignupStore


def verify_entitlement(
    entitlement: Mapping[str, Any],
    *,
    signing_key: Optional[Union[bytes, str, Path]] = None,
    root: Optional[Path] = None,
    now: Any = None,
    workspace_id: Optional[str] = None,
    period: Optional[str] = None,
) -> EntitlementVerification:
    if signing_key is None and root is None:
        raise EntitlementError(
            "A signing key or root is required to verify an entitlement."
        )
    store_root = root if root is not None else Path(tempfile.gettempdir())
    store = SignupStore(store_root, signing_key=signing_key)
    return store.verify_entitlement(
        entitlement,
        signing_key=signing_key,
        now=now,
        workspace_id=workspace_id,
        period=period,
    )
