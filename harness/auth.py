"""Workspace, API-key, and scoped-token authentication for the cloud boundary.

The OSS harness remains account-free.  This module provides the cloud-side
contract used before an action can consume quota: every request resolves to
exactly one workspace, and a run id is visible only to the workspace that
claimed it.

Secrets are generated once and returned once.  The durable store keeps only a
per-key salt and a SHA-256 hash, plus a public prefix used for lookup and
rotation.  Authentication failures are typed 401/403 responses that never carry
workspace identifiers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

from .determinism import format_timestamp, parse_timestamp
from .errors import HarnessError
from .models import new_id, utc_now


AUTH_SCHEMA = "quality-harness.auth/v1"
AUDIT_SCHEMA = "quality-harness.auth.audit/v1"
DEFAULT_STORE = Path(".harness") / "cloud" / "auth.json"
DEFAULT_AUDIT = Path(".harness") / "cloud" / "auth-audit.jsonl"

SCOPE_RUN = "run"
SCOPE_READ = "read"
SCOPE_EXPORT = "export"
SCOPE_ADMIN = "admin"
SCOPES: Sequence[str] = (SCOPE_RUN, SCOPE_READ, SCOPE_EXPORT, SCOPE_ADMIN)

KEY_PREFIX = "qh_"

_UNSET = object()


class AuthError(HarnessError):
    """A machine-readable authentication or authorization failure.

    ``status_code`` is the HTTP status a cloud adapter should return.  The
    response body intentionally contains no workspace identifiers, including for
    wrong-scope and cross-workspace run lookups.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        required_scope: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.status = self.status_code
        self.code = str(code)
        self.required_scope = str(required_scope or "")

    @property
    def headers(self) -> Dict[str, str]:
        if self.status_code == 401:
            return {"WWW-Authenticate": 'Bearer realm="quality-harness"'}
        return {}

    def to_dict(self) -> Dict[str, Any]:
        error: Dict[str, Any] = {
            "code": self.code,
            "message": str(self),
        }
        if self.required_scope:
            error["required_scope"] = self.required_scope
        return {
            "status": self.status_code,
            "status_code": self.status_code,
            "error": error,
        }

    def response(self) -> Dict[str, Any]:
        return self.to_dict()


