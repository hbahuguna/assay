"""Durable run state and the manual kill switch.

Everything lives below ``<project>/.harness``.  Writes are atomic so a crash
cannot leave a half-written ``progress.json``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .determinism import parse_timestamp
from .errors import StateError
from .models import RunState, Severity, utc_now
from .report import decision_for_state


HISTORY_SCHEMA = "quality-harness.history/v1"


def _history_sort_key(entry: Dict[str, Any]) -> tuple:
    return (
        str(
            entry.get("completed_at")
            or entry.get("started_at")
            or entry.get("recorded_at")
            or ""
        ),
        str(entry.get("run_id", "")),
    )


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.harness_dir = self.root / ".harness"
        self.progress_path = self.harness_dir / "progress.json"
        self.report_path = self.harness_dir / "report.json"
        self.kill_path = self.harness_dir / "KILL"
        self.events_path = self.harness_dir / "events.jsonl"
        self.history_path = self.harness_dir / "history.json"
        self.evidence_root = self.harness_dir / "evidence"

    def ensure(self) -> None:
        self.harness_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_root.mkdir(parents=True, exist_ok=True)
        gitignore = self.harness_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    def exists(self) -> bool:
        return self.progress_path.exists()

    def load(self) -> RunState:
        if not self.progress_path.exists():
            raise StateError(
                "No run state found at {}. Start a run with `python -m harness run --target ...`.".format(
                    self.progress_path
                )
            )
        try:
            data = json.loads(self.progress_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise StateError("progress.json is not valid JSON: {}".format(exc))
        if not isinstance(data, dict):
            raise StateError("progress.json must contain a JSON object")
        return RunState.from_dict(data)

    def save(self, state: RunState) -> None:
        self.ensure()
        state.touch()
        payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False)
        self._atomic_write(self.progress_path, payload)

    def save_report(self, report: Dict[str, Any]) -> Path:
        self.ensure()
        payload = json.dumps(report, indent=2, ensure_ascii=False)
        self._atomic_write(self.report_path, payload)
        return self.report_path

    def load_report(self) -> Dict[str, Any]:
        if not self.report_path.exists():
            raise StateError("No report found at {}".format(self.report_path))
        return json.loads(self.report_path.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- history
    def load_history(self) -> Dict[str, Any]:
        if not self.history_path.exists():
            return {"schema_version": HISTORY_SCHEMA, "runs": []}
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StateError("Could not read history.json: {}".format(exc))
        except ValueError as exc:
            raise StateError("history.json is not valid JSON: {}".format(exc))
        if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
            raise StateError("history.json must contain a 'runs' array")
        if data.get("schema_version", HISTORY_SCHEMA) != HISTORY_SCHEMA:
            raise StateError(
                "Unsupported history schema version: {}".format(data.get("schema_version"))
            )
        data["runs"] = sorted(
            (item for item in data.get("runs", []) if isinstance(item, dict)),
            key=_history_sort_key,
            reverse=True,
        )
        return data

    def save_history(self, history: Dict[str, Any]) -> Path:
        self.ensure()
        payload = json.dumps(history, indent=2, ensure_ascii=False)
        self._atomic_write(self.history_path, payload)
        return self.history_path

    def record_run(self, state: RunState, report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        entry = self._history_entry(state, report)
        history = self.load_history()
        runs = [
            item
            for item in history.get("runs", [])
            if isinstance(item, dict) and item.get("run_id") != state.run_id
        ]
        runs.append(entry)
        runs.sort(key=_history_sort_key, reverse=True)
        self.save_history({"schema_version": HISTORY_SCHEMA, "runs": runs})
        return entry

    def prune(
        self,
        keep_runs: Optional[int] = None,
        older_than_days: Optional[int] = None,
        dry_run: bool = False,
        now: Optional[str] = None,
    ) -> Dict[str, Any]:
        if keep_runs is not None and keep_runs < 0:
            raise StateError("keep_runs must be zero or greater")
        if older_than_days is not None and older_than_days < 0:
            raise StateError("older_than_days must be zero or greater")

        history = self.load_history()
        runs = [item for item in history.get("runs", []) if isinstance(item, dict)]
        runs.sort(key=_history_sort_key, reverse=True)
        if not runs:
            return {"removed": [], "retained": []}

        protected = self._protected_run_ids(runs)
        try:
            now_dt = parse_timestamp(now or utc_now())
        except ValueError as exc:
            raise StateError("now must be an ISO-8601 timestamp: {}".format(exc))
        retained: List[Dict[str, Any]] = []
        removed: List[Dict[str, Any]] = []
        for index, entry in enumerate(runs):
            run_id = str(entry.get("run_id", ""))
            keep = True
            if keep_runs is not None and index >= keep_runs:
                keep = False
            if older_than_days is not None:
                timestamp = (
                    entry.get("completed_at")
                    or entry.get("started_at")
                    or entry.get("recorded_at")
                )
                if timestamp:
                    try:
                        age_seconds = (now_dt - parse_timestamp(timestamp)).total_seconds()
                    except ValueError:
                        age_seconds = None
                    if age_seconds is not None and age_seconds > older_than_days * 86400:
                        keep = False
            if run_id in protected:
                keep = True
            if keep:
                retained.append(entry)
            else:
                removed.append(entry)

        if not dry_run:
            # Write the index first so a crash never leaves history claiming
            # that an already-deleted evidence directory still exists.
            self.save_history({"schema_version": HISTORY_SCHEMA, "runs": retained})
            for entry in removed:
                self._remove_evidence(entry.get("evidence_path"))

        return {
            "removed": [str(entry.get("run_id", "")) for entry in removed],
            "retained": [str(entry.get("run_id", "")) for entry in retained],
        }

    @staticmethod
    def _protected_run_ids(runs: List[Dict[str, Any]]) -> set:
        protected = set()
        if runs:
            protected.add(str(runs[0].get("run_id", "")))
        latest_completed_seen = False
        for entry in runs:
            if entry.get("status") == "halted":
                protected.add(str(entry.get("run_id", "")))
            if not latest_completed_seen and entry.get("status") == "completed":
                protected.add(str(entry.get("run_id", "")))
                latest_completed_seen = True
        return {run_id for run_id in protected if run_id}

    def _history_entry(
        self, state: RunState, report: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        finding_counts = {severity.value: 0 for severity in Severity}
        for finding in state.findings:
            finding_counts[finding.severity] = finding_counts.get(finding.severity, 0) + 1
        decision = decision_for_state(state)
        if report:
            decision = dict(report.get("decision") or decision)
        evidence_path = state.evidence_dir or str(
            Path(".harness") / "evidence" / state.run_id
        )
        return {
            "run_id": state.run_id,
            "workflow_id": state.workflow_id,
            "workflow_version": state.workflow_version,
            "target": state.target.to_dict(),
            "status": state.status,
            "score": state.confidence.score,
            "decision": decision.get("decision", "review"),
            "finding_counts": finding_counts,
            "finding_fingerprints": sorted({finding.fingerprint for finding in state.findings if finding.fingerprint}),
            "evidence_path": evidence_path,
            "started_at": state.started_at,
            "completed_at": state.completed_at,
            "recorded_at": utc_now(),
        }

    def _remove_evidence(self, relative_path: Any) -> None:
        if not relative_path:
            return
        candidate = Path(str(relative_path))
        if candidate.is_absolute():
            raise StateError("Refusing to remove an absolute evidence path: {}".format(candidate))
        evidence_root = self.evidence_root.resolve()
        target = (self.root / candidate).resolve()
        try:
            target.relative_to(evidence_root)
        except ValueError:
            raise StateError(
                "Refusing to remove evidence outside .harness/evidence: {}".format(candidate)
            )
        if target == evidence_root:
            raise StateError("Refusing to remove the evidence root")
        if not target.exists():
            return
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise StateError("Could not remove evidence {}: {}".format(target, exc))

    def clear(self) -> None:
        if self.progress_path.exists():
            try:
                self.progress_path.unlink()
            except OSError as exc:
                raise StateError("Could not remove progress.json: {}".format(exc))

    def request_kill(self, reason: str) -> None:
        self.ensure()
        self.kill_path.write_text(
            json.dumps({"reason": reason, "requested_at": utc_now()}),
            encoding="utf-8",
        )

    def kill_request(self) -> Optional[str]:
        if not self.kill_path.exists():
            return None
        try:
            raw = self.kill_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "Kill file exists but could not be read"
        if not raw:
            return "Manual kill requested"
        try:
            data = json.loads(raw)
            return str(data.get("reason") or "Manual kill requested")
        except ValueError:
            return raw

    def clear_kill(self) -> None:
        if self.kill_path.exists():
            try:
                self.kill_path.unlink()
            except OSError:
                pass

    def append_event(self, event: Dict[str, Any]) -> None:
        self.ensure()
        record = dict(event)
        record.setdefault("timestamp", utc_now())
        try:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Audit logging must never take down a run.
            pass

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, str(path))
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass


SUPPRESSION_SCHEMA = "quality-harness.suppressions/v1"


class SuppressionStore:
    """Durable finding suppressions with owner, reason, ticket, and expiry."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.harness_dir = self.root / ".harness"
        self.path = self.harness_dir / "suppressions.json"
        self.events_path = self.harness_dir / "events.jsonl"

    def ensure(self) -> None:
        self.harness_dir.mkdir(parents=True, exist_ok=True)

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _parse_expiry(self, value: str) -> datetime:
        text = str(value or "").strip()
        if not text:
            raise StateError("Suppression expiry is required.")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise StateError("Suppression expiry must be an ISO-8601 timestamp: {}".format(exc))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StateError("Could not read suppressions.json: {}".format(exc))
        if not isinstance(data, dict):
            raise StateError("suppressions.json must contain a JSON object")
        if data.get("schema_version", SUPPRESSION_SCHEMA) != SUPPRESSION_SCHEMA:
            raise StateError("Unsupported suppression schema version: {}".format(data.get("schema_version")))
        records = data.get("suppressions", [])
        if not isinstance(records, list):
            raise StateError("suppressions must be an array")
        return [record for record in records if isinstance(record, dict)]

    def _save(self, records: List[Dict[str, Any]]) -> None:
        self.ensure()
        payload = json.dumps(
            {"schema_version": SUPPRESSION_SCHEMA, "suppressions": records},
            indent=2,
            ensure_ascii=False,
        )
        StateStore(self.root)._atomic_write(self.path, payload)

    def _append_event(self, event: Dict[str, Any]) -> None:
        self.ensure()
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def add(
        self,
        fingerprint: str,
        owner: str,
        reason: str,
        ticket: str = "",
        expires_at: str = "",
    ) -> Dict[str, Any]:
        fingerprint = str(fingerprint or "").strip()
        owner = str(owner or "").strip()
        reason = str(reason or "").strip()
        ticket = str(ticket or "").strip()
        if not fingerprint:
            raise StateError("Suppression fingerprint is required.")
        if not owner:
            raise StateError("Suppression owner is required.")
        if not reason:
            raise StateError("Suppression reason is required.")
        expiry = self._parse_expiry(expires_at)
        if expiry <= self._now():
            raise StateError("Suppression expiry must be in the future.")

        record = {
            "fingerprint": fingerprint,
            "owner": owner,
            "reason": reason,
            "ticket": ticket,
            "expires_at": expiry.isoformat().replace("+00:00", "Z"),
            "created_at": utc_now(),
        }
        records = [item for item in self.load() if item.get("fingerprint") != fingerprint]
        records.append(record)
        self._save(records)
        self._append_event(
            {
                "event": "suppression_added",
                "fingerprint": fingerprint,
                "owner": owner,
                "ticket": ticket,
                "expires_at": record["expires_at"],
                "reason": reason,
            }
        )
        return record

    def remove(self, fingerprint: str) -> Dict[str, Any]:
        fingerprint = str(fingerprint or "").strip()
        if not fingerprint:
            raise StateError("Suppression fingerprint is required.")
        records = self.load()
        removed = [item for item in records if item.get("fingerprint") == fingerprint]
        if not removed:
            raise StateError("No suppression found for fingerprint: {}".format(fingerprint))
        records = [item for item in records if item.get("fingerprint") != fingerprint]
        self._save(records)
        self._append_event(
            {
                "event": "suppression_removed",
                "fingerprint": fingerprint,
            }
        )
        return removed[-1]

    def active_and_expired(self, now: Optional[datetime] = None) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
        now = now or self._now()
        active: Dict[str, Dict[str, Any]] = {}
        expired: List[str] = []
        for record in self.load():
            fingerprint = str(record.get("fingerprint", ""))
            if not fingerprint:
                continue
            try:
                expires = self._parse_expiry(str(record.get("expires_at", "")))
            except StateError:
                expired.append(fingerprint)
                continue
            if expires > now:
                active[fingerprint] = record
            else:
                expired.append(fingerprint)
        return active, expired
