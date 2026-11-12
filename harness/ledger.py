"""Append-only usage ledger and deterministic invoice export.

Metering events are the source of truth for billable usage.  This module turns
those events into immutable ledger entries, closes monthly periods, records
explicit corrections, and renders an invoice that a finance operator can
reconcile without re-reading the raw event stream.

The ledger is intentionally local and dependency-free.  Prices are supplied by
the caller through a versioned price book; the OSS core does not embed a
commercial pricing policy.  Every entry is hash-chained to the previous entry so
tampering or accidental edits are detectable.

Storage lives under ``.harness/ledger/``.  Usage rows are append-only JSON Lines,
period closes are an atomic JSON document, and both operations use a process
lock plus an advisory file lock so concurrent workers cannot interleave writes.
"""

from __future__ import annotations

import contextlib
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple, Union

from .determinism import format_timestamp, parse_timestamp
from .errors import LedgerError, LedgerPeriodClosed
from .metering import (
    EVENT_BROWSER_MINUTE_CONSUMED,
    EVENT_CHECK_EXECUTED,
    EVENT_EVIDENCE_BYTE_STORED,
    EVENT_EXPORT_GENERATED,
    EVENT_RUN_STARTED,
    EVENT_TYPES,
    EVENT_UNITS,
    validate_event,
)
from .models import new_id, utc_now
from .quota import quota_resource_for_event


SCHEMA_VERSION = "quality-harness.ledger/v1"
PERIOD_SCHEMA_VERSION = "quality-harness.ledger.period/v1"
INVOICE_SCHEMA_VERSION = "quality-harness.invoice/v1"
PRICE_BOOK_SCHEMA_VERSION = "quality-harness.price-book/v1"

DEFAULT_ENTRIES = Path(".harness") / "ledger" / "entries.jsonl"
DEFAULT_PERIODS = Path(".harness") / "ledger" / "periods.json"
DEFAULT_LOCK = Path(".harness") / "ledger" / "ledger.lock"

ENTRY_USAGE = "usage"
ENTRY_CORRECTION = "correction"
ENTRY_ADJUSTMENT = "adjustment"
ENTRY_TYPES = (ENTRY_USAGE, ENTRY_CORRECTION, ENTRY_ADJUSTMENT)

_SAFE_METADATA_KEYS = (
    "workflow_id",
    "check_id",
    "status",
    "mode",
    "kind",
    "format",
)
_MONEY_PLACES = 6
_PERIOD_RE = re.compile(r"^(\d{4})-(\d{2})$")
_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number(value: Any, name: str) -> float:
    if not _is_number(value):
        raise ValueError("{} must be a number.".format(name))
    if not math.isfinite(float(value)):
        raise ValueError("{} must be finite.".format(name))
    return float(value)


def _money(value: Any) -> float:
    return round(float(value), _MONEY_PLACES)


