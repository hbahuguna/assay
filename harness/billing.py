"""Metered subscription billing behind an explicit feature flag.

Billing is disabled by default.  A named workspace must be allow-listed and the
process must run in sandbox mode before any billing provider call is made.  A
billing kill switch (``.harness/billing/KILL`` or the global ``.harness/KILL``)
disables writes without stopping the harness or the runs that already paid
their quota.

Secrets are never accepted as raw values in these APIs.  Callers pass a secret
reference such as ``billing/api_key``; a :class:`SecretManager` resolves it.
The default environment manager reads only the requested name and never dumps
the environment.  Provider calls carry an idempotency key, and transient
failures are retried before the service reports a degraded status.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .models import utc_now
from .errors import (
    BillingDegraded,
    BillingDisabled,
    BillingError,
    BillingTransientError,
)
from .ledger import LedgerStore
from .models import new_id
from .signup import SignupStore


SCHEMA_VERSION = "quality-harness.billing/v1"
DEFAULT_STATE = Path(".harness") / "billing" / "state.json"
DEFAULT_KILL = Path(".harness") / "billing" / "KILL"
GLOBAL_KILL = Path(".harness") / "KILL"
DEFAULT_LOCK = Path(".harness") / "billing" / "billing.lock"

_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


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


@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    key = str(path.resolve())
    with _LOCK_REGISTRY_GUARD:
        lock = _LOCK_REGISTRY.setdefault(key, threading.Lock())
    with lock:
        yield


@dataclass(frozen=True)
class BillingFeatureFlag:
    """Explicit, off-by-default feature flag for billing writes."""

    enabled: bool = False
    sandbox: bool = False
    workspaces: Sequence[str] = field(default_factory=tuple)
    kill_switch_path: Optional[Path] = None

    def is_enabled(self, workspace_id: str) -> bool:
        if not self.enabled or not self.sandbox:
            return False
        workspace_key = _safe_text(workspace_id)
        if not workspace_key:
            return False
        if not self.workspaces:
            return False
        return workspace_key in {str(item).strip() for item in self.workspaces}

    @classmethod
    def from_env(cls) -> "BillingFeatureFlag":
        enabled = os.environ.get("QH_BILLING_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        sandbox = os.environ.get("QH_BILLING_SANDBOX", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        workspaces = [
            item.strip()
            for item in os.environ.get("QH_BILLING_WORKSPACES", "").split(",")
            if item.strip()
        ]
        return cls(enabled=enabled, sandbox=sandbox, workspaces=workspaces)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BillingFeatureFlag":
        if not isinstance(data, Mapping):
            raise BillingError("Billing feature flag must be a mapping.")
        workspaces = data.get("workspaces", data.get("allowed_workspaces", ()))
        if isinstance(workspaces, str):
            workspaces = [item.strip() for item in workspaces.split(",") if item.strip()]
        return cls(
            enabled=bool(data.get("enabled", False)),
            sandbox=bool(data.get("sandbox", False)),
            workspaces=tuple(workspaces or ()),
            kill_switch_path=(
                Path(data["kill_switch_path"])
                if data.get("kill_switch_path")
                else None
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "sandbox": self.sandbox,
            "workspaces": list(self.workspaces),
        }


class SecretManager:
    """Resolve secret references without exposing their values."""

    def get(self, reference: str) -> str:
        raise NotImplementedError

    def has(self, reference: str) -> bool:
        try:
            return bool(self.get(reference))
        except Exception:
            return False


class EnvSecretManager(SecretManager):
    """Read one named environment variable at a time."""

    def get(self, reference: str) -> str:
        key = _safe_text(reference, 200)
        if not key:
            raise BillingError("A secret reference is required.")
        value = os.environ.get(key)
        if not value:
            raise BillingError("Secret not found: {}".format(key))
        return value


class InMemorySecretManager(SecretManager):
    def __init__(self, values: Optional[Mapping[str, str]] = None) -> None:
        self._values = dict(values or {})

    def get(self, reference: str) -> str:
        key = _safe_text(reference, 200)
        if key not in self._values:
            raise BillingError("Secret not found: {}".format(key))
        return str(self._values[key])

    def set(self, reference: str, value: str) -> None:
        self._values[_safe_text(reference, 200)] = str(value)


class FileSecretManager(SecretManager):
    """Read secrets from a mode-600 JSON file keyed by reference."""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path).expanduser().resolve()

    def get(self, reference: str) -> str:
        key = _safe_text(reference, 200)
        if not self.path.exists():
            raise BillingError("Secret store not found: {}".format(self.path))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BillingError("Secret store is unreadable: {}".format(exc))
        if not isinstance(data, Mapping) or key not in data:
            raise BillingError("Secret not found: {}".format(key))
        return str(data[key])


class BillingProvider:
    """Provider boundary.  Implementations must honor idempotency keys."""

    def create_customer(
        self, *, workspace_id: str, name: str, idempotency_key: str, secret: str
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def ensure_subscription(
        self,
        *,
        workspace_id: str,
        customer_id: str,
        plan_id: str,
        idempotency_key: str,
        secret: str,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def cancel_subscription(
        self,
        *,
        workspace_id: str,
        subscription_id: str,
        idempotency_key: str,
        secret: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def reconcile_invoice(
        self,
        *,
        workspace_id: str,
        period: str,
        invoice: Mapping[str, Any],
        idempotency_key: str,
        secret: str,
    ) -> Dict[str, Any]:
        raise NotImplementedError


class SandboxBillingProvider(BillingProvider):
    """Deterministic local provider used for sandbox reconciliation."""

    def __init__(
        self,
        root: Path,
        *,
        fail_reconcile_times: int = 0,
        fail_customer_times: int = 0,
        fail_subscription_times: int = 0,
    ) -> None:
        self.root = Path(root).resolve()
        self.state_path = self.root / ".harness" / "billing" / "sandbox-provider.json"
        self.fail_reconcile_times = int(fail_reconcile_times or 0)
        self.fail_customer_times = int(fail_customer_times or 0)
        self.fail_subscription_times = int(fail_subscription_times or 0)

    def _load(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {
                "customers": {},
                "subscriptions": {},
                "invoices": {},
                "attempts": {},
            }
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("customers", {})
        data.setdefault("subscriptions", {})
        data.setdefault("invoices", {})
        data.setdefault("attempts", {})
        return data

    def _save(self, data: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_path.parent, 0o700)
        _atomic_write(
            self.state_path,
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    def _attempt(self, key: str, limit: int) -> None:
        data = self._load()
        attempts = data["attempts"].get(key, 0) + 1
        data["attempts"][key] = attempts
        self._save(data)
        if attempts <= limit:
            raise BillingTransientError(
                "Sandbox provider transient failure {} for {}".format(attempts, key)
            )

    def create_customer(
        self, *, workspace_id: str, name: str, idempotency_key: str, secret: str
    ) -> Dict[str, Any]:
        data = self._load()
        if idempotency_key in data["customers"]:
            return dict(data["customers"][idempotency_key])
        self._attempt("customer:" + idempotency_key, self.fail_customer_times)
        data = self._load()
        if idempotency_key in data["customers"]:
            return dict(data["customers"][idempotency_key])
        customer = {
            "provider": "sandbox",
            "customer_id": "cus_{}".format(workspace_id),
            "workspace_id": workspace_id,
            "name": _safe_text(name),
            "idempotency_key": idempotency_key,
            "status": "created",
        }
        data["customers"][idempotency_key] = customer
        self._save(data)
        return dict(customer)

    def ensure_subscription(
        self,
        *,
        workspace_id: str,
        customer_id: str,
        plan_id: str,
        idempotency_key: str,
        secret: str,
    ) -> Dict[str, Any]:
        data = self._load()
        if idempotency_key in data["subscriptions"]:
            return dict(data["subscriptions"][idempotency_key])
        self._attempt(
            "subscription:" + idempotency_key, self.fail_subscription_times
        )
        data = self._load()
        if idempotency_key in data["subscriptions"]:
            return dict(data["subscriptions"][idempotency_key])
        subscription = {
            "provider": "sandbox",
            "subscription_id": "sub_{}".format(workspace_id),
            "workspace_id": workspace_id,
            "customer_id": customer_id,
            "plan_id": _safe_text(plan_id),
            "idempotency_key": idempotency_key,
            "status": "active",
        }
        data["subscriptions"][idempotency_key] = subscription
        self._save(data)
        return dict(subscription)

    def cancel_subscription(
        self,
        *,
        workspace_id: str,
        subscription_id: str,
        idempotency_key: str,
        secret: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        data = self._load()
        if idempotency_key in data.setdefault("cancellations", {}):
            return dict(data["cancellations"][idempotency_key])
        result = {
            "provider": "sandbox",
            "workspace_id": workspace_id,
            "subscription_id": subscription_id,
            "idempotency_key": idempotency_key,
            "reason": _safe_text(reason, 500),
            "status": "cancelled",
        }
        data.setdefault("cancellations", {})[idempotency_key] = result
        # Mark the subscription cancelled if it exists.
        for subscription in data.get("subscriptions", {}).values():
            if isinstance(subscription, dict) and subscription.get("subscription_id") == subscription_id:
                subscription["status"] = "cancelled"
        self._save(data)
        return dict(result)

    def reconcile_invoice(
        self,
        *,
        workspace_id: str,
        period: str,
        invoice: Mapping[str, Any],
        idempotency_key: str,
        secret: str,
    ) -> Dict[str, Any]:
        data = self._load()
        if idempotency_key in data["invoices"]:
            return dict(data["invoices"][idempotency_key])
        self._attempt("invoice:" + idempotency_key, self.fail_reconcile_times)
        data = self._load()
        if idempotency_key in data["invoices"]:
            return dict(data["invoices"][idempotency_key])
        ledger_total = float(invoice.get("total", 0))
        provider_total = round(ledger_total, 2)
        matched = abs(provider_total - ledger_total) <= 0.005
        result = {
            "provider": "sandbox",
            "workspace_id": workspace_id,
            "period": period,
            "idempotency_key": idempotency_key,
            "invoice_id": str(invoice.get("invoice_id", "")),
            "ledger_total": ledger_total,
            "provider_total": provider_total,
            "matched": matched,
            "status": "reconciled" if matched else "mismatch",
        }
        data["invoices"][idempotency_key] = result
        self._save(data)
        if not matched:
            raise BillingError(
                "Sandbox invoice mismatch: ledger={}, provider={}".format(
                    ledger_total, provider_total
                )
            )
        return dict(result)


class BillingStore:
    """Feature-gated billing orchestration over ledger invoices."""

    def __init__(
        self,
        root: Path,
        *,
        feature_flag: Any = None,
        secret_manager: Optional[SecretManager] = None,
        secret_ref: str = "billing/api_key",
        provider: Optional[BillingProvider] = None,
        ledger: Optional[LedgerStore] = None,
        signup: Optional[SignupStore] = None,
        kill_switch_path: Optional[Union[str, Path]] = None,
        global_kill_switch_path: Optional[Union[str, Path]] = None,
        state_path: Optional[Union[str, Path]] = None,
        lock_path: Optional[Union[str, Path]] = None,
        max_retries: int = 3,
        retry_delay_seconds: float = 0.0,
    ) -> None:
        self.root = Path(root).resolve()
        self.feature_flag = (
            feature_flag
            if feature_flag is not None
            else BillingFeatureFlag.from_env()
        )
        if isinstance(self.feature_flag, Mapping):
            self.feature_flag = BillingFeatureFlag.from_dict(self.feature_flag)
        self.secret_manager = secret_manager or EnvSecretManager()
        self.secret_ref = _safe_text(secret_ref, 200) or "billing/api_key"
        self.ledger = ledger if ledger is not None else LedgerStore(self.root)
        self.signup = signup if signup is not None else SignupStore(self.root)
        self.provider = provider if provider is not None else SandboxBillingProvider(
            self.root
        )
        self.kill_switch_path = self._resolve(
            kill_switch_path or DEFAULT_KILL
        )
        self.global_kill_switch_path = self._resolve(
            global_kill_switch_path or GLOBAL_KILL
        )
        self.state_path = self._resolve(state_path or DEFAULT_STATE)
        self.lock_path = self._resolve(lock_path or DEFAULT_LOCK)
        self.max_retries = max(1, int(max_retries or 1))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds or 0.0))

    def _resolve(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path

    # --------------------------------------------------------------- state
    def _empty(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "workspaces": {},
            "disabled_events": [],
        }

    def _load(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return self._empty()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BillingError("Billing state is unreadable: {}".format(exc))
        if not isinstance(data, dict):
            raise BillingError("Billing state must contain a JSON object.")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise BillingError("Unsupported billing schema version.")
        data.setdefault("workspaces", {})
        data.setdefault("disabled_events", [])
        if not isinstance(data["workspaces"], dict):
            data["workspaces"] = {}
        if not isinstance(data["disabled_events"], list):
            data["disabled_events"] = []
        return data

    def _save(self, data: Mapping[str, Any]) -> None:
        _atomic_write(
            self.state_path,
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    # --------------------------------------------------------------- flags
    def billing_enabled(self, workspace_id: str) -> bool:
        workspace_key = _safe_text(workspace_id)
        if not workspace_key:
            return False
        if self.kill_switch_active():
            return False
        return self.feature_flag.is_enabled(workspace_key)

    def kill_switch_active(self) -> bool:
        return (
            self.kill_switch_path.exists()
            or self.global_kill_switch_path.exists()
        )

    def disable(self, reason: str = "operator disabled billing writes") -> Dict[str, Any]:
        self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        self.kill_switch_path.write_text(
            json.dumps(
                {
                    "status": "disabled",
                    "reason": _safe_text(reason, 500),
                    "disabled_at": utc_now(),
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(self.kill_switch_path, 0o600)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "disabled",
            "workspace": None,
            "reason": _safe_text(reason, 500),
        }

    def enable_writes(self) -> None:
        try:
            self.kill_switch_path.unlink()
        except FileNotFoundError:
            pass

    # --------------------------------------------------------------- helpers
    def _workspace_state(self, data: Dict[str, Any], workspace_id: str) -> Dict[str, Any]:
        workspaces = data.setdefault("workspaces", {})
        state = workspaces.get(workspace_id)
        if not isinstance(state, dict):
            state = {
                "workspace_id": workspace_id,
                "customer_id": None,
                "subscription_id": None,
                "plan_id": None,
                "invoices": {},
                "attempts": 0,
                "last_status": "not_started",
                "last_error": "",
                "updated_at": "",
            }
            workspaces[workspace_id] = state
        state.setdefault("invoices", {})
        return state

    def _idempotency_key(self, action: str, workspace_id: str, period: str, invoice_id: str = "") -> str:
        return "billing:{}:{}:{}:{}".format(
            action, workspace_id, period, invoice_id or "default"
        )

    def _retry(self, operation, *, idempotency_key: str):
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return operation()
            except BillingTransientError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    if self.retry_delay_seconds:
                        time.sleep(self.retry_delay_seconds)
                    continue
                raise
            except Exception:
                raise
        if last_error is not None:
            raise last_error
        raise BillingError("Billing operation did not run.")

    # --------------------------------------------------------------- status
    def status(self, workspace_id: str) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        with _locked(self.lock_path):
            data = self._load()
        state = data["workspaces"].get(workspace_key, {})
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "workspace_id": workspace_key,
            "billing_enabled": self.billing_enabled(workspace_key),
            "kill_switch_active": self.kill_switch_active(),
            "feature_flag": self.feature_flag.to_dict(),
            "customer_id": state.get("customer_id") if isinstance(state, dict) else None,
            "subscription_id": state.get("subscription_id") if isinstance(state, dict) else None,
            "plan_id": state.get("plan_id") if isinstance(state, dict) else None,
            "last_status": state.get("last_status", "not_started") if isinstance(state, dict) else "not_started",
            "last_error": state.get("last_error", "") if isinstance(state, dict) else "",
            "invoices": state.get("invoices", {}) if isinstance(state, dict) else {},
        }

    # --------------------------------------------------------------- customer
    def ensure_customer(
        self,
        workspace_id: str,
        *,
        name: str = "",
        secret_ref: Optional[str] = None,
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        if not self.billing_enabled(workspace_key):
            return self._disabled_result(workspace_key, "ensure_customer")
        secret = self._secret(secret_ref)
        now_text = utc_now() if now is None else str(now)
        idempotency_key = self._idempotency_key(
            "customer", workspace_key, "account", ""
        )
        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_key)
            if state.get("customer_id"):
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "existing",
                    "workspace_id": workspace_key,
                    "customer_id": state["customer_id"],
                    "idempotency_key": idempotency_key,
                    "billing_enabled": True,
                }
                self._save(data)
                return result
            try:
                provider_result = self._retry(
                    lambda: self.provider.create_customer(
                        workspace_id=workspace_key,
                        name=name or workspace_key,
                        idempotency_key=idempotency_key,
                        secret=secret,
                    ),
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                state["last_status"] = "degraded"
                state["last_error"] = _safe_text(exc, 500)
                state["updated_at"] = utc_now()
                self._save(data)
                return self._degraded_result(
                    workspace_key, "ensure_customer", exc, idempotency_key
                )
            state["customer_id"] = provider_result.get("customer_id")
            state["last_status"] = "customer_ready"
            state["last_error"] = ""
            state["updated_at"] = utc_now()
            self._save(data)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "created",
                "workspace_id": workspace_key,
                "customer_id": state["customer_id"],
                "provider": provider_result,
                "idempotency_key": idempotency_key,
                "billing_enabled": True,
            }

    def ensure_subscription(
        self,
        workspace_id: str,
        *,
        plan_id: str,
        secret_ref: Optional[str] = None,
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        if not self.billing_enabled(workspace_key):
            return self._disabled_result(workspace_key, "ensure_subscription")
        customer = self.ensure_customer(
            workspace_key, secret_ref=secret_ref, now=now
        )
        if customer.get("status") == "degraded":
            return customer
        customer_id = customer.get("customer_id")
        if not customer_id:
            with _locked(self.lock_path):
                data = self._load()
                state = self._workspace_state(data, workspace_key)
                customer_id = state.get("customer_id")
                self._save(data)
        if not customer_id:
            return self._degraded_result(
                workspace_key,
                "ensure_subscription",
                BillingError("No customer id is available."),
                self._idempotency_key("subscription", workspace_key, plan_id, ""),
            )
        secret = self._secret(secret_ref)
        idempotency_key = self._idempotency_key(
            "subscription", workspace_key, plan_id, ""
        )
        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_key)
            if state.get("subscription_id"):
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "existing",
                    "workspace_id": workspace_key,
                    "subscription_id": state["subscription_id"],
                    "plan_id": state.get("plan_id"),
                    "idempotency_key": idempotency_key,
                    "billing_enabled": True,
                }
                self._save(data)
                return result
            try:
                provider_result = self._retry(
                    lambda: self.provider.ensure_subscription(
                        workspace_id=workspace_key,
                        customer_id=str(customer_id),
                        plan_id=plan_id,
                        idempotency_key=idempotency_key,
                        secret=secret,
                    ),
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                state["last_status"] = "degraded"
                state["last_error"] = _safe_text(exc, 500)
                state["updated_at"] = utc_now()
                self._save(data)
                return self._degraded_result(
                    workspace_key, "ensure_subscription", exc, idempotency_key
                )
            state["subscription_id"] = provider_result.get("subscription_id")
            state["plan_id"] = _safe_text(plan_id)
            state["last_status"] = "subscription_ready"
            state["last_error"] = ""
            state["updated_at"] = utc_now()
            self._save(data)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "created",
                "workspace_id": workspace_key,
                "subscription_id": state["subscription_id"],
                "plan_id": state["plan_id"],
                "provider": provider_result,
                "idempotency_key": idempotency_key,
                "billing_enabled": True,
            }

    # --------------------------------------------------------------- invoices
    def sync_invoice(
        self,
        workspace_id: str,
        period: str,
        *,
        invoice_id: Optional[str] = None,
        secret_ref: Optional[str] = None,
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        period_key = _safe_text(period)
        if not workspace_key or not period_key:
            raise BillingError("Workspace and period are required.")
        if not self.billing_enabled(workspace_key):
            return self._disabled_result(workspace_key, "sync_invoice")
        secret = self._secret(secret_ref)
        invoice = self.ledger.invoice(workspace_key, period_key, invoice_id=invoice_id)
        idempotency_key = self._idempotency_key(
            "invoice", workspace_key, period_key, str(invoice.get("invoice_id", ""))
        )
        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_key)
            existing = state["invoices"].get(idempotency_key)
            if isinstance(existing, dict) and existing.get("status") == "reconciled":
                self._save(data)
                return {
                    "schema_version": SCHEMA_VERSION,
                    "status": "already_reconciled",
                    "workspace_id": workspace_key,
                    "period": period_key,
                    "invoice_id": invoice.get("invoice_id"),
                    "billing": existing,
                    "billing_enabled": True,
                }
        try:
            provider_result = self._retry(
                lambda: self.provider.reconcile_invoice(
                    workspace_id=workspace_key,
                    period=period_key,
                    invoice=invoice,
                    idempotency_key=idempotency_key,
                    secret=secret,
                ),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            with _locked(self.lock_path):
                data = self._load()
                state = self._workspace_state(data, workspace_key)
                state["last_status"] = "degraded"
                state["last_error"] = _safe_text(exc, 500)
                state["updated_at"] = utc_now()
                state["invoices"][idempotency_key] = {
                    "status": "degraded",
                    "error": _safe_text(exc, 500),
                    "invoice_id": invoice.get("invoice_id"),
                    "idempotency_key": idempotency_key,
                }
                self._save(data)
            return self._degraded_result(
                workspace_key, "sync_invoice", exc, idempotency_key, invoice=invoice
            )

        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_key)
            state["invoices"][idempotency_key] = provider_result
            state["last_status"] = "reconciled"
            state["last_error"] = ""
            state["updated_at"] = utc_now()
            self._save(data)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "reconciled",
            "workspace_id": workspace_key,
            "period": period_key,
            "invoice_id": invoice.get("invoice_id"),
            "ledger_total": invoice.get("total"),
            "billing": provider_result,
            "idempotency_key": idempotency_key,
            "billing_enabled": True,
        }

    def reconcile_period(
        self,
        workspace_id: str,
        period: str,
        *,
        invoice_id: Optional[str] = None,
        secret_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.sync_invoice(
            workspace_id, period, invoice_id=invoice_id, secret_ref=secret_ref
        )

    # --------------------------------------------------------------- helpers
    def _secret(self, secret_ref: Optional[str]) -> str:
        reference = _safe_text(secret_ref or self.secret_ref, 200)
        if not reference:
            raise BillingError("A secret reference is required.")
        return self.secret_manager.get(reference)

    def _disabled_result(self, workspace_id: str, action: str) -> Dict[str, Any]:
        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_id)
            state["last_status"] = "disabled"
            state["last_error"] = ""
            state["updated_at"] = utc_now()
            self._save(data)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "disabled",
            "action": action,
            "workspace_id": workspace_id,
            "billing_enabled": False,
            "kill_switch_active": self.kill_switch_active(),
            "feature_flag": self.feature_flag.to_dict(),
            "message": "Billing is disabled by feature flag or kill switch.",
        }

    def delete_workspace(self, workspace_id: str) -> bool:
        workspace_key = _safe_text(workspace_id)
        with _locked(self.lock_path):
            data = self._load()
            workspaces = data.get("workspaces", {})
            if workspace_key in workspaces:
                del workspaces[workspace_key]
                self._save(data)
                return True
        return False

    def cancel_subscription(
        self,
        workspace_id: str,
        *,
        secret_ref: Optional[str] = None,
        reason: str = "customer cancelled",
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        if not self.billing_enabled(workspace_key):
            return self._disabled_result(workspace_key, "cancel_subscription")
        secret = self._secret(secret_ref)
        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_key)
            if not state.get("subscription_id"):
                self._save(data)
                return self._degraded_result(
                    workspace_key,
                    "cancel_subscription",
                    BillingError("No subscription is available to cancel."),
                    self._idempotency_key("cancel", workspace_key, "subscription", ""),
                )
            subscription_id = str(state["subscription_id"])
            idempotency_key = self._idempotency_key(
                "cancel", workspace_key, subscription_id, ""
            )
            if state.get("subscription_status") == "cancelled":
                self._save(data)
                return {
                    "schema_version": SCHEMA_VERSION,
                    "status": "already_cancelled",
                    "workspace_id": workspace_key,
                    "subscription_id": subscription_id,
                    "billing_enabled": True,
                }
        try:
            provider_result = self._retry(
                lambda: self.provider.cancel_subscription(
                    workspace_id=workspace_key,
                    subscription_id=subscription_id,
                    idempotency_key=idempotency_key,
                    secret=secret,
                    reason=reason,
                ),
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            with _locked(self.lock_path):
                data = self._load()
                state = self._workspace_state(data, workspace_key)
                state["last_status"] = "degraded"
                state["last_error"] = _safe_text(exc, 500)
                state["updated_at"] = utc_now()
                self._save(data)
            return self._degraded_result(
                workspace_key, "cancel_subscription", exc, idempotency_key
            )
        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_key)
            state["subscription_status"] = "cancelled"
            state["last_status"] = "cancelled"
            state["last_error"] = ""
            state["updated_at"] = utc_now()
            state.setdefault("cancellations", []).append(provider_result)
            self._save(data)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "cancelled",
            "workspace_id": workspace_key,
            "subscription_id": subscription_id,
            "billing": provider_result,
            "idempotency_key": idempotency_key,
            "billing_enabled": True,
        }

    def record_payment_failure(
        self,
        workspace_id: str,
        *,
        reason: str,
        amount: Any = None,
        invoice_id: Optional[str] = None,
        now: Any = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_text(workspace_id)
        now_text = utc_now() if now is None else str(now)
        with _locked(self.lock_path):
            data = self._load()
            state = self._workspace_state(data, workspace_key)
            state["last_status"] = "payment_failed"
            state["last_error"] = _safe_text(reason, 500)
            state["updated_at"] = now_text
            state.setdefault("payment_failures", []).append(
                {
                    "reason": _safe_text(reason, 500),
                    "amount": amount,
                    "invoice_id": _safe_text(invoice_id or "", 200),
                    "timestamp": now_text,
                }
            )
            self._save(data)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "payment_failed",
            "workspace_id": workspace_key,
            "reason": _safe_text(reason, 500),
            "amount": amount,
            "invoice_id": _safe_text(invoice_id or "", 200),
        }

    fail_payment = record_payment_failure
    mark_payment_failed = record_payment_failure

    def _degraded_result(
        self,
        workspace_id: str,
        action: str,
        exc: Exception,
        idempotency_key: str,
        invoice: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "degraded",
            "action": action,
            "workspace_id": workspace_id,
            "billing_enabled": self.billing_enabled(workspace_id),
            "error": {"type": type(exc).__name__, "message": _safe_text(exc, 500)},
            "idempotency_key": idempotency_key,
            "invoice_id": (invoice or {}).get("invoice_id"),
            "message": "Billing provider failed; run and quota state are preserved.",
        }


# Aliases for adapters that use alternate names.
BillingService = BillingStore
BillingManager = BillingStore
BillingController = BillingStore
FeatureFlag = BillingFeatureFlag
SecretProvider = SecretManager
