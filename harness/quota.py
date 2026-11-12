"""Monthly, per-workspace quota enforcement for the cloud boundary.

Quotas are deliberately per action, not per run submission.  A request that
does not fit inside a hard limit is rejected before its usage is recorded, but
every earlier action remains in the durable usage record.  That keeps partial
work billable and visible in the usage report.

The OSS/local mode remains unlimited: with no plan, or when the caller
explicitly selects the synthetic ``local`` workspace without enforcement, calls
return an allowed decision without creating quota state.  Supplying a
:class:`QuotaPlan` is the cloud opt-in.

State lives under ``.harness/quota/usage.json`` and is guarded by both a
process-local lock and an advisory file lock, so parallel workers cannot
overspend a hard limit.  Soft-limit warnings are recorded once per resource per
billing period per workspace.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from .determinism import format_timestamp, parse_timestamp
from .errors import QuotaError
from .models import utc_now


SCHEMA_VERSION = "quality-harness.quota/v1"
PLAN_SCHEMA_VERSION = "quality-harness.quota-plan/v1"
EVENT_SCHEMA_VERSION = "quality-harness.quota.events/v1"

DEFAULT_STORE = Path(".harness") / "quota" / "usage.json"
DEFAULT_EVENTS = Path(".harness") / "quota" / "events.jsonl"
DEFAULT_LOCK = Path(".harness") / "quota" / "quota.lock"
DEFAULT_WORKSPACE = "local"

RESOURCE_RUNS = "runs"
RESOURCE_CHECKS = "checks"
RESOURCE_BROWSER_MINUTES = "browser_minutes"
RESOURCE_EVIDENCE_BYTES = "evidence_bytes"
RESOURCES: Tuple[str, ...] = (
    RESOURCE_RUNS,
    RESOURCE_CHECKS,
    RESOURCE_BROWSER_MINUTES,
    RESOURCE_EVIDENCE_BYTES,
)
RESOURCE_UNITS: Dict[str, str] = {
    RESOURCE_RUNS: "run",
    RESOURCE_CHECKS: "check",
    RESOURCE_BROWSER_MINUTES: "browser_minute",
    RESOURCE_EVIDENCE_BYTES: "byte",
}
RESOURCE_ALIASES: Dict[str, str] = {
    "run": RESOURCE_RUNS,
    "runs": RESOURCE_RUNS,
    "check": RESOURCE_CHECKS,
    "checks": RESOURCE_CHECKS,
    "browser_minute": RESOURCE_BROWSER_MINUTES,
    "browser_minutes": RESOURCE_BROWSER_MINUTES,
    "browser-minute": RESOURCE_BROWSER_MINUTES,
    "browser-minutes": RESOURCE_BROWSER_MINUTES,
    "byte": RESOURCE_EVIDENCE_BYTES,
    "bytes": RESOURCE_EVIDENCE_BYTES,
    "evidence_byte": RESOURCE_EVIDENCE_BYTES,
    "evidence_bytes": RESOURCE_EVIDENCE_BYTES,
    "evidence-byte": RESOURCE_EVIDENCE_BYTES,
    "evidence-bytes": RESOURCE_EVIDENCE_BYTES,
}

EVENT_RESOURCES: Dict[str, str] = {
    "run_started": RESOURCE_RUNS,
    "check_executed": RESOURCE_CHECKS,
    "browser_minute_consumed": RESOURCE_BROWSER_MINUTES,
    "evidence_byte_stored": RESOURCE_EVIDENCE_BYTES,
}

EVENT_SOFT_LIMIT = "quota_soft_limit"
EVENT_HARD_LIMIT = "quota_hard_limit"
EVENT_TYPES = (EVENT_SOFT_LIMIT, EVENT_HARD_LIMIT)

_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()
_WORKSPACE_UNSET = object()


def normalize_resource(resource: str) -> str:
    value = str(resource or "").strip().lower()
    try:
        return RESOURCE_ALIASES[value]
    except KeyError:
        raise ValueError(
            "Unknown quota resource '{}'. Allowed resources: {}.".format(
                resource, ", ".join(RESOURCES)
            )
        )


def quota_resource_for_event(event_type: str) -> Optional[str]:
    return EVENT_RESOURCES.get(str(event_type or "").strip())


def workspace_from_context(context: Any) -> str:
    """Resolve a tenant id from an authenticated context, never a body value."""

    workspace = getattr(context, "workspace_id", None)
    if not workspace:
        raise QuotaError(
            "An authenticated workspace context is required before quota lookup."
        )
    return str(workspace)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_quantity(value: Any, name: str = "quantity") -> Union[int, float]:
    if not _is_number(value):
        raise ValueError("{} must be a number.".format(name))
    if not math.isfinite(float(value)):
        raise ValueError("{} must be finite.".format(name))
    if float(value) < 0:
        raise ValueError("{} must be greater than or equal to zero.".format(name))
    return value


def _validate_limit(value: Any, name: str) -> Optional[Union[int, float]]:
    if value is None:
        return None
    if not _is_number(value):
        raise ValueError("{} must be a number or null.".format(name))
    if not math.isfinite(float(value)):
        raise ValueError("{} must be finite.".format(name))
    if float(value) < 0:
        raise ValueError("{} must be greater than or equal to zero.".format(name))
    return value


_QUANTITY_EPSILON = 1e-9


def _clean_quantity(value: Any) -> Any:
    if not isinstance(value, float):
        return value
    cleaned = round(value, 9)
    return cleaned


def _format_quantity(value: Any) -> str:
    if value is None:
        return "unlimited"
    if _is_number(value) and float(value).is_integer():
        return str(int(value))
    return str(value)


def _resource_header(resource: str) -> str:
    return "-".join(piece.capitalize() for piece in resource.split("_"))


@dataclass(frozen=True)
class QuotaLimit:
    """Soft and hard monthly allowances for one resource."""

    soft: Optional[Union[int, float]] = None
    hard: Optional[Union[int, float]] = None

    def __post_init__(self) -> None:
        soft = _validate_limit(self.soft, "soft")
        hard = _validate_limit(self.hard, "hard")
        if soft is not None and hard is not None and float(soft) > float(hard):
            raise ValueError("soft limit cannot exceed hard limit")
        object.__setattr__(self, "soft", soft)
        object.__setattr__(self, "hard", hard)

    @property
    def unlimited(self) -> bool:
        return self.soft is None and self.hard is None

    def to_dict(self) -> Dict[str, Any]:
        return {"soft": self.soft, "hard": self.hard}


def coerce_limit(value: Any) -> QuotaLimit:
    if isinstance(value, QuotaLimit):
        return value
    if value is None:
        return QuotaLimit()
    if isinstance(value, Mapping):
        soft = value.get("soft", value.get("soft_limit"))
        hard = value.get("hard", value.get("hard_limit", value.get("limit")))
        return QuotaLimit(soft=soft, hard=hard)
    return QuotaLimit(hard=_validate_limit(value, "limit"))


@dataclass(frozen=True)
class QuotaPlan:
    """A monthly allowance plan for one workspace tier."""

    id: str = "default"
    version: str = "1.0"
    period: str = "monthly"
    limits: Mapping[str, QuotaLimit] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if str(self.period) != "monthly":
            raise ValueError("Only monthly quota plans are supported.")
        normalized: Dict[str, QuotaLimit] = {}
        for key, value in dict(self.limits or {}).items():
            resource = normalize_resource(str(key))
            normalized[resource] = coerce_limit(value)
        object.__setattr__(self, "id", str(self.id or "default"))
        object.__setattr__(self, "version", str(self.version or "1.0"))
        object.__setattr__(self, "limits", normalized)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QuotaPlan":
        if not isinstance(payload, Mapping):
            raise ValueError("Quota plan must be a JSON object.")
        values = payload.get("limits")
        if values is None:
            values = payload.get("allowances")
        if values is None:
            # Also accept a flat {"checks": 10, "runs": {"hard": 2}} shape.
            flat = {
                key: value
                for key, value in payload.items()
                if str(key).strip().lower() in RESOURCE_ALIASES
            }
            values = flat
        if not isinstance(values, Mapping):
            raise ValueError("Quota plan limits must be a JSON object.")
        return cls(
            id=str(payload.get("id", "default")),
            version=str(payload.get("version", "1.0")),
            period=str(payload.get("period", "monthly")),
            limits={
                str(key): coerce_limit(value) for key, value in values.items()
            },
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> "QuotaPlan":
        value = Path(path).expanduser()
        try:
            payload = json.loads(value.read_text(encoding="utf-8"))
        except OSError as exc:
            raise QuotaError("Could not read quota plan {}: {}".format(value, exc))
        except ValueError as exc:
            raise QuotaError("Quota plan {} is not valid JSON: {}".format(value, exc))
        if not isinstance(payload, Mapping):
            raise QuotaError("Quota plan {} must contain a JSON object.".format(value))
        try:
            return cls.from_dict(payload)
        except ValueError as exc:
            raise QuotaError("Quota plan {} is invalid: {}".format(value, exc))

    def limit(self, resource: str) -> QuotaLimit:
        return self.limits.get(normalize_resource(resource), QuotaLimit())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "id": self.id,
            "version": self.version,
            "period": self.period,
            "limits": {
                resource: limit.to_dict()
                for resource, limit in sorted(self.limits.items())
            },
        }


def coerce_plan(value: Any) -> Optional[QuotaPlan]:
    if value is None:
        return None
    if isinstance(value, QuotaPlan):
        return value
    if isinstance(value, (str, Path)):
        return QuotaPlan.load(value)
    if isinstance(value, Mapping):
        return QuotaPlan.from_dict(value)
    raise ValueError("Quota plan must be a QuotaPlan, mapping, path, or null.")


def quota_period(now: Any = None) -> Dict[str, Any]:
    """Return the UTC monthly period containing ``now``."""

    moment = parse_timestamp(now if now is not None else utc_now())
    moment = moment.astimezone(timezone.utc)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return {
        "key": start.strftime("%Y-%m"),
        "start": format_timestamp(start),
        "end": format_timestamp(end),
        "reset_at": format_timestamp(end),
    }


def _headers(
    resource: str,
    *,
    used: Any,
    limit: Any,
    remaining: Any,
    soft_limit: Any,
    soft_remaining: Any,
    period_start: str,
    period_end: str,
    enforced: bool,
    allowed: bool,
    retry_after: Optional[int] = None,
) -> Dict[str, str]:
    suffix = _resource_header(resource)
    headers = {
        "X-QH-Quota-Enforced": "true" if enforced else "false",
        "X-QH-Quota-Allowed": "true" if allowed else "false",
        "X-QH-Quota-Resource": resource or "",
        "X-QH-Quota-Used": _format_quantity(used),
        "X-QH-Quota-Limit": _format_quantity(limit),
        "X-QH-Quota-Remaining": _format_quantity(remaining),
        "X-QH-Quota-Soft-Limit": _format_quantity(soft_limit),
        "X-QH-Quota-Soft-Remaining": _format_quantity(soft_remaining),
        "X-QH-Quota-Period-Start": period_start,
        "X-QH-Quota-Period-End": period_end,
        "X-QH-Quota-Reset": period_end,
        # Generic aliases keep HTTP clients that only know the common names
        # working without special-casing the per-resource headers.
        "X-Quota-Resource": resource or "",
        "X-Quota-Used": _format_quantity(used),
        "X-Quota-Limit": _format_quantity(limit),
        "X-Quota-Remaining": _format_quantity(remaining),
        "X-Quota-Reset": period_end,
    }
    if resource:
        headers["X-QH-Quota-Used-{}".format(suffix)] = _format_quantity(used)
        headers["X-QH-Quota-Limit-{}".format(suffix)] = _format_quantity(limit)
        headers["X-QH-Quota-Remaining-{}".format(suffix)] = _format_quantity(
            remaining
        )
        headers["X-Quota-Used-{}".format(suffix)] = _format_quantity(used)
        headers["X-Quota-Limit-{}".format(suffix)] = _format_quantity(limit)
        headers["X-Quota-Remaining-{}".format(suffix)] = _format_quantity(remaining)
    if retry_after is not None:
        headers["Retry-After"] = str(max(0, int(retry_after)))
    return headers


@dataclass
class QuotaDecision:
    allowed: bool
    enforced: bool
    resource: str
    requested: Union[int, float]
    used: Union[int, float]
    limit: Optional[Union[int, float]] = None
    soft_limit: Optional[Union[int, float]] = None
    remaining: Optional[Union[int, float]] = None
    soft_remaining: Optional[Union[int, float]] = None
    period_start: str = ""
    period_end: str = ""
    reset_at: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    warning: Optional[Dict[str, Any]] = None

    @property
    def status_code(self) -> int:
        return 200 if self.allowed else 429

    @property
    def status(self) -> int:
        return self.status_code

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "allowed" if self.allowed else "blocked",
            "status_code": self.status_code,
            "http_status": self.status_code,
            "allowed": self.allowed,
            "enforced": self.enforced,
            "resource": self.resource,
            "requested": self.requested,
            "used": self.used,
            "limit": self.limit,
            "soft_limit": self.soft_limit,
            "remaining": self.remaining,
            "soft_remaining": self.soft_remaining,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "reset_at": self.reset_at,
            "reset": self.reset_at,
            "headers": dict(self.headers),
        }
        if self.warning is not None:
            payload["warning"] = dict(self.warning)
        return payload


class QuotaExceeded(QuotaError):
    """A hard limit blocked the requested action."""

    status_code = 429
    exit_code = 1
    code = "quota_exceeded"

    def __init__(self, decision: QuotaDecision) -> None:
        self.decision = decision
        self.status = self.status_code
        self.resource = decision.resource
        self.limit = decision.limit
        self.soft_limit = decision.soft_limit
        self.used = decision.used
        self.requested = decision.requested
        self.remaining = decision.remaining
        self.reset_at = decision.reset_at
        self.period_start = decision.period_start
        self.period_end = decision.period_end
        self.headers = dict(decision.headers)
        self.details = {
            "resource": self.resource,
            "limit": self.limit,
            "soft_limit": self.soft_limit,
            "used": self.used,
            "requested": self.requested,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "period_start": self.period_start,
            "period_end": self.period_end,
        }
        super().__init__(
            "Monthly quota exceeded for {}: limit {}, used {}, requested {}; "
            "resets at {}.".format(
                self.resource,
                _format_quantity(self.limit),
                _format_quantity(self.used),
                _format_quantity(self.requested),
                self.reset_at,
            )
        )

    def to_dict(self) -> Dict[str, Any]:
        error = {
            "code": self.code,
            "message": str(self),
            **dict(self.details),
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status_code,
            "status_code": self.status_code,
            "http_status": self.status_code,
            "allowed": False,
            "enforced": self.decision.enforced,
            "resource": self.resource,
            "limit": self.limit,
            "soft_limit": self.soft_limit,
            "used": self.used,
            "requested": self.requested,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "reset": self.reset_at,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "error": error,
            "headers": self.headers,
        }

    def response(self) -> Dict[str, Any]:
        return self.to_dict()


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCK_REGISTRY_GUARD:
        return _LOCK_REGISTRY.setdefault(key, threading.Lock())


@contextlib.contextmanager
def _locked(lock_path: Path) -> Iterator[None]:
    lock = _thread_lock(lock_path)
    with lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(lock_path.parent, 0o700)
        except OSError:
            pass
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                os.chmod(lock_path, 0o600)
            except OSError:
                pass
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            try:
                yield
            finally:
                try:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor, temporary = tempfile.mkstemp(
        prefix=".{}".format(path.name), dir=str(path.parent)
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


class QuotaStore:
    """Durable monthly usage with atomic, process-safe quota consumption."""

    def __init__(
        self,
        root: Path,
        plan: Any = None,
        *,
        workspace: Any = _WORKSPACE_UNSET,
        enforce: Optional[bool] = None,
        store_path: Optional[Path] = None,
        events_path: Optional[Path] = None,
        lock_path: Optional[Path] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.plan = coerce_plan(plan)
        workspace_was_explicit = workspace is not _WORKSPACE_UNSET
        self.workspace = (
            DEFAULT_WORKSPACE
            if not workspace_was_explicit
            else (str(workspace or DEFAULT_WORKSPACE).strip() or DEFAULT_WORKSPACE)
        )
        if self.plan is None:
            self.enforced = False
        elif enforce is not None:
            self.enforced = bool(enforce)
        elif workspace_was_explicit and self.workspace == DEFAULT_WORKSPACE:
            # The CLI and OSS callers can explicitly select the synthetic local
            # workspace without an enabled plan.  Supplying a plan without that
            # explicit selection is an opt-in to enforcement, even though the
            # default workspace label is still ``local``.
            self.enforced = False
        else:
            self.enforced = True
        self.store_path = self._resolve_path(store_path or DEFAULT_STORE)
        self.events_path = self._resolve_path(events_path or DEFAULT_EVENTS)
        self.lock_path = self._resolve_path(lock_path or DEFAULT_LOCK)
        self.event_errors: List[str] = []

    def _resolve_path(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path

    @classmethod
    def from_auth_context(
        cls,
        root: Path,
        context: Any,
        plan: Any = None,
        **kwargs: Any,
    ) -> "QuotaStore":
        """Build a store bound to the workspace in an ``AuthContext``.

        Cloud callers must resolve the tenant from the scoped token first.  This
        helper deliberately ignores any caller-supplied ``workspace`` keyword.
        """

        kwargs.pop("workspace", None)
        kwargs.setdefault("enforce", True)
        return cls(
            root,
            plan,
            workspace=workspace_from_context(context),
            **kwargs
        )

    @property
    def unlimited(self) -> bool:
        return not self.enforced

    # --------------------------------------------------------------- storage
    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "workspaces": {},
        }

    def _load(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return self._empty()
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise QuotaError(
                "The quota store is unreadable: {}".format(exc)
            ) from exc
        if not isinstance(data, dict):
            raise QuotaError("The quota store must contain a JSON object.")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise QuotaError("Unsupported quota store schema version.")
        if not isinstance(data.get("workspaces"), dict):
            data["workspaces"] = {}
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        _atomic_write(
            self.store_path,
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    def _append_event(
        self,
        event_type: str,
        *,
        resource: str,
        decision: QuotaDecision,
        timestamp: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        safe_metadata = {}
        for key in ("run", "event_type", "source", "plan_id"):
            value = (metadata or {}).get(key)
            if value not in (None, ""):
                safe_metadata[key] = str(value)[:200]
        record = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_type": event_type,
            "workspace": self.workspace,
            "resource": resource,
            "timestamp": timestamp,
            "used": decision.used,
            "requested": decision.requested,
            "limit": decision.limit,
            "soft_limit": decision.soft_limit,
            "remaining": decision.remaining,
            "reset_at": decision.reset_at,
            "metadata": safe_metadata,
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.events_path.parent, 0o700)
        except OSError:
            pass
        try:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(self.events_path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            # The usage record is the source of truth. A failed warning/audit
            # write must not roll back the consumed quantity or the hard-limit
            # response.
            self.event_errors.append(str(exc))

    def events(self) -> List[Dict[str, Any]]:
        if not self.events_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        except OSError:
            return records
        return records

    def _state_for(
        self, data: Dict[str, Any], period: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], bool]:
        state = data["workspaces"].get(self.workspace)
        rolled = not isinstance(state, dict) or state.get("period") != period["key"]
        if rolled:
            state = {
                "period": period["key"],
                "period_start": period["start"],
                "period_end": period["end"],
                "plan_id": self.plan.id if self.plan is not None else "",
                "plan_version": self.plan.version if self.plan is not None else "",
                "usage": {resource: 0 for resource in RESOURCES},
                "soft_warned": {},
                "updated_at": "",
            }
        else:
            if not isinstance(state.get("usage"), dict):
                state["usage"] = {}
            for resource in RESOURCES:
                state["usage"].setdefault(resource, 0)
            if not isinstance(state.get("soft_warned"), dict):
                state["soft_warned"] = {}
            if self.plan is not None:
                state["plan_id"] = self.plan.id
                state["plan_version"] = self.plan.version
        data["workspaces"][self.workspace] = state
        return state, rolled

    def _decision(
        self,
        *,
        plan: QuotaLimit,
        resource: str,
        requested: Union[int, float],
        used: Union[int, float],
        period: Dict[str, Any],
        allowed: bool,
        warning: Optional[Dict[str, Any]] = None,
        now: Any = None,
    ) -> QuotaDecision:
        hard = plan.hard
        soft = plan.soft
        remaining = (
            None
            if hard is None
            else _clean_quantity(max(float(hard) - float(used), 0))
        )
        if remaining is not None and float(remaining).is_integer():
            remaining = int(remaining)
        soft_remaining = (
            None
            if soft is None
            else _clean_quantity(max(float(soft) - float(used), 0))
        )
        if soft_remaining is not None and float(soft_remaining).is_integer():
            soft_remaining = int(soft_remaining)
        retry_after = None
        if not allowed:
            try:
                moment = parse_timestamp(now if now is not None else utc_now())
                seconds = (
                    parse_timestamp(period["reset_at"]) - moment
                ).total_seconds()
                retry_after = max(0, int(math.ceil(seconds)))
            except (TypeError, ValueError):
                retry_after = None
        headers = _headers(
            resource,
            used=used,
            limit=hard,
            remaining=remaining,
            soft_limit=soft,
            soft_remaining=soft_remaining,
            period_start=period["start"],
            period_end=period["end"],
            enforced=self.enforced,
            allowed=allowed,
            retry_after=retry_after,
        )
        return QuotaDecision(
            allowed=allowed,
            enforced=self.enforced,
            resource=resource,
            requested=requested,
            used=used,
            limit=hard,
            soft_limit=soft,
            remaining=remaining,
            soft_remaining=soft_remaining,
            period_start=period["start"],
            period_end=period["end"],
            reset_at=period["reset_at"],
            headers=headers,
            warning=warning,
        )

    def _unlimited_decision(
        self, resource: str, requested: Union[int, float], now: Any = None
    ) -> QuotaDecision:
        period = quota_period(now)
        headers = _headers(
            resource,
            used=0,
            limit=None,
            remaining=None,
            soft_limit=None,
            soft_remaining=None,
            period_start=period["start"],
            period_end=period["end"],
            enforced=False,
            allowed=True,
        )
        return QuotaDecision(
            allowed=True,
            enforced=False,
            resource=resource,
            requested=requested,
            used=0,
            limit=None,
            soft_limit=None,
            remaining=None,
            soft_remaining=None,
            period_start=period["start"],
            period_end=period["end"],
            reset_at=period["reset_at"],
            headers=headers,
        )

    # --------------------------------------------------------------- consume
    def consume(
        self,
        resource: str,
        quantity: Union[int, float] = 1,
        *,
        run: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        now: Any = None,
    ) -> QuotaDecision:
        """Consume quota for one action or raise :class:`QuotaExceeded`.

        A denied request does not increment usage.  Usage from earlier actions
        remains persisted and is returned by :meth:`snapshot`.
        """

        canonical = normalize_resource(resource)
        amount = _validate_quantity(quantity)
        if float(amount) <= 0:
            raise ValueError("quantity must be greater than zero for quota consumption.")
        if not self.enforced or self.plan is None:
            return self._unlimited_decision(canonical, amount, now=now)

        period = quota_period(now)
        stamp = format_timestamp(now if now is not None else utc_now())
        with _locked(self.lock_path):
            data = self._load()
            state, rolled = self._state_for(data, period)
            used_before = state["usage"].get(canonical, 0)
            plan = self.plan.limit(canonical)
            hard = plan.hard
            if (
                hard is not None
                and float(used_before) + float(amount)
                > float(hard) + _QUANTITY_EPSILON
            ):
                decision = self._decision(
                    plan=plan,
                    resource=canonical,
                    requested=amount,
                    used=used_before,
                    period=period,
                    allowed=False,
                    now=now,
                )
                if rolled:
                    self._save(data)
                self._append_event(
                    EVENT_HARD_LIMIT,
                    resource=canonical,
                    decision=decision,
                    timestamp=stamp,
                    metadata={"run": str(run or "") if run else "", **(metadata or {})},
                )
                raise QuotaExceeded(decision)

            used_after = _clean_quantity(float(used_before) + float(amount))
            if isinstance(used_before, int) and isinstance(amount, int):
                used_after = used_before + amount
            state["usage"][canonical] = used_after
            state["updated_at"] = stamp
            warning: Optional[Dict[str, Any]] = None
            soft = plan.soft
            if (
                soft is not None
                and float(used_after) + _QUANTITY_EPSILON >= float(soft)
                and not bool(state["soft_warned"].get(canonical))
            ):
                state["soft_warned"][canonical] = True
                warning = {
                    "resource": canonical,
                    "soft_limit": soft,
                    "used": used_after,
                    "period_start": period["start"],
                    "period_end": period["end"],
                    "reset_at": period["reset_at"],
                    "workspace": self.workspace,
                }
            self._save(data)
            decision = self._decision(
                plan=plan,
                resource=canonical,
                requested=amount,
                used=used_after,
                period=period,
                allowed=True,
                warning=warning,
                now=now,
            )
            if warning is not None:
                self._append_event(
                    EVENT_SOFT_LIMIT,
                    resource=canonical,
                    decision=decision,
                    timestamp=stamp,
                    metadata={
                        "soft_limit": soft,
                        "run": str(run or "") if run else "",
                        **(metadata or {}),
                    },
                )
            return decision

    def record(self, resource: str, quantity: Union[int, float] = 1, **kwargs: Any) -> QuotaDecision:
        return self.consume(resource, quantity, **kwargs)

    reserve = record
    enforce = record

    def consume_for(
        self,
        context: Any,
        resource: str,
        quantity: Union[int, float] = 1,
        **kwargs: Any,
    ) -> QuotaDecision:
        """Consume quota after verifying the store belongs to the caller."""

        workspace = workspace_from_context(context)
        if workspace != self.workspace:
            raise QuotaError(
                "The quota store does not belong to the authenticated workspace."
            )
        return self.consume(resource, quantity, **kwargs)

    def snapshot_for(self, context: Any, now: Any = None) -> Dict[str, Any]:
        workspace = workspace_from_context(context)
        if workspace != self.workspace:
            raise QuotaError(
                "The quota store does not belong to the authenticated workspace."
            )
        return self.snapshot(now=now)

    def consume_event(
        self,
        event_type: str,
        quantity: Union[int, float] = 1,
        **kwargs: Any,
    ) -> QuotaDecision:
        resource = quota_resource_for_event(event_type)
        if resource is None:
            return self._unlimited_decision("", quantity, now=kwargs.get("now"))
        return self.consume(resource, quantity, **kwargs)

    def consume_metering_event(self, event: Mapping[str, Any]) -> QuotaDecision:
        event_type = str(event.get("event_type", ""))
        resource = quota_resource_for_event(event_type)
        if resource is None:
            return self._unlimited_decision("", 0, now=event.get("timestamp"))
        quantity = event.get("quantity", 1)
        return self.consume(
            resource,
            quantity,
            run=str(event.get("run", "")),
            metadata={"event_type": event_type},
            now=event.get("timestamp"),
        )

    def peek(
        self,
        resource: str,
        quantity: Union[int, float] = 1,
        *,
        now: Any = None,
    ) -> QuotaDecision:
        """Return the decision for an action without consuming quota."""

        canonical = normalize_resource(resource)
        amount = _validate_quantity(quantity)
        if float(amount) <= 0:
            raise ValueError("quantity must be greater than zero for a quota decision.")
        if not self.enforced or self.plan is None:
            return self._unlimited_decision(canonical, amount, now=now)
        period = quota_period(now)
        with _locked(self.lock_path):
            data = self._load()
            state, _rolled = self._state_for(data, period)
            used = state["usage"].get(canonical, 0)
            plan = self.plan.limit(canonical)
            hard = plan.hard
            allowed = (
                hard is None
                or float(used) + float(amount)
                <= float(hard) + _QUANTITY_EPSILON
            )
            return self._decision(
                plan=plan,
                resource=canonical,
                requested=amount,
                used=used,
                period=period,
                allowed=allowed,
                now=now,
            )

    check = peek

    def try_consume(
        self,
        resource: str,
        quantity: Union[int, float] = 1,
        **kwargs: Any,
    ) -> QuotaDecision:
        """Consume and return a blocked decision instead of raising."""

        try:
            return self.consume(resource, quantity, **kwargs)
        except QuotaExceeded as exc:
            return exc.decision

    # --------------------------------------------------------------- reporting
    def snapshot(self, now: Any = None) -> Dict[str, Any]:
        period = quota_period(now)
        if not self.enforced or self.plan is None:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "ok",
                "enforced": False,
                "unlimited": True,
                "workspace": self.workspace,
                "plan": None,
                "period": period,
                "limits": {},
                "usage": {resource: 0 for resource in RESOURCES},
                "remaining": {resource: None for resource in RESOURCES},
                "soft_remaining": {resource: None for resource in RESOURCES},
                "soft_warnings": [],
                "headers": _headers(
                    "",
                    used=0,
                    limit=None,
                    remaining=None,
                    soft_limit=None,
                    soft_remaining=None,
                    period_start=period["start"],
                    period_end=period["end"],
                    enforced=False,
                    allowed=True,
                ),
            }
        with _locked(self.lock_path):
            data = self._load()
            state, _rolled = self._state_for(data, period)
            usage = {
                resource: state["usage"].get(resource, 0) for resource in RESOURCES
            }
            limits: Dict[str, Any] = {}
            remaining: Dict[str, Any] = {}
            soft_remaining: Dict[str, Any] = {}
            for resource in RESOURCES:
                plan = self.plan.limit(resource)
                limits[resource] = plan.to_dict()
                hard = plan.hard
                soft = plan.soft
                remaining[resource] = (
                    None
                    if hard is None
                    else _clean_quantity(
                        max(float(hard) - float(usage[resource]), 0)
                    )
                )
                if remaining[resource] is not None and float(
                    remaining[resource]
                ).is_integer():
                    remaining[resource] = int(remaining[resource])
                soft_remaining[resource] = (
                    None
                    if soft is None
                    else _clean_quantity(
                        max(float(soft) - float(usage[resource]), 0)
                    )
                )
                if soft_remaining[resource] is not None and float(
                    soft_remaining[resource]
                ).is_integer():
                    soft_remaining[resource] = int(soft_remaining[resource])
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "ok",
                "enforced": True,
                "unlimited": False,
                "workspace": self.workspace,
                "plan": self.plan.to_dict(),
                "period": period,
                "limits": limits,
                "usage": usage,
                "remaining": remaining,
                "soft_remaining": soft_remaining,
                "soft_warnings": sorted(
                    resource
                    for resource, warned in state.get("soft_warned", {}).items()
                    if warned
                ),
                "updated_at": state.get("updated_at", ""),
                "headers": _headers(
                    "",
                    used=0,
                    limit=None,
                    remaining=None,
                    soft_limit=None,
                    soft_remaining=None,
                    period_start=period["start"],
                    period_end=period["end"],
                    enforced=True,
                    allowed=True,
                ),
            }

    def summary(self, now: Any = None) -> Dict[str, Any]:
        return self.snapshot(now=now)

    def warnings(self, now: Any = None) -> List[str]:
        return list(self.snapshot(now=now).get("soft_warnings", []))

    def usage(self, now: Any = None) -> Dict[str, Any]:
        return dict(self.snapshot(now=now)["usage"])

    def remaining(
        self, resource: Optional[str] = None, now: Any = None
    ) -> Any:
        values = dict(self.snapshot(now=now)["remaining"])
        if resource is None:
            return values
        return values.get(normalize_resource(resource))

    def reset(self, now: Any = None) -> Dict[str, Any]:
        """Clear current-period usage for the configured workspace.

        This is an operator/test helper.  It does not delete the event log, so
        an audit trail of prior warnings and denials remains available.
        """

        if not self.enforced or self.plan is None:
            return self.snapshot(now=now)
        period = quota_period(now)
        with _locked(self.lock_path):
            data = self._load()
            state, _rolled = self._state_for(data, period)
            state["usage"] = {resource: 0 for resource in RESOURCES}
            state["soft_warned"] = {}
            state["updated_at"] = format_timestamp(
                now if now is not None else utc_now()
            )
            self._save(data)
        return self.snapshot(now=now)


# Friendly aliases for cloud adapters that use alternate resource names.
QuotaLimits = QuotaPlan
QuotaManager = QuotaStore
QuotaUsage = QuotaStore
