"""Canonical usage metering events for the cloud billing boundary.

The OSS harness stays dependency-free and account-free.  It emits the same
versioned usage events that a cloud deployment will later forward to its
ledger, so the event contract can be tested locally before any billing provider
is involved.

Events are append-only JSON Lines.  Every event carries an idempotency key; a
duplicate delivery is ignored rather than written a second time.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .determinism import parse_timestamp
from .errors import MeteringError
from .models import new_id, utc_now


SCHEMA_VERSION = "quality-harness.metering/v1"
SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas" / "metering"
DEFAULT_SINK = Path(".harness") / "metering" / "events.jsonl"
DEFAULT_WORKSPACE = "local"
DEFAULT_SOURCE = "oss"

EVENT_RUN_STARTED = "run_started"
EVENT_CHECK_EXECUTED = "check_executed"
EVENT_BROWSER_MINUTE_CONSUMED = "browser_minute_consumed"
EVENT_EVIDENCE_BYTE_STORED = "evidence_byte_stored"
EVENT_EXPORT_GENERATED = "export_generated"

# Frozen v1 names.  Adding a new billable action requires a new versioned
# contract; it must not silently reuse an existing name.
EVENT_TYPES: Tuple[str, ...] = (
    EVENT_RUN_STARTED,
    EVENT_CHECK_EXECUTED,
    EVENT_BROWSER_MINUTE_CONSUMED,
    EVENT_EVIDENCE_BYTE_STORED,
    EVENT_EXPORT_GENERATED,
)

EVENT_UNITS: Dict[str, str] = {
    EVENT_RUN_STARTED: "run",
    EVENT_CHECK_EXECUTED: "check",
    EVENT_BROWSER_MINUTE_CONSUMED: "browser_minute",
    EVENT_EVIDENCE_BYTE_STORED: "byte",
    EVENT_EXPORT_GENERATED: "export",
}

EVENT_REQUIRED_METADATA: Dict[str, Tuple[str, ...]] = {
    EVENT_RUN_STARTED: ("workflow_id",),
    EVENT_CHECK_EXECUTED: ("check_id", "status"),
    EVENT_BROWSER_MINUTE_CONSUMED: ("mode",),
    EVENT_EVIDENCE_BYTE_STORED: ("kind", "path"),
    EVENT_EXPORT_GENERATED: ("format",),
}

SCHEMA_FILES: Dict[str, str] = {
    "envelope": "envelope.v1.schema.json",
    EVENT_RUN_STARTED: "run_started.v1.schema.json",
    EVENT_CHECK_EXECUTED: "check_executed.v1.schema.json",
    EVENT_BROWSER_MINUTE_CONSUMED: "browser_minute_consumed.v1.schema.json",
    EVENT_EVIDENCE_BYTE_STORED: "evidence_byte_stored.v1.schema.json",
    EVENT_EXPORT_GENERATED: "export_generated.v1.schema.json",
}

REQUIRED_ENVELOPE_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "event_id",
    "event_type",
    "idempotency_key",
    "workspace",
    "run",
    "quantity",
    "unit",
    "timestamp",
    "source",
)


def schema_path(name: str) -> Path:
    """Return the JSON Schema path for an envelope or event type name."""

    try:
        filename = SCHEMA_FILES[name]
    except KeyError:
        raise MeteringError("Unknown metering schema: {}".format(name))
    return SCHEMA_DIR / filename


def event_schema_paths() -> Dict[str, Path]:
    """Return every schema path keyed by envelope/event name."""

    return {name: schema_path(name) for name in SCHEMA_FILES}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_event(event: Dict[str, Any]) -> List[str]:
    """Validate an event against the v1 contract without external packages.

    The JSON Schema files are the normative contract.  This function is the
    dependency-free runtime check used before a sink write.
    """

    errors: List[str] = []
    if not isinstance(event, dict):
        return ["event must be a JSON object"]
    for field in REQUIRED_ENVELOPE_FIELDS:
        if field not in event:
            errors.append("missing required field: {}".format(field))
    if event.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be {}".format(SCHEMA_VERSION))
    for field in ("event_id", "idempotency_key", "workspace", "run", "source"):
        if field in event and not _non_empty_string(event.get(field)):
            errors.append("{} must be a non-empty string".format(field))
    event_type = event.get("event_type")
    if event_type not in EVENT_TYPES:
        errors.append(
            "event_type must be one of: {}".format(", ".join(EVENT_TYPES))
        )
        return errors
    quantity = event.get("quantity")
    if not _is_number(quantity):
        errors.append("quantity must be a number")
    elif not math.isfinite(float(quantity)):
        errors.append("quantity must be finite")
    elif event_type == EVENT_BROWSER_MINUTE_CONSUMED and float(quantity) <= 0:
        errors.append("quantity for browser_minute_consumed must be greater than zero")
    elif event_type == EVENT_EVIDENCE_BYTE_STORED and (
        not isinstance(quantity, int) or isinstance(quantity, bool)
    ):
        errors.append("quantity for evidence_byte_stored must be an integer")
    elif event_type in (
        EVENT_RUN_STARTED,
        EVENT_CHECK_EXECUTED,
        EVENT_EXPORT_GENERATED,
    ) and float(quantity) != 1.0:
        errors.append("quantity for {} must be 1".format(event_type))
    elif float(quantity) < 0:
        errors.append("quantity must be greater than or equal to zero")
    if event.get("unit") != EVENT_UNITS[event_type]:
        errors.append(
            "unit for {} must be {}".format(event_type, EVENT_UNITS[event_type])
        )
    timestamp = event.get("timestamp")
    try:
        parse_timestamp(timestamp)
    except (TypeError, ValueError):
        errors.append("timestamp must be an ISO-8601 timestamp")
    metadata = event.get("metadata", {})
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object")
    else:
        for field in EVENT_REQUIRED_METADATA[event_type]:
            if field not in metadata:
                errors.append(
                    "metadata.{} is required for {}".format(field, event_type)
                )
    return errors


def build_event(
    event_type: str,
    *,
    run: str,
    workspace: str = DEFAULT_WORKSPACE,
    source: str = DEFAULT_SOURCE,
    quantity: float = 1,
    unit: Optional[str] = None,
    idempotency_key: str = "",
    timestamp: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create one canonical event envelope.

    Callers normally use :class:`MeteringEmitter`, which also writes and
    de-duplicates the event.  This pure helper is useful for contract tests and
    for adapters that deliver events to another sink.
    """

    if event_type not in EVENT_TYPES:
        raise MeteringError("Unknown metering event type: {}".format(event_type))
    resolved_unit = unit or EVENT_UNITS[event_type]
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id or new_id("usage"),
        "event_type": event_type,
        "idempotency_key": str(idempotency_key or "").strip(),
        "workspace": str(workspace or "").strip(),
        "run": str(run or "").strip(),
        "quantity": quantity,
        "unit": resolved_unit,
        "timestamp": timestamp or utc_now(),
        "source": str(source or "").strip(),
        "metadata": dict(metadata or {}),
    }
    if not event["idempotency_key"]:
        event["idempotency_key"] = default_idempotency_key(
            event["run"], event_type, quantity, resolved_unit, event["metadata"]
        )
    errors = validate_event(event)
    if errors:
        raise MeteringError("Invalid metering event: {}".format("; ".join(errors)))
    return event


