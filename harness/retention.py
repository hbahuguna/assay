"""Tenant retention, signed export, and deletion controls.

Retention is explicit per workspace.  An export is a ZIP archive containing the
tenant's state and evidence plus a signed manifest.  Deletion is soft first:
the workspace is marked deleted and retained for a grace period, after which a
hard delete removes tenant roots, queue records, cached exports, and usage
state while leaving an audit record.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import shutil
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .auth import AuthStore
from .billing import BillingStore
from .determinism import format_timestamp, parse_timestamp
from .errors import HarnessError
from .models import new_id, utc_now
from .queue import JobQueue
from .quota import QuotaStore
from .signup import SignupStore


SCHEMA_VERSION = "quality-harness.retention/v1"
MANIFEST_SCHEMA_VERSION = "quality-harness.export-manifest/v1"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_GRACE_DAYS = 7
DEFAULT_EXPORTS = Path(".harness") / "exports"
DEFAULT_AUDIT = Path(".harness") / "cloud" / "deletion-audit.jsonl"
DEFAULT_SIGNING_KEY = Path(".harness") / "cloud" / "retention-signing.key"
DEFAULT_LOCK = Path(".harness") / "cloud" / "retention.lock"

_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


def _safe_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _safe_workspace(value: Any) -> str:
    text = _safe_text(value, 128)
    if not text or "/" in text or "\\" in text or ".." in text:
        raise ValueError("Invalid workspace id: {}".format(value))
    return text


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


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = str(path.resolve())
    with _LOCK_REGISTRY_GUARD:
        lock = _LOCK_REGISTRY.setdefault(key, threading.Lock())
    with lock:
        yield


def _load_signing_key(path: Path) -> bytes:
    if path.exists():
        data = path.read_bytes()
        if data:
            return data
    key = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".key-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
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
    return key


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class RetentionStore:
    """Retention policy, signed export, soft delete, and hard delete."""

    def __init__(
        self,
        root: Path,
        *,
        signup: Optional[SignupStore] = None,
        queue: Optional[JobQueue] = None,
        billing: Optional[BillingStore] = None,
        auth: Optional[AuthStore] = None,
        quota_root: Optional[Path] = None,
        default_retention_days: int = DEFAULT_RETENTION_DAYS,
        grace_period_days: int = DEFAULT_GRACE_DAYS,
        signing_key: Optional[bytes] = None,
        signing_key_path: Optional[Path] = None,
        exports_root: Optional[Path] = None,
        audit_path: Optional[Path] = None,
        lock_path: Optional[Path] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.signup = signup if signup is not None else SignupStore(self.root)
        self.queue = queue if queue is not None else JobQueue(self.root)
        self.billing = billing if billing is not None else BillingStore(self.root)
        self.auth = auth if auth is not None else AuthStore(self.root)
        self.quota_root = quota_root if quota_root is not None else self.root
        self.default_retention_days = int(default_retention_days or DEFAULT_RETENTION_DAYS)
        self.grace_period_days = int(grace_period_days or DEFAULT_GRACE_DAYS)
        self.exports_root = self._resolve(exports_root or DEFAULT_EXPORTS)
        self.audit_path = self._resolve(audit_path or DEFAULT_AUDIT)
        self.lock_path = self._resolve(lock_path or DEFAULT_LOCK)
        self.signing_key_path = self._resolve(signing_key_path or DEFAULT_SIGNING_KEY)
        self.signing_key = signing_key if signing_key is not None else _load_signing_key(self.signing_key_path)

    def _resolve(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path

    # --------------------------------------------------------------- policy
    def policy(self, workspace_id: str) -> Dict[str, Any]:
        workspace_key = _safe_workspace(workspace_id)
        record = self.signup.get_signup(workspace_key)
        retention_days = self.default_retention_days
        plan_id = None
        if isinstance(record, dict):
            retention_days = int(record.get("retention_days", retention_days))
            plan = record.get("plan")
            if isinstance(plan, dict):
                plan_id = plan.get("id")
                if "retention_days" in plan:
                    retention_days = int(plan.get("retention_days", retention_days))
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": workspace_key,
            "retention_days": retention_days,
            "grace_period_days": self.grace_period_days,
            "plan_id": plan_id,
            "default": retention_days == self.default_retention_days,
        }

    def set_retention(self, workspace_id: str, retention_days: int) -> Dict[str, Any]:
        workspace_key = _safe_workspace(workspace_id)
        if int(retention_days) < 1:
            raise ValueError("retention_days must be positive")
        with _locked(self.lock_path):
            data = self.signup._load()
            record = data["workspaces"].get(workspace_key)
            if not isinstance(record, dict):
                record = {
                    "workspace_id": workspace_key,
                    "name": workspace_key,
                    "created_at": utc_now(),
                }
            record["retention_days"] = int(retention_days)
            record["updated_at"] = utc_now()
            data["workspaces"][workspace_key] = record
            self.signup._save(data)
        return self.policy(workspace_key)

    # --------------------------------------------------------------- export
    def export(self, workspace_id: str, *, now: Any = None) -> Dict[str, Any]:
        workspace_key = _safe_workspace(workspace_id)
        now_text = format_timestamp(now) if now is not None else utc_now()
        export_id = "export-{}".format(new_id("export").split("-", 1)[-1])
        export_dir = self.exports_root / workspace_key
        export_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(export_dir, 0o700)
        archive_path = export_dir / "{}.zip".format(export_id)
        staging = Path(tempfile.mkdtemp(prefix="export-"))
        try:
            tenant_root = self.queue._tenant_root(workspace_key)
            included: List[Dict[str, Any]] = []
            if tenant_root.exists():
                for path in sorted(tenant_root.rglob("*")):
                    if path.is_file():
                        relative = path.relative_to(tenant_root).as_posix()
                        if relative.startswith("..") or Path(relative).is_absolute():
                            continue
                        destination = staging / "tenant" / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, destination)
                        included.append(self._file_entry(destination, "tenant/{}".format(relative)))

            # Add filtered central records without cross-tenant data.
            central = staging / "central"
            central.mkdir(parents=True, exist_ok=True)
            queue_jobs = [
                job
                for job in self.queue.list_jobs(workspace_key)
            ]
            (central / "queue-jobs.json").write_text(
                json.dumps(queue_jobs, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            included.append(self._file_entry(central / "queue-jobs.json", "central/queue-jobs.json"))

            billing_state = self.billing.status(workspace_key)
            (central / "billing-status.json").write_text(
                json.dumps(billing_state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            included.append(self._file_entry(central / "billing-status.json", "central/billing-status.json"))

            signup_record = self.signup.get_signup(workspace_key)
            if signup_record is not None:
                (central / "signup.json").write_text(
                    json.dumps(signup_record, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                included.append(self._file_entry(central / "signup.json", "central/signup.json"))

            retention_policy = self.policy(workspace_key)
            (central / "retention-policy.json").write_text(
                json.dumps(retention_policy, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            included.append(
                self._file_entry(central / "retention-policy.json", "central/retention-policy.json")
            )

            # Build manifest.
            manifest = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "export_id": export_id,
                "workspace_id": workspace_key,
                "created_at": now_text,
                "retention": retention_policy,
                "files": [
                    {
                        "path": entry["path"],
                        "sha256": entry["sha256"],
                        "size": entry["size"],
                    }
                    for entry in included
                ],
            }
            manifest["signature"] = hmac.new(
                self.signing_key,
                _canonical({k: v for k, v in manifest.items() if k != "signature"}).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(staging).as_posix())
            os.chmod(archive_path, 0o600)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        return {
            "schema_version": SCHEMA_VERSION,
            "status": "exported",
            "workspace_id": workspace_key,
            "export_id": export_id,
            "archive": str(archive_path),
            "manifest_signature": manifest["signature"],
            "file_count": len(manifest["files"]),
        }

    def _file_entry(self, path: Path, relative: str) -> Dict[str, Any]:
        return {
            "path": relative,
            "sha256": _sha256_file(path),
            "size": path.stat().st_size,
        }

    def verify_export(self, archive_path: Union[str, Path]) -> Dict[str, Any]:
        path = Path(archive_path).expanduser().resolve()
        if not path.is_file():
            raise HarnessError("Export archive not found: {}".format(path))
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if "manifest.json" not in names:
                return {"valid": False, "reason": "missing manifest"}
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
                return {"valid": False, "reason": "bad manifest schema"}
            signature = manifest.get("signature")
            expected = hmac.new(
                self.signing_key,
                _canonical({k: v for k, v in manifest.items() if k != "signature"}).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(str(signature), expected):
                return {"valid": False, "reason": "invalid signature"}
            for entry in manifest.get("files", []):
                relative = str(entry.get("path", ""))
                if relative not in names:
                    return {"valid": False, "reason": "missing file {}".format(relative)}
                if ".." in relative.split("/") or relative.startswith("/"):
                    return {"valid": False, "reason": "unsafe path {}".format(relative)}
                data = archive.read(relative)
                if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                    return {"valid": False, "reason": "hash mismatch {}".format(relative)}
                if len(data) != int(entry.get("size", -1)):
                    return {"valid": False, "reason": "size mismatch {}".format(relative)}
            # Ensure no cross-tenant paths in archive names.
            workspace_id = str(manifest.get("workspace_id", ""))
            for name in names:
                if name.startswith("../") or "/../" in name or name.startswith("/"):
                    return {"valid": False, "reason": "unsafe archive path {}".format(name)}
            return {
                "valid": True,
                "reason": "ok",
                "workspace_id": workspace_id,
                "file_count": len(manifest.get("files", [])),
            }

    # --------------------------------------------------------------- delete
    def soft_delete(
        self,
        workspace_id: str,
        *,
        grace_days: Optional[int] = None,
        actor: str = "operator",
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_workspace(workspace_id)
        grace = int(grace_days if grace_days is not None else self.grace_period_days)
        if grace < 0:
            raise ValueError("grace_days cannot be negative")
        now_text = format_timestamp(now) if now is not None else utc_now()
        hard_after = format_timestamp(parse_timestamp(now_text) + timedelta(days=grace))
        with _locked(self.lock_path):
            data = self.signup._load()
            record = data["workspaces"].get(workspace_key)
            if not isinstance(record, dict):
                record = {
                    "workspace_id": workspace_key,
                    "name": workspace_key,
                    "created_at": utc_now(),
                }
            if record.get("deleted_at"):
                return {
                    "schema_version": SCHEMA_VERSION,
                    "status": "already_soft_deleted",
                    "workspace_id": workspace_key,
                    "deleted_at": record.get("deleted_at"),
                    "hard_delete_after": record.get("hard_delete_after"),
                }
            record["deleted_at"] = now_text
            record["hard_delete_after"] = hard_after
            record["deleted_by"] = _safe_text(actor, 120)
            record["updated_at"] = now_text
            data["workspaces"][workspace_key] = record
            self.signup._save(data)
        self._audit("soft_deleted", workspace_key, actor=actor, deleted_at=now_text, hard_delete_after=hard_after)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "soft_deleted",
            "workspace_id": workspace_key,
            "deleted_at": now_text,
            "hard_delete_after": hard_after,
            "grace_days": grace,
        }

    def hard_delete(
        self,
        workspace_id: str,
        *,
        force: bool = False,
        actor: str = "operator",
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_workspace(workspace_id)
        now_text = format_timestamp(now) if now is not None else utc_now()
        with _locked(self.lock_path):
            data = self.signup._load()
            record = data["workspaces"].get(workspace_key)
            if not isinstance(record, dict):
                # Already deleted; still allow forced cleanup of leftovers.
                record = {"workspace_id": workspace_key}
            hard_after = record.get("hard_delete_after")
            if not force and hard_after:
                if parse_timestamp(now_text) < parse_timestamp(hard_after):
                    raise HarnessError(
                        "Workspace {} is in its deletion grace period until {}.".format(
                            workspace_key, hard_after
                        )
                    )
            removed = {
                "tenant_root": False,
                "queue_jobs": 0,
                "billing_state": False,
                "quota_state": False,
                "cached_exports": 0,
            }
            tenant_root = self.queue._tenant_root(workspace_key)
            if tenant_root.exists():
                shutil.rmtree(tenant_root, ignore_errors=True)
                removed["tenant_root"] = True
            exports_dir = self.exports_root / workspace_key
            if exports_dir.exists():
                removed["cached_exports"] = len(list(exports_dir.glob("*.zip")))
                shutil.rmtree(exports_dir, ignore_errors=True)
            removed["queue_jobs"] = self.queue.delete_workspace_jobs(workspace_key)
            removed["billing_state"] = self.billing.delete_workspace(workspace_key)
            removed["quota_state"] = self._delete_quota_workspace(workspace_key)
            # Remove signup record (hard delete) but audit remains.
            if workspace_key in data["workspaces"]:
                del data["workspaces"][workspace_key]
            self.signup._save(data)
            # Auth workspace and keys remain managed by AuthStore; remove if supported.
            try:
                self.auth.delete_workspace(workspace_key)
            except Exception:
                pass
        self._audit(
            "hard_deleted",
            workspace_key,
            actor=actor,
            deleted_at=now_text,
            removed=removed,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "deleted",
            "workspace_id": workspace_key,
            "deleted_at": now_text,
            "removed": removed,
        }

    delete = hard_delete

    def _delete_quota_workspace(self, workspace_id: str) -> bool:
        quota_path = self.quota_root / ".harness" / "quota" / "usage.json"
        if not quota_path.exists():
            return False
        try:
            data = json.loads(quota_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict) or not isinstance(data.get("workspaces"), dict):
            return False
        if workspace_id in data["workspaces"]:
            del data["workspaces"][workspace_id]
            _atomic_write(quota_path, json.dumps(data, indent=2, sort_keys=True) + "\n")
            return True
        return False

    def _audit(self, event: str, workspace_id: str, **fields: Any) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.audit_path.parent, 0o700)
        record = {
            "schema_version": SCHEMA_VERSION,
            "event": event,
            "workspace_id": workspace_id,
            "timestamp": utc_now(),
        }
        record.update(fields)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(self.audit_path, 0o600)

    def audit_log(self) -> List[Dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        records = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
        return records


# Aliases
TenantRetentionStore = RetentionStore
TenantExporter = RetentionStore
TenantDeletion = RetentionStore
RetentionManager = RetentionStore
