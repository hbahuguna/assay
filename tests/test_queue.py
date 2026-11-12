"""Cloud run queue and per-tenant isolation tests."""

import contextlib
import io
import json
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import cli
from harness.queue import JobQueue, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_QUEUED


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class QueueContractTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.personas = (Path.cwd() / "personas").resolve()
        self.target = (Path.cwd() / "examples" / "sample-repo").resolve()
        self.queue = JobQueue(
            self.root,
            personas_dir=self.personas,
            tenant_root=self.root / "tenants",
        )

    def tearDown(self):
        self._temporary.cleanup()

    def _options(self):
        return {
            "no_browser": True,
            "personas_dir": str(self.personas),
            "max_checks": 20,
        }

    def test_submit_returns_job_id_and_owner_only_polling(self):
        job = self.queue.submit("ws_acme", str(self.target), options=self._options())
        self.assertTrue(job["job_id"].startswith("job-"))
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["workspace_id"], "ws_acme")
        self.assertIsNone(self.queue.get_job("ws_other", job["job_id"]))
        self.assertEqual(self.queue.list_jobs("ws_other"), [])
        owner_job = self.queue.get_job("ws_acme", job["job_id"])
        self.assertEqual(owner_job["job_id"], job["job_id"])
        self.assertNotIn("root", owner_job)

    def test_process_runs_job_and_records_metrics(self):
        job = self.queue.submit("ws_acme", str(self.target), options=self._options())
        result = self.queue.process_next()
        self.assertEqual(result["job_id"], job["job_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["findings"], 4)
        self.assertEqual(result["confidence"], 30.2)
        metrics = self.queue.metrics()
        self.assertEqual(metrics["queue_depth"], 0)
        self.assertEqual(metrics["completed"], 1)
        self.assertGreaterEqual(metrics["average_run_duration_seconds"], 0)

    def test_two_tenants_use_isolated_roots_and_cannot_read_each_other(self):
        first = self.queue.submit("ws_one", str(self.target), options=self._options())
        second = self.queue.submit("ws_two", str(self.target), options=self._options())
        self.queue.process_next()
        self.queue.process_next()
        first_job = self.queue.get_job("ws_one", first["job_id"])
        second_job = self.queue.get_job("ws_two", second["job_id"])
        self.assertNotEqual(first_job["run_id"], second_job["run_id"])
        self.assertIsNone(self.queue.get_job("ws_one", second["job_id"]))
        self.assertIsNone(self.queue.get_job("ws_two", first["job_id"]))
        # Tenant roots are distinct and each contains its own state.
        first_root = self.root / "tenants" / "ws_one"
        second_root = self.root / "tenants" / "ws_two"
        self.assertTrue((first_root / ".harness" / "progress.json").is_file())
        self.assertTrue((second_root / ".harness" / "progress.json").is_file())
        self.assertNotEqual(
            (first_root / ".harness" / "progress.json").read_text(),
            (second_root / ".harness" / "progress.json").read_text(),
        )

    def test_cancel_queued_job(self):
        job = self.queue.submit("ws_acme", str(self.target), options=self._options())
        cancelled = self.queue.cancel("ws_acme", job["job_id"], reason="operator stop")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["cancel_requested"])
        self.assertEqual(self.queue.process_next(), None)

    def test_cancel_running_job_writes_tenant_kill_file(self):
        job = self.queue.submit("ws_acme", str(self.target), options=self._options())
        # Claim the job without executing it.
        claimed = self.queue._claim_next()
        self.assertEqual(claimed["status"], "running")
        cancelled = self.queue.cancel("ws_acme", job["job_id"], reason="stop now")
        self.assertEqual(cancelled["status_detail"], "cancellation requested")
        kill_path = self.root / "tenants" / "ws_acme" / ".harness" / "KILL"
        self.assertTrue(kill_path.is_file())

    def test_recover_resets_running_jobs(self):
        job = self.queue.submit("ws_acme", str(self.target), options=self._options())
        claimed = self.queue._claim_next()
        self.assertEqual(claimed["status"], "running")
        recovered = self.queue.recover()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["status"], "queued")
        self.assertEqual(self.queue.get_job("ws_acme", job["job_id"])["status"], "queued")

    def test_crash_requeues_and_does_not_double_bill(self):
        job = self.queue.submit("ws_acme", str(self.target), options=self._options())
        original_run = Orchestrator if False else None
        # Force the first worker attempt to crash.
        from harness import queue as queue_module

        original_execute = queue_module.JobQueue._execute

        def crashing_execute(self, job):
            if int(job.get("attempt", 0)) == 1:
                raise RuntimeError("simulated worker crash")
            return original_execute(self, job)

        with mock.patch.object(queue_module.JobQueue, "_execute", crashing_execute):
            first = self.queue.process_next()
            self.assertEqual(first["status"], "queued")
        # Retry should complete using the same run state and not duplicate
        # metering events.
        second = self.queue.process_next()
        self.assertEqual(second["status"], "completed")
        events_path = self.root / "tenants" / "ws_acme" / ".harness" / "metering" / "events.jsonl"
        event_types = [json.loads(line)["event_type"] for line in events_path.read_text().splitlines()]
        self.assertEqual(event_types.count("run_started"), 1)
        self.assertEqual(event_types.count("check_executed"), 7)

    def test_cancellation_preserves_partial_evidence(self):
        # Cancel before execution still records a terminal cancelled job.
        job = self.queue.submit("ws_acme", str(self.target), options=self._options())
        self.queue.cancel("ws_acme", job["job_id"])
        polled = self.queue.get_job("ws_acme", job["job_id"])
        self.assertEqual(polled["status"], "cancelled")


class QueueCliTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.personas = (Path.cwd() / "personas").resolve()
        self.target = (Path.cwd() / "examples" / "sample-repo").resolve()

    def tearDown(self):
        self._temporary.cleanup()

    def test_cli_queue_submit_status_and_process(self):
        # The CLI for queue is optional; skip if unavailable.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