@dataclass(frozen=True)
class AuthContext:
    """The authenticated identity attached to one cloud action."""

    workspace_id: str
    key_id: str
    prefix: str
    scopes: Sequence[str]
    authenticated_at: str

    def has_scope(self, scope: str) -> bool:
        return SCOPE_ADMIN in self.scopes or scope in self.scopes

    def require(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise AuthError(
                403,
                "insufficient_scope",
                "The API key does not grant the required scope: {}.".format(scope),
                required_scope=scope,
            )

    def require_workspace(self, workspace_id: str) -> None:
        """Reject a request that names a workspace other than this key's own.

        A 404 is deliberate: a guessed workspace id and another tenant's real id
        must be indistinguishable from the caller's side.
        """

        if not workspace_id or str(workspace_id) != self.workspace_id:
            raise AuthError(404, "workspace_not_found", "Workspace not found.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "key_id": self.key_id,
            "prefix": self.prefix,
            "scopes": list(self.scopes),
            "authenticated_at": self.authenticated_at,
        }


def normalize_scopes(scopes: Union[str, Iterable[str], None]) -> List[str]:
    if scopes is None:
        raise ValueError("At least one scope is required.")
    if isinstance(scopes, str):
        values = [item.strip() for item in scopes.split(",")]
    else:
        values = [str(item).strip() for item in scopes]
    values = [item for item in values if item]
    if not values:
        raise ValueError("At least one scope is required.")
    unknown = sorted({item for item in values if item not in SCOPES})
    if unknown:
        raise ValueError(
            "Unknown scope(s): {}. Allowed scopes: {}.".format(
                ", ".join(unknown), ", ".join(SCOPES)
            )
        )
    # Keep canonical order so stored records and tests are stable.
    return [scope for scope in SCOPES if scope in values]


def normalize_expiry(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return format_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("expires_at must be an ISO-8601 timestamp: {}".format(exc))


def _hash_token(token: str, salt: str) -> str:
    return hashlib.sha256("{}:{}".format(salt, token).encode("utf-8")).hexdigest()


def _parse_prefix(token: str) -> str:
    if not token.startswith(KEY_PREFIX) or "." not in token:
        raise AuthError(401, "malformed_api_key", "The API key is malformed.")
    prefix, _, _secret = token.partition(".")
    if not prefix.startswith(KEY_PREFIX) or not prefix[len(KEY_PREFIX) :]:
        raise AuthError(401, "malformed_api_key", "The API key is malformed.")
    return prefix


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
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
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class AuthStore:
    """Durable workspace, scoped API-key, and run-ownership store."""

    def __init__(
        self,
        root: Path,
        store_path: Optional[Path] = None,
        audit_path: Optional[Path] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.store_path = self._resolve_path(store_path or DEFAULT_STORE)
        self.audit_path = self._resolve_path(audit_path or DEFAULT_AUDIT)

    def _resolve_path(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path

    # --------------------------------------------------------------- storage
    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": AUTH_SCHEMA,
            "workspaces": {},
            "keys": {},
            "runs": {},
        }

    def _load(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return self._empty()
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AuthError(
                500, "auth_store_unreadable", "The authentication store is unreadable."
            ) from exc
        if not isinstance(data, dict):
            raise AuthError(
                500, "auth_store_invalid", "The authentication store is invalid."
            )
        if data.get("schema_version") != AUTH_SCHEMA:
            raise AuthError(
                500,
                "auth_store_version",
                "Unsupported authentication store schema version.",
            )
        for field in ("workspaces", "keys", "runs"):
            if not isinstance(data.get(field), dict):
                data[field] = {}
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
        _atomic_write(self.store_path, payload + "\n")

    def _audit(self, event: str, **fields: Any) -> Dict[str, Any]:
        record = {
            "schema_version": AUDIT_SCHEMA,
            "event": event,
            "timestamp": utc_now(),
        }
        record.update(fields)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.audit_path.parent, 0o700)
        except OSError:
            pass
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(self.audit_path, 0o600)
        except OSError:
            pass
        return record

    def load_audit(self) -> List[Dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    # ------------------------------------------------------------ public view
    @staticmethod
    def _public_workspace(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record.get("id", ""),
            "name": record.get("name", ""),
            "status": record.get("status", "active"),
            "created_at": record.get("created_at", ""),
        }

    @staticmethod
    def _public_key(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record.get("id", ""),
            "workspace_id": record.get("workspace_id", ""),
            "prefix": record.get("prefix", ""),
            "scopes": list(record.get("scopes", [])),
            "label": record.get("label", ""),
            "created_at": record.get("created_at", ""),
            "expires_at": record.get("expires_at"),
            "revoked_at": record.get("revoked_at"),
            "revoked_reason": record.get("revoked_reason", ""),
            "rotated_from": record.get("rotated_from"),
        }

    def _workspace(self, data: Dict[str, Any], workspace_id: str) -> Dict[str, Any]:
        workspace = data["workspaces"].get(str(workspace_id or ""))
        if not isinstance(workspace, dict):
            raise AuthError(404, "workspace_not_found", "Workspace not found.")
        return workspace

    def _key(self, data: Dict[str, Any], key_id_or_prefix: str) -> Dict[str, Any]:
        value = str(key_id_or_prefix or "").strip()
        if not value:
            raise AuthError(404, "key_not_found", "API key not found.")
        record = data["keys"].get(value)
        if isinstance(record, dict):
            return record
        for candidate in data["keys"].values():
            if isinstance(candidate, dict) and candidate.get("id") == value:
                return candidate
        raise AuthError(404, "key_not_found", "API key not found.")

    # ------------------------------------------------------------- workspaces
    def create_workspace(
        self, name: str = "", workspace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        data = self._load()
        identifier = str(workspace_id or new_id("ws")).strip()
        if not identifier:
            raise ValueError("A workspace id is required.")
        if identifier in data["workspaces"]:
            raise AuthError(409, "workspace_exists", "Workspace already exists.")
        record = {
            "id": identifier,
            "name": str(name or identifier),
            "status": "active",
            "created_at": utc_now(),
        }
        data["workspaces"][identifier] = record
        self._save(data)
        self._audit("workspace_created", workspace_id=identifier, name=record["name"])
        return self._public_workspace(record)

    def get_workspace(self, workspace_id: str) -> Dict[str, Any]:
        return self._public_workspace(self._workspace(self._load(), workspace_id))

    def delete_workspace(self, workspace_id: str) -> bool:
        data = self._load()
        identifier = str(workspace_id or "").strip()
        if identifier not in data.get("workspaces", {}):
            return False
        del data["workspaces"][identifier]
        data["keys"] = {
            prefix: record
            for prefix, record in data.get("keys", {}).items()
            if str(record.get("workspace_id", "")) != identifier
        }
        data["runs"] = {
            run_id: owner
            for run_id, owner in data.get("runs", {}).items()
            if str(owner) != identifier
        }
        self._save(data)
        self._audit("workspace_deleted", workspace_id=identifier)
        return True

    def list_workspaces(self) -> List[Dict[str, Any]]:
        data = self._load()
        return [
            self._public_workspace(record)
            for record in sorted(
                data["workspaces"].values(), key=lambda item: str(item.get("id", ""))
            )
            if isinstance(record, dict)
        ]

    def resolve_workspace(
        self, principal: Union[AuthContext, str], requested_workspace_id: str = ""
    ) -> str:
        """Resolve exactly one workspace for a cloud request.

        When the request body, path, or query names a workspace, it must be the
        workspace already bound to the authenticated key.  A mismatch is
        reported as ``workspace_not_found`` without echoing either tenant id.
        """

        if isinstance(principal, AuthContext):
            workspace_id = principal.workspace_id
            if requested_workspace_id:
                principal.require_workspace(requested_workspace_id)
        else:
            workspace_id = str(principal or "").strip()
            if not workspace_id:
                raise AuthError(401, "missing_workspace", "A workspace is required.")
            if requested_workspace_id and str(requested_workspace_id) != workspace_id:
                raise AuthError(404, "workspace_not_found", "Workspace not found.")
        workspace = self._workspace(self._load(), workspace_id)
        if workspace.get("status") != "active":
            raise AuthError(403, "workspace_inactive", "The workspace is not active.")
        return workspace_id

    # --------------------------------------------------------------- api keys
    def _new_prefix(self, data: Dict[str, Any]) -> str:
        while True:
            prefix = "{}{}".format(KEY_PREFIX, secrets.token_hex(6))
            if prefix not in data["keys"]:
                return prefix

    def _make_key_record(
        self,
        data: Dict[str, Any],
        workspace_id: str,
        scopes: Union[str, Iterable[str], None],
        label: str = "",
        expires_at: Any = None,
        rotated_from: Optional[str] = None,
    ) -> tuple:
        normalized_scopes = normalize_scopes(scopes)
        expiry = normalize_expiry(expires_at)
        prefix = self._new_prefix(data)
        salt = secrets.token_hex(16)
        secret = "{}.{}".format(prefix, secrets.token_urlsafe(32))
        key_id = new_id("key")
        record = {
            "id": key_id,
            "workspace_id": str(workspace_id),
            "prefix": prefix,
            "salt": salt,
            "hash_algorithm": "sha256",
            "key_hash": _hash_token(secret, salt),
            "scopes": normalized_scopes,
            "label": str(label or ""),
            "created_at": utc_now(),
            "expires_at": expiry,
            "revoked_at": None,
            "revoked_reason": "",
            "rotated_from": rotated_from,
        }
        data["keys"][prefix] = record
        return record, secret

    def _audit_created(self, record: Dict[str, Any]) -> None:
        self._audit(
            "api_key_created",
            workspace_id=record.get("workspace_id", ""),
            key_id=record.get("id", ""),
            prefix=record.get("prefix", ""),
            scopes=list(record.get("scopes", [])),
            label=record.get("label", ""),
            expires_at=record.get("expires_at"),
            rotated_from=record.get("rotated_from"),
        )

    def create_key(
        self,
        workspace_id: str,
        scopes: Union[str, Iterable[str], None] = (SCOPE_RUN,),
        label: str = "",
        expires_at: Any = None,
        rotated_from: Optional[str] = None,
    ) -> Dict[str, Any]:
        data = self._load()
        self._workspace(data, workspace_id)
        record, secret = self._make_key_record(
            data,
            workspace_id,
            scopes,
            label=label,
            expires_at=expires_at,
            rotated_from=rotated_from,
        )
        self._save(data)
        self._audit_created(record)
        issued = self._public_key(record)
        # The secret is returned exactly once and is never persisted.
        issued["secret"] = secret
        return issued

    def list_keys(self, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._load()
        if workspace_id is not None:
            self._workspace(data, workspace_id)
        records = [
            self._public_key(record)
            for record in data["keys"].values()
            if isinstance(record, dict)
            and (workspace_id is None or record.get("workspace_id") == workspace_id)
        ]
        return sorted(records, key=lambda item: str(item.get("id", "")))

    def revoke_key(self, key_id_or_prefix: str, reason: str = "") -> Dict[str, Any]:
        data = self._load()
        record = self._key(data, key_id_or_prefix)
        if not record.get("revoked_at"):
            record["revoked_at"] = utc_now()
            record["revoked_reason"] = str(reason or "")
            self._save(data)
            self._audit(
                "api_key_revoked",
                workspace_id=record.get("workspace_id", ""),
                key_id=record.get("id", ""),
                prefix=record.get("prefix", ""),
                reason=record["revoked_reason"],
            )
        return self._public_key(record)

    def rotate_key(
        self,
        key_id_or_prefix: str,
        scopes: Union[str, Iterable[str], None] = None,
        label: Any = _UNSET,
        expires_at: Any = _UNSET,
    ) -> Dict[str, Any]:
        data = self._load()
        old = self._key(data, key_id_or_prefix)
        if old.get("revoked_at"):
            raise AuthError(409, "key_revoked", "The API key is already revoked.")
        workspace_id = str(old.get("workspace_id", ""))
        new_scopes = old.get("scopes", []) if scopes is None else scopes
        new_label = old.get("label", "") if label is _UNSET else label
        new_expiry = old.get("expires_at") if expires_at is _UNSET else expires_at
        # Rotation is one store transaction: the replacement and the revocation
        # of the old key land together, so a crash cannot leave both usable.
        record, secret = self._make_key_record(
            data,
            workspace_id,
            new_scopes,
            label=new_label or "",
            expires_at=new_expiry,
            rotated_from=str(old.get("id", "")),
        )
        old["revoked_at"] = utc_now()
        old["revoked_reason"] = "rotated"
        self._save(data)
        self._audit_created(record)
        self._audit(
            "api_key_revoked",
            workspace_id=workspace_id,
            key_id=old.get("id", ""),
            prefix=old.get("prefix", ""),
            reason=old["revoked_reason"],
        )
        self._audit(
            "api_key_rotated",
            workspace_id=workspace_id,
            old_key_id=old.get("id", ""),
            old_prefix=old.get("prefix", ""),
            new_key_id=record.get("id", ""),
            new_prefix=record.get("prefix", ""),
        )
        issued = self._public_key(record)
        issued["secret"] = secret
        return issued

    def authenticate(
        self, token: Optional[str], required_scope: Optional[str] = None
    ) -> AuthContext:
        token = str(token or "").strip()
        if not token:
            raise AuthError(401, "missing_api_key", "An API key is required.")
        prefix = _parse_prefix(token)
        data = self._load()
        record = data["keys"].get(prefix)
        if not isinstance(record, dict):
            raise AuthError(401, "invalid_api_key", "The API key is invalid.")
        expected = str(record.get("key_hash", ""))
        actual = _hash_token(token, str(record.get("salt", "")))
        if not expected or not hmac.compare_digest(expected, actual):
            raise AuthError(401, "invalid_api_key", "The API key is invalid.")
        if record.get("revoked_at"):
            raise AuthError(401, "revoked_api_key", "The API key has been revoked.")
        expires_at = record.get("expires_at")
        if expires_at:
            try:
                expired = parse_timestamp(expires_at) <= parse_timestamp(utc_now())
            except (TypeError, ValueError):
                expired = True
            if expired:
                raise AuthError(401, "expired_api_key", "The API key has expired.")
        workspace = data["workspaces"].get(str(record.get("workspace_id", "")))
        if not isinstance(workspace, dict):
            raise AuthError(401, "invalid_api_key", "The API key is invalid.")
        if workspace.get("status") != "active":
            raise AuthError(403, "workspace_inactive", "The workspace is not active.")
        context = AuthContext(
            workspace_id=str(workspace.get("id", "")),
            key_id=str(record.get("id", "")),
            prefix=str(record.get("prefix", "")),
            scopes=tuple(record.get("scopes", [])),
            authenticated_at=utc_now(),
        )
        if required_scope:
            context.require(required_scope)
        return context

    # ---------------------------------------------------------- run ownership
    def claim_run(self, workspace_id: str, run_id: str) -> Dict[str, Any]:
        data = self._load()
        self._workspace(data, workspace_id)
        identifier = str(run_id or "").strip()
        if not identifier:
            raise ValueError("A run id is required.")
        existing = data["runs"].get(identifier)
        if isinstance(existing, dict):
            if existing.get("workspace_id") != str(workspace_id):
                raise AuthError(
                    409, "run_already_claimed", "This run id is already claimed."
                )
            return dict(existing)
        record = {
            "run_id": identifier,
            "workspace_id": str(workspace_id),
            "claimed_at": utc_now(),
        }
        data["runs"][identifier] = record
        self._save(data)
        self._audit(
            "run_claimed", workspace_id=str(workspace_id), run_id=identifier
        )
        return dict(record)

    def authorize_run(
        self,
        principal: Union[AuthContext, str],
        run_id: str,
        required_scope: Optional[str] = SCOPE_READ,
    ) -> Dict[str, Any]:
        if isinstance(principal, AuthContext):
            workspace_id = principal.workspace_id
            if required_scope:
                principal.require(required_scope)
        else:
            workspace_id = str(principal or "").strip()
            if not workspace_id:
                raise AuthError(401, "missing_workspace", "A workspace is required.")
        data = self._load()
        record = data["runs"].get(str(run_id or "").strip())
        if not isinstance(record, dict) or record.get("workspace_id") != workspace_id:
            # A guessed id and a real id owned by another workspace are
            # indistinguishable so the response cannot confirm existence.
            raise AuthError(404, "run_not_found", "Run not found.")
        return dict(record)

    def workspace_for_run(self, run_id: str) -> Optional[str]:
        record = self._load()["runs"].get(str(run_id or "").strip())
        if not isinstance(record, dict):
            return None
        return str(record.get("workspace_id", "")) or None


# Readable aliases for callers that name the resource rather than the store.
AuthStore.create_api_key = AuthStore.create_key
AuthStore.issue_key = AuthStore.create_key
AuthStore.list_api_keys = AuthStore.list_keys
AuthStore.revoke_api_key = AuthStore.revoke_key
AuthStore.rotate_api_key = AuthStore.rotate_key
AuthStore.authenticate_token = AuthStore.authenticate
AuthStore.register_run = AuthStore.claim_run
AuthStore.authorize_workspace = AuthStore.resolve_workspace

ApiKeyStore = AuthStore
WorkspaceAuthStore = AuthStore
WorkspaceStore = AuthStore
