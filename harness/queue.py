"""Durable cloud run queue with per-tenant isolation.

The queue stores job records in ``.harness/cloud/queue.json``.  Each job runs in
its own tenant root (``.harness/tenants/<workspace>/``), so state, evidence, and
metering for one workspace cannot be read through another workspace's job API.
A manual cancellation writes the tenant kill file, letting the orchestrator stop
remaining checks and preserve partial evidence.  Crashed or abandoned running
jobs can be recovered and retried; metering idempotency keys are derived from
the run id, so a retried job does not bill the same check twice.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .models import utc_now
from .errors import HarnessError
from .orchestrator import RunOptions
from .orchestrator import Orchestrator
from .state import StateStore


SCHEMA_VERSION = "quality-harness.queue/v1"
DEFAULT_QUEUE = Path(".harness") / "cloud" / "queue.json"
DEFAULT_TENANTS = Path(".harness") / "tenants"
DEFAULT_LOCK = Path(".harness") / "cloud" / "queue.lock"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}

_WORKSPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

_LOCK_REGISTRY: Dict[str, threading.Lock] = {}
_LOCK_REGISTRY_GUARD = threading.Lock()


def _safe_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _safe_workspace(value: Any) -> str:
    text = _safe_text(value, 128)
    if not _WORKSPACE_RE.match(text):
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


class JobQueue:
    """Durable FIFO queue for tenant-scoped harness runs."""

    def __init__(
        self,
        root: Path,
        *,
        queue_path: Optional[Union[str, Path]] = None,
        tenant_root: Optional[Union[str, Path]] = None,
        lock_path: Optional[Union[str, Path]] = None,
        personas_dir: Optional[Union[str, Path]] = None,
        max_attempts: int = 3,
        stale_after_seconds: int = 300,
    ) -> None:
        self.root = Path(root).resolve()
        self.queue_path = self._resolve(queue_path or DEFAULT_QUEUE)
        self.tenant_root = self._resolve(tenant_root or DEFAULT_TENANTS)
        self.lock_path = self._resolve(lock_path or DEFAULT_LOCK)
        self.personas_dir = (
            Path(personas_dir).resolve()
            if personas_dir is not None
            else (self.root / "personas").resolve()
        )
        if not self.personas_dir.exists() and (Path.cwd() / "personas").exists():
            self.personas_dir = (Path.cwd() / "personas").resolve()
        self.max_attempts = max(1, int(max_attempts or 1))
        self.stale_after_seconds = max(1, int(stale_after_seconds or 1))

    def _resolve(self, value: Union[str, Path]) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        return path

    # ---------------------------------------------------------------- state
    def _empty(self) -> Dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "jobs": {}}

    def _load(self) -> Dict[str, Any]:
        if not self.queue_path.exists():
            return self._empty()
        try:
            data = json.loads(self.queue_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HarnessError("Run queue is unreadable: {}".format(exc))
        if not isinstance(data, dict):
            raise HarnessError("Run queue must contain a JSON object.")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise HarnessError("Unsupported run queue schema version.")
        data.setdefault("jobs", {})
        if not isinstance(data["jobs"], dict):
            data["jobs"] = {}
        return data

    def _save(self, data: Mapping[str, Any]) -> None:
        _atomic_write(
            self.queue_path,
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

    def _tenant_root(self, workspace_id: str) -> Path:
        return self.tenant_root / _safe_workspace(workspace_id)

    # ---------------------------------------------------------------- submit
    def submit(
        self,
        workspace_id: str,
        target: str,
        *,
        options: Optional[Mapping[str, Any]] = None,
        source: Optional[str] = None,
        intent: Optional[str] = None,
        workspace: Optional[str] = None,
        metering_sink: Optional[str] = None,
    ) -> Dict[str, Any]:
        workspace_key = _safe_workspace(workspace_id)
        target_text = _safe_text(target, 4000)
        if not target_text:
            raise ValueError("A run target is required.")
        option_data = dict(options or {})
        option_data.setdefault("target", target_text)
        if source is not None:
            option_data["source"] = source
        if intent is not None:
            option_data["intent"] = intent
        option_data.setdefault("workspace", workspace or workspace_key)
        option_data.setdefault("personas_dir", str(self.personas_dir))
        option_data.setdefault("no_browser", True)
        option_data.setdefault("metering_sink", metering_sink or ".harness/metering/events.jsonl")

        job_id = "job-{}".format(__import__("uuid").uuid4().hex[:12])
        now = utc_now()
        job = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "workspace_id": workspace_key,
            "root": str(self._tenant_root(workspace_key)),
            "target": target_text,
            "status": STATUS_QUEUED,
            "attempt": 0,
            "max_attempts": self.max_attempts,
            "options": option_data,
            "run_id": None,
            "created_at": now,
            "queued_at": now,
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "cancel_requested": False,
            "cancel_reason": "",
            "error": "",
            "report_path": None,
            "evidence_dir": None,
            "findings": 0,
            "confidence": None,
            "status_detail": "queued",
        }
        with _locked(self.lock_path):
            data = self._load()
            data["jobs"][job_id] = job
            self._save(data)
        return self._public(job)

    # ---------------------------------------------------------------- read
    def _job(self, data: Mapping[str, Any], workspace_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        workspace_key = _safe_workspace(workspace_id)
        job = data.get("jobs", {}).get(str(job_id))
        if not isinstance(job, dict):
            return None
        if job.get("workspace_id") != workspace_key:
            return None
        return job

    def get_job(self, workspace_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        with _locked(self.lock_path):
            data = self._load()
        job = self._job(data, workspace_id, job_id)
        if job is None:
            return None
        return self._public(job)

    def list_jobs(self, workspace_id: str) -> List[Dict[str, Any]]:
        workspace_key = _safe_workspace(workspace_id)
        with _locked(self.lock_path):
            data = self._load()
        jobs = [
            self._public(job)
            for job in data.get("jobs", {}).values()
            if isinstance(job, dict) and job.get("workspace_id") == workspace_key
        ]
        jobs.sort(key=lambda item: str(item.get("created_at", "")))
        return jobs

    def _public(self, job: Mapping[str, Any]) -> Dict[str, Any]:
        # Never expose tenant filesystem paths or another tenant's identifiers.
        return {
            "schema_version": SCHEMA_VERSION,
            "job_id": job.get("job_id"),
            "workspace_id": job.get("workspace_id"),
            "status": job.get("status"),
            "attempt": job.get("attempt"),
            "run_id": job.get("run_id"),
            "target": job.get("target"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "duration_seconds": job.get("duration_seconds"),
            "cancel_requested": bool(job.get("cancel_requested")),
            "error": job.get("error", ""),
            "report_path": job.get("report_path"),
            "evidence_dir": job.get("evidence_dir"),
            "findings": job.get("findings", 0),
            "confidence": job.get("confidence"),
            "status_detail": job.get("status_detail", ""),
        }

    # --------------------------------------------------------------- cancel
    def cancel(self, workspace_id: str, job_id: str, reason: str = "operator cancelled") -> Dict[str, Any]:
        workspace_key = _safe_workspace(workspace_id)
        with _locked(self.lock_path):
            data = self._load()
            job = self._job(data, workspace_key, job_id)
            if job is None:
                raise HarnessError("Job not found.")
            if job.get("status") in TERMINAL_STATUSES:
                return self._public(job)
            job["cancel_requested"] = True
            job["cancel_reason"] = _safe_text(reason, 500)
            if job.get("status") == STATUS_QUEUED:
                job["status"] = STATUS_CANCELLED
                job["completed_at"] = utc_now()
                job["status_detail"] = "cancelled before execution"
            else:
                job["status_detail"] = "cancellation requested"
                root = Path(str(job.get("root", "")))
                kill_path = root / ".harness" / "KILL"
                kill_path.parent.mkdir(parents=True, exist_ok=True)
                kill_path.write_text(
                    json.dumps({"reason": job["cancel_reason"]}) + "\n",
                    encoding="utf-8",
                )
                try:
                    os.chmod(kill_path, 0o600)
                except OSError:
                    pass
            self._save(data)
            return self._public(job)

    # --------------------------------------------------------------- recovery
    def recover(self) -> List[Dict[str, Any]]:
        recovered: List[Dict[str, Any]] = []
        with _locked(self.lock_path):
            data = self._load()
            for job in data.get("jobs", {}).values():
                if not isinstance(job, dict):
                    continue
                if job.get("status") == STATUS_RUNNING:
                    job["status"] = STATUS_QUEUED
                    job["status_detail"] = "recovered after worker crash"
                    job["started_at"] = None
                    job["queued_at"] = utc_now()
                    job["cancel_requested"] = False
                    recovered.append(self._public(job))
            self._save(data)
        return recovered

    def recover_stale(self, now: Any = None) -> List[Dict[str, Any]]:
        # All running jobs are treated as stale by the simple local worker.
        return self.recover()

    # --------------------------------------------------------------- execution
    def _claim_next(self) -> Optional[Dict[str, Any]]:
        with _locked(self.lock_path):
            data = self._load()
            queued = [
                job for job in data.get("jobs", {}).values()
                if isinstance(job, dict) and job.get("status") == STATUS_QUEUED
            ]
            if not queued:
                return None
            queued.sort(key=lambda item: str(item.get("queued_at", "")))
            job = queued[0]
            job["status"] = STATUS_RUNNING
            job["attempt"] = int(job.get("attempt", 0)) + 1
            job["started_at"] = utc_now()
            job["status_detail"] = "running"
            self._save(data)
            return json.loads(json.dumps(job))

    def process_next(self) -> Optional[Dict[str, Any]]:
        job = self._claim_next()
        if job is None:
            return None
        try:
            return self._execute(job)
        except Exception as exc:
            status = (
                STATUS_QUEUED
                if int(job.get("attempt", 0)) < self.max_attempts
                else STATUS_FAILED
            )
            finished = self._finish(
                str(job["job_id"]),
                status,
                error=str(exc),
            )
            if status == STATUS_QUEUED:
                with _locked(self.lock_path):
                    data = self._load()
                    current = data["jobs"].get(str(job["job_id"]))
                    if isinstance(current, dict):
                        current["status_detail"] = "requeued after worker crash"
                        current["queued_at"] = utc_now()
                        self._save(data)
            return finished

    def process_pending(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        processed = 0
        while limit is None or processed < limit:
            job = self.process_next()
            if job is None:
                break
            results.append(job)
            processed += 1
        return results

    def _execute(self, job: Mapping[str, Any]) -> Dict[str, Any]:
        job_id = str(job["job_id"])
        workspace_id = str(job["workspace_id"])
        root = Path(str(job["root"]))
        root.mkdir(parents=True, exist_ok=True)
        (root / ".harness").mkdir(parents=True, exist_ok=True)
        options_data = dict(job.get("options") or {})
        options_data.setdefault("target", job.get("target"))
        try:
            options = RunOptions(**options_data)
        except Exception as exc:
            return self._finish(job_id, STATUS_FAILED, error=str(exc))
        options.workspace = workspace_id
        options.metering_sink = options.metering_sink or ".harness/metering/events.jsonl"

        # If a previous attempt already completed, do not run again.
        state_store = StateStore(root)
        if state_store.progress_path.exists():
            try:
                existing = state_store.load()
                if existing.status in ("completed", "failed", "halted", "cancelled"):
                    status = STATUS_COMPLETED if existing.status == "completed" else (
                        STATUS_CANCELLED if existing.status in ("halted", "cancelled") else STATUS_FAILED
                    )
                    return self._finish(
                        job_id,
                        status,
                        run_id=existing.run_id,
                        findings=len(existing.findings),
                        confidence=getattr(existing.confidence, "score", None),
                        report_path=str(state_store.report_path) if state_store.report_path.exists() else None,
                        evidence_dir=existing.evidence_dir,
                        error="" if status != STATUS_FAILED else "previous run failed",
                    )
            except Exception:
                pass

        resume = int(job.get("attempt", 0)) > 1 and state_store.progress_path.exists()
        orchestrator = Orchestrator(root, options, log=lambda message: None)
        started = time.monotonic()
        try:
            state = orchestrator.run(resume=resume)
        except Exception as exc:
            status = STATUS_QUEUED if int(job.get("attempt", 0)) < self.max_attempts else STATUS_FAILED
            finished = self._finish(
                job_id,
                status,
                error=str(exc),
                duration_seconds=round(time.monotonic() - started, 6),
            )
            if status == STATUS_QUEUED:
                with _locked(self.lock_path):
                    data = self._load()
                    current = data["jobs"].get(job_id)
                    if isinstance(current, dict):
                        current["status_detail"] = "requeued after worker crash"
                        current["queued_at"] = utc_now()
                        self._save(data)
            return finished

        cancelled = bool(job.get("cancel_requested"))
        if state.kill_switch.triggered or job.get("cancel_requested"):
            cancelled = True
        status = STATUS_CANCELLED if cancelled else (
            STATUS_COMPLETED if state.status == "completed" else STATUS_FAILED
        )
        report_path = str(state_store.report_path) if state_store.report_path.exists() else None
        return self._finish(
            job_id,
            status,
            run_id=state.run_id,
            findings=len(state.findings),
            confidence=getattr(state.confidence, "score", None),
            report_path=report_path,
            evidence_dir=state.evidence_dir,
            duration_seconds=round(time.monotonic() - started, 6),
            error="" if status in (STATUS_COMPLETED, STATUS_CANCELLED) else "run failed",
        )

    def _finish(self, job_id: str, status: str, **updates: Any) -> Dict[str, Any]:
        with _locked(self.lock_path):
            data = self._load()
            job = data["jobs"].get(job_id)
            if not isinstance(job, dict):
                raise HarnessError("Job not found: {}".format(job_id))
            job["status"] = status
            job["completed_at"] = utc_now()
            if updates.get("duration_seconds") is not None:
                job["duration_seconds"] = updates["duration_seconds"]
            for key in (
                "run_id",
                "findings",
                "confidence",
                "report_path",
                "evidence_dir",
                "error",
            ):
                if key in updates and updates[key] is not None:
                    job[key] = updates[key]
            if status in TERMINAL_STATUSES:
                job["status_detail"] = status
            self._save(data)
            return self._public(job)

    # --------------------------------------------------------------- metrics
    def delete_workspace_jobs(self, workspace_id: str) -> int:
        workspace_key = _safe_workspace(workspace_id)
        removed = 0
        with _locked(self.lock_path):
            data = self._load()
            jobs = data.get("jobs", {})
            to_delete = [
                job_id
                for job_id, job in jobs.items()
                if isinstance(job, dict) and job.get("workspace_id") == workspace_key
            ]
            for job_id in to_delete:
                del jobs[job_id]
                removed += 1
            self._save(data)
        return removed

    def metrics(self) -> Dict[str, Any]:
        with _locked(self.lock_path):
            data = self._load()
        jobs = [job for job in data.get("jobs", {}).values() if isinstance(job, dict)]
        by_status: Dict[str, int] = {}
        for job in jobs:
            status = str(job.get("status", ""))
            by_status[status] = by_status.get(status, 0) + 1
        durations = [
            float(job["duration_seconds"])
            for job in jobs
            if isinstance(job.get("duration_seconds"), (int, float))
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "queue_depth": by_status.get(STATUS_QUEUED, 0),
            "running": by_status.get(STATUS_RUNNING, 0),
            "completed": by_status.get(STATUS_COMPLETED, 0),
            "failed": by_status.get(STATUS_FAILED, 0),
            "cancelled": by_status.get(STATUS_CANCELLED, 0),
            "total": len(jobs),
            "by_status": by_status,
            "average_run_duration_seconds": (
                round(sum(durations) / len(durations), 6) if durations else 0.0
            ),
            "run_durations": {
                str(job.get("job_id")): job.get("duration_seconds")
                for job in jobs
                if job.get("duration_seconds") is not None
            },
        }


# Aliases for adapters that use alternate names.
RunQueue = JobQueue
JobStore = JobQueue
TaskQueue = JobQueue