def _safe_text(value: Any, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _safe_metadata(metadata: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    if not isinstance(metadata, Mapping):
        return {}
    safe: Dict[str, str] = {}
    for key in _SAFE_METADATA_KEYS:
        value = metadata.get(key)
        if value not in (None, ""):
            safe[key] = _safe_text(value)
    return safe


def period_for_timestamp(timestamp: Any) -> str:
    """Return the UTC monthly period key for an ISO-8601 timestamp."""

    moment = parse_timestamp(timestamp).astimezone(timezone.utc)
    return "{:04d}-{:02d}".format(moment.year, moment.month)


def normalize_period(period: Any) -> str:
    """Validate ``YYYY-MM``, or derive the period from a timestamp."""

    text = _safe_text(period)
    if not text:
        raise LedgerError("A billing period is required.")
    match = _PERIOD_RE.match(text)
    if match:
        month = int(match.group(2))
        if month < 1 or month > 12:
            raise LedgerError("Billing period month must be between 01 and 12.")
        return text
    try:
        return period_for_timestamp(text)
    except (TypeError, ValueError) as exc:
        raise LedgerError(
            "Billing period must be YYYY-MM or an ISO-8601 timestamp: {}".format(exc)
        )


def period_bounds(period: Any) -> Dict[str, str]:
    """Return the inclusive start and exclusive end timestamps of a period."""

    key = normalize_period(period)
    year, month = (int(piece) for piece in key.split("-"))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return {
        "key": key,
        "start": format_timestamp(start),
        "end": format_timestamp(end),
        "reset_at": format_timestamp(end),
    }


def _resolve_context_workspace(context: Any) -> str:
    workspace = getattr(context, "workspace_id", None)
    if not workspace:
        raise LedgerError(
            "An authenticated workspace context is required before ledger access."
        )
    return _safe_text(workspace)


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".{}-".format(path.name), dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod(Path(temporary), 0o600)
        os.replace(temporary, path)
        _chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod(path.parent, 0o700)
    key = str(path.resolve())
    with _LOCK_REGISTRY_GUARD:
        lock = _LOCK_REGISTRY.setdefault(key, threading.Lock())
    with lock:
        with path.open("a+", encoding="utf-8") as handle:
            _chmod(path, 0o600)
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                fcntl = None  # type: ignore[assignment]
            try:
                yield
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass


@dataclass(frozen=True)
class PriceBook:
    """A versioned event-type price list supplied by cloud configuration."""

    prices: Mapping[str, float] = field(default_factory=dict)
    currency: str = "USD"
    version: str = "v1"

    def __post_init__(self) -> None:
        normalized: Dict[str, float] = {}
        for key, value in dict(self.prices or {}).items():
            event_type = _safe_text(key)
            if event_type not in EVENT_TYPES:
                raise LedgerError(
                    "Unknown price-book event type '{}'. Allowed event types: {}.".format(
                        key, ", ".join(EVENT_TYPES)
                    )
                )
            try:
                price = _number(value, "price for {}".format(event_type))
            except ValueError as exc:
                raise LedgerError(str(exc))
            if price < 0:
                raise LedgerError(
                    "Price for {} must be greater than or equal to zero.".format(
                        event_type
                    )
                )
            normalized[event_type] = price
        object.__setattr__(self, "prices", normalized)
        currency = _safe_text(self.currency, 12)
        if not currency:
            raise LedgerError("Price-book currency must be a non-empty string.")
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "version", _safe_text(self.version, 40) or "v1")

    def price(self, event_type: str) -> float:
        return float(self.prices.get(str(event_type), 0.0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": PRICE_BOOK_SCHEMA_VERSION,
            "version": self.version,
            "currency": self.currency,
            "prices": {key: self.prices[key] for key in sorted(self.prices)},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PriceBook":
        if not isinstance(data, Mapping):
            raise LedgerError("Price book must be a JSON object.")
        if data.get("schema_version") not in (None, PRICE_BOOK_SCHEMA_VERSION):
            raise LedgerError(
                "Unsupported price-book schema version: {}".format(
                    data.get("schema_version")
                )
            )
        raw = data.get("prices")
        if raw is None:
            raw = {
                key: value
                for key, value in data.items()
                if key not in ("schema_version", "version", "currency")
            }
        if not isinstance(raw, Mapping):
            raise LedgerError("Price-book prices must be an object.")
        prices: Dict[str, float] = {}
        for event_type, value in raw.items():
            if isinstance(value, Mapping):
                value = value.get("unit_price", value.get("price"))
            prices[event_type] = value
        return cls(
            prices=prices,
            currency=str(data.get("currency", "USD") or "USD"),
            version=str(data.get("version", "v1") or "v1"),
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> "PriceBook":
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise LedgerError("Could not read price book {}: {}".format(path, exc))
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise LedgerError("Price book is not valid JSON: {}".format(exc))
        return cls.from_dict(data)


class LedgerStore:
    """Append-only usage ledger with period close and invoice rendering."""

    def __init__(
        self,
        root: Path,
        entries_path: Optional[Union[str, Path]] = None,
        periods_path: Optional[Union[str, Path]] = None,
        lock_path: Optional[Union[str, Path]] = None,
        workspace: Optional[str] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.entries_path = self._resolve(entries_path or DEFAULT_ENTRIES)
        self.periods_path = self._resolve(periods_path or DEFAULT_PERIODS)
        self.lock_path = self._resolve(lock_path or DEFAULT_LOCK)
        self.workspace = _safe_text(workspace) or None
        self.bound_workspace = self.workspace

    def _resolve(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path

    def _workspace_or_bound(self, workspace: Optional[str]) -> str:
        value = _safe_text(workspace) or self.workspace
        if not value:
            raise LedgerError("A workspace is required for this ledger operation.")
        return value

    # ------------------------------------------------------------- context
    @classmethod
    def from_auth_context(
        cls,
        root: Path,
        context: Any,
        **kwargs: Any,
    ) -> "LedgerStore":
        """Bind a ledger to the workspace in an ``AuthContext``.

        Any caller-supplied ``workspace`` is ignored; tenant identity must come
        from the authenticated context, never a request body.
        """

        kwargs.pop("workspace", None)
        return cls(
            root,
            workspace=_resolve_context_workspace(context),
            **kwargs
        )

    # --------------------------------------------------------------- storage
    def _repair_tail(self) -> None:
        if not self.entries_path.exists():
            return
        try:
            data = self.entries_path.read_bytes()
        except OSError as exc:
            raise LedgerError("Could not read ledger entries: {}".format(exc))
        if not data or data.endswith(b"\n"):
            return
        cutoff = data.rfind(b"\n")
        try:
            with self.entries_path.open("r+b") as handle:
                handle.truncate(cutoff + 1 if cutoff >= 0 else 0)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerError("Could not repair the ledger tail: {}".format(exc))

    def _load_entries(self, repair: bool = False) -> List[Dict[str, Any]]:
        if not self.entries_path.exists():
            return []
        if repair:
            self._repair_tail()
        try:
            text = self.entries_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LedgerError("Could not read ledger entries: {}".format(exc))
        if text and not text.endswith("\n"):
            raise LedgerError(
                "Ledger entries end with an incomplete record; run a repair or "
                "inspect the file before appending."
            )
        entries: List[Dict[str, Any]] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise LedgerError(
                    "Ledger entry line {} is not valid JSON: {}".format(number, exc)
                )
            if not isinstance(entry, dict):
                raise LedgerError("Ledger entry line {} is not an object.".format(number))
            if entry.get("schema_version") != SCHEMA_VERSION:
                raise LedgerError(
                    "Ledger entry line {} has an unsupported schema version.".format(
                        number
                    )
                )
            entries.append(entry)
        return entries

    def _append_locked(
        self, entries: List[Dict[str, Any]], entry: Dict[str, Any]
    ) -> None:
        problem = self._verify_chain(entries)
        if problem is not None:
            raise LedgerError(
                "Ledger integrity check failed at {}: {}".format(*problem)
            )
        self.entries_path.parent.mkdir(parents=True, exist_ok=True)
        _chmod(self.entries_path.parent, 0o700)
        line = json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
        try:
            with self.entries_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerError("Could not append to the ledger: {}".format(exc))
        _chmod(self.entries_path, 0o600)

    def _empty_periods(self) -> Dict[str, Any]:
        return {
            "schema_version": PERIOD_SCHEMA_VERSION,
            "periods": {},
        }

    def _load_periods(self) -> Dict[str, Any]:
        if not self.periods_path.exists():
            return self._empty_periods()
        try:
            data = json.loads(self.periods_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LedgerError("The ledger period store is unreadable: {}".format(exc))
        if not isinstance(data, dict):
            raise LedgerError("The ledger period store is invalid.")
        if data.get("schema_version") != PERIOD_SCHEMA_VERSION:
            raise LedgerError(
                "Unsupported ledger period schema version: {}".format(
                    data.get("schema_version")
                )
            )
        if not isinstance(data.get("periods"), dict):
            data["periods"] = {}
        return data

    def _save_periods(self, data: Dict[str, Any]) -> None:
        _atomic_write(
            self.periods_path,
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    # --------------------------------------------------------------- hashing
    def _entry_hash(self, entry: Mapping[str, Any]) -> str:
        payload = {key: value for key, value in entry.items() if key != "entry_hash"}
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _verify_chain(
        self, entries: Iterable[Mapping[str, Any]]
    ) -> Optional[Tuple[str, str]]:
        previous = ""
        expected = 1
        for entry in entries:
            entry_id = str(entry.get("entry_id", ""))
            if entry.get("sequence") != expected:
                return entry_id or "?", "sequence {} is not {}".format(
                    entry.get("sequence"), expected
                )
            if entry.get("previous_hash") != previous:
                return entry_id or "?", "previous_hash does not match the prior entry"
            if entry.get("entry_hash") != self._entry_hash(entry):
                return entry_id or "?", "entry hash does not match the stored payload"
            previous = str(entry.get("entry_hash", ""))
            expected += 1
        return None

    def _new_entry(
        self,
        entries: List[Dict[str, Any]],
        *,
        workspace: str,
        period: str,
        entry_type: str,
        event_type: str,
        unit: str,
        quantity: float,
        unit_price: float,
        currency: str,
        timestamp: str,
        recorded_at: str,
        source_event_id: str = "",
        source_idempotency_key: str = "",
        idempotency_key: str = "",
        references: Optional[Iterable[str]] = None,
        reason: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        if entry_type not in ENTRY_TYPES:
            raise LedgerError("Unknown ledger entry type: {}".format(entry_type))
        if event_type not in EVENT_TYPES:
            raise LedgerError("Unknown metering event type: {}".format(event_type))
        sequence = int(entries[-1].get("sequence", 0)) + 1 if entries else 1
        previous_hash = str(entries[-1].get("entry_hash", "")) if entries else ""
        entry = {
            "schema_version": SCHEMA_VERSION,
            "entry_id": new_id("ledger"),
            "workspace": workspace,
            "period": period,
            "entry_type": entry_type,
            "event_type": event_type,
            "resource": quota_resource_for_event(event_type) or "",
            "unit": unit,
            "quantity": quantity,
            "unit_price": _money(unit_price),
            "amount": _money(float(quantity) * float(unit_price)),
            "currency": currency,
            "timestamp": format_timestamp(timestamp),
            "recorded_at": format_timestamp(recorded_at),
            "source_event_id": _safe_text(source_event_id),
            "source_idempotency_key": _safe_text(source_idempotency_key),
            "idempotency_key": _safe_text(idempotency_key),
            "references": [
                _safe_text(reference)
                for reference in (references or [])
                if _safe_text(reference)
            ],
            "reason": _safe_text(reason, 500),
            "metadata": _safe_metadata(metadata),
            "sequence": sequence,
            "previous_hash": previous_hash,
        }
        entry["entry_hash"] = self._entry_hash(entry)
        return entry

    # --------------------------------------------------------------- queries
    def entries(
        self,
        workspace: Optional[str] = None,
        period: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return copies of ledger entries, optionally filtered."""

        expected_workspace = (
            _safe_text(workspace) or self.workspace or None
        )
        expected_period = normalize_period(period) if period else None
        result = []
        with _locked(self.lock_path):
            entries = self._load_entries()
        for entry in entries:
            if expected_workspace and entry.get("workspace") != expected_workspace:
                continue
            if expected_period and entry.get("period") != expected_period:
                continue
            result.append(copy.deepcopy(entry))
        return result

    def get(self, entry_id: str) -> Optional[Dict[str, Any]]:
        identifier = _safe_text(entry_id)
        with _locked(self.lock_path):
            entries = self._load_entries()
        for entry in entries:
            if entry.get("entry_id") == identifier:
                return copy.deepcopy(entry)
        return None

    def periods(self, workspace: Optional[str] = None) -> List[Dict[str, Any]]:
        expected = _safe_text(workspace) or self.workspace or None
        with _locked(self.lock_path):
            data = self._load_periods()
        result = []
        for workspace_id in sorted(data.get("periods", {})):
            if expected and workspace_id != expected:
                continue
            for period in sorted(data["periods"][workspace_id]):
                record = data["periods"][workspace_id][period]
                if isinstance(record, dict):
                    result.append(copy.deepcopy(record))
        return result

    def get_close(
        self, workspace: Optional[str], period: str
    ) -> Optional[Dict[str, Any]]:
        workspace_id = self._workspace_or_bound(workspace)
        period_id = normalize_period(period)
        with _locked(self.lock_path):
            data = self._load_periods()
        record = data.get("periods", {}).get(workspace_id, {}).get(period_id)
        return copy.deepcopy(record) if isinstance(record, dict) else None

    def is_closed(self, workspace: Optional[str], period: str) -> bool:
        return self.get_close(workspace, period) is not None

    def verify(self) -> Dict[str, Any]:
        """Verify sequence and hash continuity without mutating the ledger."""

        try:
            with _locked(self.lock_path):
                entries = self._load_entries()
        except LedgerError as exc:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "broken",
                "ok": False,
                "entry_count": 0,
                "broken_at": None,
                "reason": str(exc),
            }
        problem = self._verify_chain(entries)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if problem is None else "broken",
            "ok": problem is None,
            "entry_count": len(entries),
            "broken_at": problem[0] if problem else None,
            "reason": problem[1] if problem else "",
        }

    # ------------------------------------------------------------- ingestion
    def ingest_event(
        self,
        event: Mapping[str, Any],
        price_book: Optional[PriceBook] = None,
    ) -> Dict[str, Any]:
        """Append one metering event, or return the existing idempotent entry."""

        if not isinstance(event, Mapping):
            raise LedgerError("A metering event must be a JSON object.")
        record = dict(event)
        errors = validate_event(record)
        if errors:
            raise LedgerError(
                "Invalid metering event: {}".format("; ".join(errors))
            )
        workspace = _safe_text(record.get("workspace"))
        if not workspace:
            raise LedgerError("A metering event requires a workspace.")
        if self.workspace and workspace != self.workspace:
            raise LedgerError(
                "The metering event workspace does not match the authenticated context."
            )
        event_type = str(record["event_type"])
        period = period_for_timestamp(record["timestamp"])
        source_key = _safe_text(record.get("idempotency_key"))
        signature = {
            "workspace": workspace,
            "event_type": event_type,
            "quantity": float(record.get("quantity", 0)),
            "unit": str(record.get("unit", "")),
        }
        with _locked(self.lock_path):
            entries = self._load_entries(repair=True)
            for existing in entries:
                if (
                    existing.get("entry_type") == ENTRY_USAGE
                    and existing.get("source_idempotency_key") == source_key
                ):
                    existing_signature = {
                        "workspace": existing.get("workspace"),
                        "event_type": existing.get("event_type"),
                        "quantity": float(existing.get("quantity", 0)),
                        "unit": existing.get("unit"),
                    }
                    if existing_signature != signature:
                        raise LedgerError(
                            "Duplicate ledger event key '{}' has a different payload.".format(
                                source_key
                            )
                        )
                    return {
                        "schema_version": SCHEMA_VERSION,
                        "status": "duplicate",
                        "created": False,
                        "duplicate": True,
                        "entry": copy.deepcopy(existing),
                    }
            periods = self._load_periods()
            if self._close_record(periods, workspace, period) is not None:
                raise LedgerPeriodClosed(workspace, period)
            price = price_book.price(event_type) if price_book else 0.0
            currency = price_book.currency if price_book else "USD"
            entry = self._new_entry(
                entries,
                workspace=workspace,
                period=period,
                entry_type=ENTRY_USAGE,
                event_type=event_type,
                unit=str(record["unit"]),
                quantity=record["quantity"],
                unit_price=price,
                currency=currency,
                timestamp=str(record["timestamp"]),
                recorded_at=utc_now(),
                source_event_id=str(record.get("event_id", "")),
                source_idempotency_key=source_key,
                metadata=record.get("metadata", {}),
            )
            self._append_locked(entries, entry)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "appended",
                "created": True,
                "duplicate": False,
                "entry": copy.deepcopy(entry),
            }

    def ingest_events(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        workspace: Optional[str] = None,
        price_book: Optional[PriceBook] = None,
        strict: bool = False,
    ) -> Dict[str, Any]:
        """Ingest a batch, reporting duplicates, skips, and rejected events."""

        expected_workspace = _safe_text(workspace) or self.workspace or None
        ingested: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        duplicates = 0
        skipped = 0
        for event in events:
            event_workspace = _safe_text(event.get("workspace")) if isinstance(event, Mapping) else ""
            if expected_workspace and event_workspace != expected_workspace:
                skipped += 1
                continue
            try:
                result = self.ingest_event(event, price_book=price_book)
            except LedgerPeriodClosed as exc:
                if strict:
                    raise
                rejected.append(
                    {
                        "code": "period_closed",
                        "workspace": exc.workspace,
                        "period": exc.period,
                        "message": str(exc),
                        "event_id": _safe_text(event.get("event_id")) if isinstance(event, Mapping) else "",
                        "idempotency_key": _safe_text(event.get("idempotency_key")) if isinstance(event, Mapping) else "",
                    }
                )
            except (LedgerError, ValueError) as exc:
                if strict:
                    raise
                rejected.append(
                    {
                        "code": "invalid_event",
                        "message": str(exc),
                        "event_id": _safe_text(event.get("event_id")) if isinstance(event, Mapping) else "",
                        "idempotency_key": _safe_text(event.get("idempotency_key")) if isinstance(event, Mapping) else "",
                    }
                )
            else:
                if result.get("duplicate"):
                    duplicates += 1
                else:
                    ingested.append(result["entry"])
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "partial" if rejected else "ok",
            "ingested": len(ingested),
            "duplicates": duplicates,
            "skipped": skipped,
            "rejected": rejected,
            "entries": ingested,
        }

    def ingest(
        self,
        event_or_events: Union[Mapping[str, Any], Iterable[Mapping[str, Any]]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if isinstance(event_or_events, Mapping):
            return self.ingest_event(event_or_events, **kwargs)
        return self.ingest_events(event_or_events, **kwargs)

    # ------------------------------------------------------------ adjustments
    def add_adjustment(
        self,
        workspace: Optional[str] = None,
        period: Optional[str] = None,
        event_type: str = "",
        quantity: Union[int, float] = 0,
        *,
        reason: str = "",
        description: Optional[str] = None,
        references: Optional[Iterable[str]] = None,
        entry_id: Optional[str] = None,
        original_entry_id: Optional[str] = None,
        close_id: Optional[str] = None,
        unit: Optional[str] = None,
        unit_price: Optional[float] = None,
        price_book: Optional[PriceBook] = None,
        idempotency_key: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        entry_type: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append an explicit signed correction or late-period adjustment.

        Corrections reference the original ledger entry.  An adjustment for a
        late event may reference the period-close id instead, which is why it is
        the only write path that remains open after a period is closed.
        """

        if isinstance(event_type, (int, float)) and quantity == 0:
            quantity = event_type  # tolerate add_adjustment(ws, period, -1, ...)
            event_type = ""
        workspace_id = self._workspace_or_bound(workspace)
        moment = format_timestamp(timestamp or utc_now())
        period_id = normalize_period(period) if period else period_for_timestamp(moment)
        amount = _number(quantity, "quantity")
        quantity_value = quantity
        if amount == 0:
            raise LedgerError("An adjustment quantity must be non-zero.")
        reason_text = _safe_text(reason or description, 500)
        if not reason_text:
            raise LedgerError("An adjustment requires a reason.")
        collected_references = list(references or [])
        for candidate in (entry_id, original_entry_id, close_id):
            if candidate:
                collected_references.append(candidate)
        reference_ids = [
            _safe_text(reference)
            for reference in collected_references
            if _safe_text(reference)
        ]
        if not reference_ids:
            raise LedgerError("An adjustment must reference an entry or period close.")
        key = _safe_text(idempotency_key)

        with _locked(self.lock_path):
            entries = self._load_entries(repair=True)
            period_data = self._load_periods()
            entry_index = {str(entry.get("entry_id")): entry for entry in entries}
            close_index: Dict[str, Dict[str, Any]] = {}
            for workspace_periods in period_data.get("periods", {}).values():
                for close in workspace_periods.values():
                    if isinstance(close, dict) and close.get("close_id"):
                        close_index[str(close["close_id"])] = close

            if key:
                for existing in entries:
                    if existing.get("idempotency_key") == key:
                        if (
                            existing.get("workspace") == workspace_id
                            and existing.get("period") == period_id
                            and existing.get("event_type") == event_type
                            and float(existing.get("quantity", 0)) == amount
                        ):
                            return {
                                "schema_version": SCHEMA_VERSION,
                                "status": "duplicate",
                                "created": False,
                                "duplicate": True,
                                "entry": copy.deepcopy(existing),
                            }
                        raise LedgerError(
                            "Duplicate adjustment key '{}' has a different payload.".format(
                                key
                            )
                        )

            referenced_entries: List[Dict[str, Any]] = []
            referenced_closes: List[Dict[str, Any]] = []
            for reference in reference_ids:
                if reference in entry_index:
                    original = entry_index[reference]
                    if original.get("workspace") != workspace_id:
                        raise LedgerError(
                            "Adjustment reference '{}' belongs to another workspace.".format(
                                reference
                            )
                        )
                    referenced_entries.append(original)
                elif reference in close_index:
                    close = close_index[reference]
                    if close.get("workspace") != workspace_id:
                        raise LedgerError(
                            "Adjustment reference '{}' belongs to another workspace.".format(
                                reference
                            )
                        )
                    referenced_closes.append(close)
                else:
                    raise LedgerError(
                        "Adjustment reference '{}' does not exist in this ledger.".format(
                            reference
                        )
                    )

            resolved_event_type = _safe_text(event_type)
            if not resolved_event_type and referenced_entries:
                values = {str(entry.get("event_type")) for entry in referenced_entries}
                if len(values) != 1:
                    raise LedgerError(
                        "An adjustment referencing multiple event types must specify one."
                    )
                resolved_event_type = values.pop()
            if resolved_event_type not in EVENT_TYPES:
                raise LedgerError(
                    "An adjustment requires a known metering event type."
                )

            resolved_unit = _safe_text(unit) or EVENT_UNITS[resolved_event_type]
            if referenced_entries:
                original_units = {str(entry.get("unit")) for entry in referenced_entries}
                if len(original_units) != 1:
                    raise LedgerError(
                        "An adjustment cannot mix units from multiple original entries."
                    )
                original_unit = original_units.pop()
                if unit and unit != original_unit:
                    raise LedgerError(
                        "Adjustment unit '{}' does not match original unit '{}'.".format(
                            unit, original_unit
                        )
                    )
                resolved_unit = original_unit

            resolved_price = unit_price
            if resolved_price is None:
                if referenced_entries:
                    original_prices = {
                        float(entry.get("unit_price", 0)) for entry in referenced_entries
                    }
                    if len(original_prices) == 1:
                        resolved_price = original_prices.pop()
                if resolved_price is None:
                    resolved_price = (
                        price_book.price(resolved_event_type) if price_book else 0.0
                    )
            resolved_price = _number(resolved_price, "unit_price")
            if resolved_price < 0:
                raise LedgerError("An adjustment unit price cannot be negative.")

            currencies = {
                str(entry.get("currency", "USD")) for entry in referenced_entries
            }
            if len(currencies) > 1:
                raise LedgerError(
                    "An adjustment cannot reference entries in multiple currencies."
                )
            currency = currencies.pop() if currencies else (
                price_book.currency if price_book else "USD"
            )

            resolved_type = entry_type
            if resolved_type is None:
                resolved_type = (
                    ENTRY_CORRECTION if referenced_entries else ENTRY_ADJUSTMENT
                )
            if resolved_type not in (ENTRY_CORRECTION, ENTRY_ADJUSTMENT):
                raise LedgerError(
                    "An adjustment entry_type must be correction or adjustment."
                )

            entry = self._new_entry(
                entries,
                workspace=workspace_id,
                period=period_id,
                entry_type=resolved_type,
                event_type=resolved_event_type,
                unit=resolved_unit,
                quantity=quantity_value,
                unit_price=resolved_price,
                currency=currency,
                timestamp=moment,
                recorded_at=utc_now(),
                idempotency_key=key,
                references=reference_ids,
                reason=reason_text,
                metadata=metadata,
            )
            self._append_locked(entries, entry)
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "appended",
                "created": True,
                "duplicate": False,
                "entry": copy.deepcopy(entry),
            }

    def add_correction(
        self,
        workspace: Optional[str],
        period: Optional[str],
        entry_id: str,
        quantity: Union[int, float],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        kwargs.pop("references", None)
        kwargs["entry_type"] = ENTRY_CORRECTION
        kwargs["references"] = [entry_id]
        return self.add_adjustment(workspace, period, "", quantity, **kwargs)

    def add_late_adjustment(
        self,
        workspace: Optional[str],
        period: Optional[str],
        close_id: str,
        event_type: str,
        quantity: Union[int, float],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        kwargs.pop("references", None)
        kwargs["entry_type"] = ENTRY_ADJUSTMENT
        kwargs["references"] = [close_id]
        return self.add_adjustment(
            workspace, period, event_type, quantity, **kwargs
        )

    adjust = add_adjustment
    correct = add_adjustment

    # ------------------------------------------------------------ period close
    def _close_record(
        self, periods: Mapping[str, Any], workspace: str, period: str
    ) -> Optional[Dict[str, Any]]:
        record = periods.get("periods", {}).get(workspace, {}).get(period)
        return record if isinstance(record, dict) else None

    def close_period(
        self,
        workspace: str,
        period: str,
        *,
        closed_by: str = "",
        closed_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Close a period and return its immutable close record."""

        workspace_id = self._workspace_or_bound(workspace)
        period_id = normalize_period(period)
        moment = format_timestamp(closed_at or utc_now())
        with _locked(self.lock_path):
            entries = self._load_entries(repair=True)
            periods = self._load_periods()
            existing = self._close_record(periods, workspace_id, period_id)
            if existing is not None:
                result = copy.deepcopy(existing)
                result["created"] = False
                return result
            invoice = self._build_invoice(
                entries,
                periods,
                workspace_id,
                period_id,
                invoice_id="close-preview",
                generated_at=moment,
            )
            record = {
                "schema_version": PERIOD_SCHEMA_VERSION,
                "close_id": new_id("close"),
                "workspace": workspace_id,
                "period": period_id,
                "period_start": invoice["period_start"],
                "period_end": invoice["period_end"],
                "closed_at": moment,
                "closed_by": _safe_text(closed_by),
                "entry_count": invoice["entry_count"],
                "last_entry_hash": str(entries[-1].get("entry_hash", "")) if entries else "",
                "currency": invoice["currency"],
                "subtotal": invoice["subtotal"],
                "credits": invoice["credits"],
                "total": invoice["total"],
            }
            periods.setdefault("periods", {}).setdefault(workspace_id, {})[
                period_id
            ] = record
            self._save_periods(periods)
            result = copy.deepcopy(record)
            result["created"] = True
            return result

    close = close_period

    # --------------------------------------------------------------- invoice
    def _build_invoice(
        self,
        entries: Iterable[Mapping[str, Any]],
        periods: Mapping[str, Any],
        workspace: str,
        period: str,
        *,
        invoice_id: Optional[str] = None,
        generated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        entries = list(entries)
        problem = self._verify_chain(entries)
        if problem is not None:
            raise LedgerError(
                "Ledger integrity check failed at {}: {}".format(*problem)
            )
        usage: List[Mapping[str, Any]] = []
        adjustments: List[Mapping[str, Any]] = []
        for entry in entries:
            if entry.get("workspace") != workspace or entry.get("period") != period:
                continue
            if entry.get("entry_type") == ENTRY_USAGE:
                usage.append(entry)
            elif entry.get("entry_type") in (ENTRY_CORRECTION, ENTRY_ADJUSTMENT):
                adjustments.append(entry)
        usage.sort(key=lambda item: int(item.get("sequence", 0)))
        adjustments.sort(key=lambda item: int(item.get("sequence", 0)))

        groups: Dict[Tuple[str, str, float, str], Dict[str, Any]] = {}
        for entry in usage:
            key = (
                str(entry.get("event_type")),
                str(entry.get("unit")),
                float(entry.get("unit_price", 0)),
                str(entry.get("currency", "USD")),
            )
            group = groups.setdefault(
                key,
                {
                    "event_type": key[0],
                    "unit": key[1],
                    "unit_price": key[2],
                    "currency": key[3],
                    "quantity": 0.0,
                    "amount": 0.0,
                    "entry_count": 0,
                },
            )
            group["quantity"] += float(entry.get("quantity", 0))
            group["amount"] += float(entry.get("amount", 0))
            group["entry_count"] += 1
        line_items = []
        for key in sorted(groups):
            group = groups[key]
            group["quantity"] = _money(group["quantity"])
            group["amount"] = _money(group["amount"])
            line_items.append(group)

        adjustment_rows = []
        for entry in adjustments:
            adjustment_rows.append(
                {
                    "entry_id": str(entry.get("entry_id")),
                    "entry_type": str(entry.get("entry_type")),
                    "event_type": str(entry.get("event_type")),
                    "unit": str(entry.get("unit")),
                    "quantity": _money(entry.get("quantity", 0)),
                    "unit_price": _money(entry.get("unit_price", 0)),
                    "amount": _money(entry.get("amount", 0)),
                    "currency": str(entry.get("currency", "USD")),
                    "reason": str(entry.get("reason", "")),
                    "references": list(entry.get("references", [])),
                    "recorded_at": str(entry.get("recorded_at", "")),
                }
            )
        subtotal = _money(sum(float(item["amount"]) for item in line_items))
        adjustment_total = _money(
            sum(float(item["amount"]) for item in adjustment_rows)
        )
        credits = _money(
            sum(abs(float(item["amount"])) for item in adjustment_rows if float(item["amount"]) < 0)
        )
        total = _money(subtotal + adjustment_total)
        currencies = {
            str(entry.get("currency", "USD"))
            for entry in list(usage) + list(adjustments)
        }
        if len(currencies) > 1:
            raise LedgerError(
                "Cannot render one invoice for entries in multiple currencies."
            )
        currency = currencies.pop() if currencies else "USD"
        close = self._close_record(periods, workspace, period)
        bounds = period_bounds(period)
        return {
            "schema_version": INVOICE_SCHEMA_VERSION,
            "invoice_id": _safe_text(invoice_id) or new_id("inv"),
            "status": "closed" if close is not None else "open",
            "workspace": workspace,
            "period": period,
            "period_start": bounds["start"],
            "period_end": bounds["end"],
            "reset_at": bounds["reset_at"],
            "generated_at": format_timestamp(generated_at or utc_now()),
            "closed": close is not None,
            "close_id": str(close.get("close_id", "")) if close else "",
            "currency": currency,
            "entry_count": len(usage) + len(adjustments),
            "line_items": line_items,
            "adjustments": adjustment_rows,
            "subtotal": subtotal,
            "credits": credits,
            "adjustments_total": adjustment_total,
            "total": total,
        }

    def invoice(
        self,
        workspace: str,
        period: str,
        *,
        invoice_id: Optional[str] = None,
        generated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        workspace_id = self._workspace_or_bound(workspace)
        period_id = normalize_period(period)
        with _locked(self.lock_path):
            entries = self._load_entries(repair=True)
            periods = self._load_periods()
            return self._build_invoice(
                entries,
                periods,
                workspace_id,
                period_id,
                invoice_id=invoice_id,
                generated_at=generated_at,
            )

    def reconcile(
        self,
        workspace: str,
        period: str,
        *,
        invoice: Optional[Mapping[str, Any]] = None,
        invoice_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compare an invoice total with the ledger's quantity-price total."""

        workspace_id = self._workspace_or_bound(workspace)
        period_id = normalize_period(period)
        rendered = (
            dict(invoice)
            if invoice is not None
            else self.invoice(workspace_id, period_id, invoice_id=invoice_id)
        )
        entries = self.entries(workspace_id, period_id)
        ledger_total = _money(
            sum(
                float(entry.get("quantity", 0))
                * float(entry.get("unit_price", 0))
                for entry in entries
            )
        )
        invoice_total = _money(rendered.get("total", 0))
        difference = _money(ledger_total - invoice_total)
        ok = abs(difference) <= 10 ** -_MONEY_PLACES
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ok else "mismatch",
            "ok": ok,
            "workspace": workspace_id,
            "period": period_id,
            "entry_count": len(entries),
            "ledger_total": ledger_total,
            "invoice_total": invoice_total,
            "difference": difference,
            "invoice_id": str(rendered.get("invoice_id", "")),
        }

    # --------------------------------------------------------------- context
    def _ensure_context(self, context: Any, workspace: str) -> str:
        resolved = _resolve_context_workspace(context)
        if self.workspace and self.workspace != resolved:
            raise LedgerError(
                "The ledger store is bound to another workspace."
            )
        if _safe_text(workspace) != resolved:
            raise LedgerError(
                "The ledger request workspace does not match the authenticated context."
            )
        return resolved

    def ingest_for_context(
        self,
        context: Any,
        event: Mapping[str, Any],
        price_book: Optional[PriceBook] = None,
    ) -> Dict[str, Any]:
        self._ensure_context(context, event.get("workspace", "") if isinstance(event, Mapping) else "")
        return self.ingest_event(event, price_book=price_book)

    def add_adjustment_for(
        self,
        context: Any,
        period: str,
        event_type: str,
        quantity: Union[int, float],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        workspace = _resolve_context_workspace(context)
        kwargs.pop("workspace", None)
        return self.add_adjustment(
            workspace, period, event_type, quantity, **kwargs
        )

    def close_for(
        self, context: Any, period: str, **kwargs: Any
    ) -> Dict[str, Any]:
        return self.close_period(
            _resolve_context_workspace(context), period, **kwargs
        )

    def invoice_for(
        self, context: Any, period: str, **kwargs: Any
    ) -> Dict[str, Any]:
        return self.invoice(
            _resolve_context_workspace(context), period, **kwargs
        )

    def entries_for(
        self,
        context: Any,
        period: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.entries(
            workspace=_resolve_context_workspace(context), period=period
        )

    def periods_for(self, context: Any) -> List[Dict[str, Any]]:
        return self.periods(workspace=_resolve_context_workspace(context))

    # ---------------------------------------------------------------- aliases
    def append(
        self,
        event_or_events: Union[Mapping[str, Any], Iterable[Mapping[str, Any]]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.ingest(event_or_events, **kwargs)

    list_entries = entries
    list_periods = periods

    def export_invoice(
        self,
        workspace: Optional[str] = None,
        period: Optional[str] = None,
        *,
        format: str = "json",
        **kwargs: Any,
    ) -> Union[Dict[str, Any], str]:
        workspace_id = self._workspace_or_bound(workspace)
        period_id = normalize_period(period) if period else None
        if period_id is None:
            raise LedgerError("An invoice requires a period.")
        invoice = self.invoice(workspace_id, period_id, **kwargs)
        if format == "csv":
            return invoice_to_csv(invoice)
        return invoice


def _format_money(value: Any) -> str:
    return "{:.{}f}".format(float(value), _MONEY_PLACES)


def invoice_to_csv(invoice: Mapping[str, Any]) -> str:
    """Render an invoice as deterministic CSV with line items and totals."""

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "workspace",
            "period",
            "currency",
            "row_type",
            "event_type",
            "unit",
            "quantity",
            "unit_price",
            "amount",
            "reference",
            "description",
        ]
    )
    workspace = str(invoice.get("workspace", ""))
    period = str(invoice.get("period", ""))
    currency = str(invoice.get("currency", "USD"))
    for item in invoice.get("line_items", []):
        writer.writerow(
            [
                workspace,
                period,
                currency,
                "line_item",
                item.get("event_type", ""),
                item.get("unit", ""),
                _format_money(item.get("quantity", 0)),
                _format_money(item.get("unit_price", 0)),
                _format_money(item.get("amount", 0)),
                "",
                "",
            ]
        )
    for item in invoice.get("adjustments", []):
        writer.writerow(
            [
                workspace,
                period,
                currency,
                item.get("entry_type", "adjustment"),
                item.get("event_type", ""),
                item.get("unit", ""),
                _format_money(item.get("quantity", 0)),
                _format_money(item.get("unit_price", 0)),
                _format_money(item.get("amount", 0)),
                ",".join(str(reference) for reference in item.get("references", [])),
                item.get("reason", ""),
            ]
        )
    for row_type in ("subtotal", "credits", "adjustments_total", "total"):
        writer.writerow(
            [
                workspace,
                period,
                currency,
                row_type,
                "",
                "",
                "",
                "",
                _format_money(invoice.get(row_type, 0)),
                "",
                "",
            ]
        )
    return output.getvalue()


# Descriptive aliases for callers that prefer alternate domain names.
UsageLedger = LedgerStore
Ledger = LedgerStore