def default_idempotency_key(
    run: str,
    event_type: str,
    quantity: Any,
    unit: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a stable key when the caller does not supply one."""

    payload = json.dumps(
        {
            "run": str(run),
            "event_type": str(event_type),
            "quantity": quantity,
            "unit": str(unit),
            "metadata": metadata or {},
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return "{}:{}:{}".format(run, event_type, digest)


class MeteringEmitter:
    """Append canonical usage events to a local JSON Lines file sink.

    The sink is intentionally separate from ``events.jsonl`` (the operational
    run audit trail).  Metering events are a billing contract and may be
    forwarded or retained independently.
    """

    def __init__(
        self,
        root: Path,
        run_id: str,
        *,
        workspace: str = DEFAULT_WORKSPACE,
        source: str = DEFAULT_SOURCE,
        sink: Optional[Path] = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.run_id = str(run_id or "").strip()
        if not self.run_id:
            raise MeteringError("A run id is required for metering.")
        self.workspace = str(workspace or DEFAULT_WORKSPACE).strip() or DEFAULT_WORKSPACE
        self.source = str(source or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE
        self.sink = Path(sink) if sink is not None else self.root / DEFAULT_SINK
        if not self.sink.is_absolute():
            self.sink = self.root / self.sink
        self.sink = self.sink.resolve()
        self._lock = threading.Lock()
        self._seen = set()
        self._known_size = -1
        self._refresh_seen()

    # ---------------------------------------------------------------- sink io
    def _read_events(self) -> Iterable[Dict[str, Any]]:
        if not self.sink.exists():
            return []
        events: List[Dict[str, Any]] = []
        try:
            with self.sink.open("r", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        record = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(record, dict):
                        events.append(record)
        except OSError:
            return events
        return events

    def _refresh_seen(self) -> None:
        try:
            size = self.sink.stat().st_size
        except OSError:
            size = 0
        if size == self._known_size:
            return
        for record in self._read_events():
            key = record.get("idempotency_key")
            if isinstance(key, str) and key:
                self._seen.add(key)
        self._known_size = size

    def events(self) -> List[Dict[str, Any]]:
        kept: List[Dict[str, Any]] = []
        keys = set()
        for record in self._read_events():
            key = str(record.get("idempotency_key", ""))
            if key and key in keys:
                continue
            keys.add(key)
            kept.append(record)
        return kept

    # ------------------------------------------------------------------ emit
    def emit(
        self,
        event_type: str,
        quantity: float = 1,
        unit: Optional[str] = None,
        *,
        idempotency_key: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Write one event, or ignore it when the idempotency key exists."""

        resolved_unit = unit or EVENT_UNITS.get(event_type, "")
        key = str(idempotency_key or "").strip()
        if not key:
            key = default_idempotency_key(
                self.run_id, event_type, quantity, resolved_unit, metadata
            )
        with self._lock:
            self._refresh_seen()
            if key in self._seen:
                return None
            event = build_event(
                event_type,
                run=self.run_id,
                workspace=self.workspace,
                source=self.source,
                quantity=quantity,
                unit=resolved_unit,
                idempotency_key=key,
                timestamp=timestamp,
                metadata=metadata,
            )
            self._append(event)
            self._seen.add(key)
            try:
                self._known_size = self.sink.stat().st_size
            except OSError:
                self._known_size = -1
            return event

    def _append(self, event: Dict[str, Any]) -> None:
        self.sink.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n"
        try:
            with self.sink.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise MeteringError(
                "Could not write metering sink {}: {}".format(self.sink, exc)
            )

    def summary(self) -> Dict[str, Any]:
        """Return a deterministic summary for the run report."""

        counts: Dict[str, int] = {}
        for event in self.events():
            event_type = str(event.get("event_type", ""))
            counts[event_type] = counts.get(event_type, 0) + 1
        try:
            sink_display = str(self.sink.relative_to(self.root))
        except ValueError:
            sink_display = str(self.sink)
        return {
            "schema_version": SCHEMA_VERSION,
            "sink": sink_display,
            "workspace": self.workspace,
            "event_count": sum(counts.values()),
            "event_types": {key: counts[key] for key in sorted(counts)},
        }
