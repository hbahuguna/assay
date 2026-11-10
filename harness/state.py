"""Durable run state and the manual kill switch.

Everything lives below ``<project>/.harness``.  Writes are atomic so a crash
cannot leave a half-written ``progress.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import StateError
from .models import RunState, utc_now


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.harness_dir = self.root / ".harness"
        self.progress_path = self.harness_dir / "progress.json"
        self.report_path = self.harness_dir / "report.json"
        self.kill_path = self.harness_dir / "KILL"
        self.events_path = self.harness_dir / "events.jsonl"
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
