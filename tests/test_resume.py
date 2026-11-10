"""Crash-safe checkpoint and resume contract tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.errors import StateError
from harness.models import CheckStatus
from harness.orchestrator import Orchestrator, RunOptions
from harness.outcomes import CheckOutcome
from harness.state import StateStore
from harness.static_analysis import StaticCheckRunner

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"
OTHER_TARGET = ROOT / "examples" / "demo-web"

COMPLETED_STATUSES = {
    CheckStatus.PASSED.value,
    CheckStatus.FAILED.value,
    CheckStatus.BLOCKED.value,
    CheckStatus.SKIPPED.value,
}
RERUN_STATUSES = {
    CheckStatus.PLANNED.value,
    CheckStatus.RUNNING.value,
    CheckStatus.ERROR.value,
}


class SimulatedCrash(RuntimeError):
    """Raised by the test runner to simulate the process dying mid-phase."""


class ResumeTests(unittest.TestCase):
    def test_crash_after_completed_checks_resumes_only_unfinished_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
            )
            original_run = StaticCheckRunner.run
            crash_calls = []

            def crash_runner(runner, check):
                if len(crash_calls) >= 3:
                    raise SimulatedCrash("simulated process kill before the fourth check")
                if not crash_calls:
                    crash_calls.append(check.id)
                    return CheckOutcome(
                        status=CheckStatus.ERROR.value,
                        actual="Simulated check error before the crash.",
                    )
                outcome = original_run(runner, check)
                crash_calls.append(check.id)
                return outcome

            with patch.object(StaticCheckRunner, "run", new=crash_runner):
                with self.assertRaises(SimulatedCrash):
                    Orchestrator(root, options).run()

            store = StateStore(root)
            crashed = store.load()
            completed = [
                check for check in crashed.checks if check.status in COMPLETED_STATUSES
            ]
            errored = [
                check for check in crashed.checks if check.status == CheckStatus.ERROR.value
            ]
            running = [
                check for check in crashed.checks if check.status == CheckStatus.RUNNING.value
            ]
            planned = [
                check for check in crashed.checks if check.status == CheckStatus.PLANNED.value
            ]

            self.assertEqual(crashed.status, "failed")
            self.assertEqual(len(completed), 2)
            self.assertEqual(len(errored), 1)
            self.assertEqual(len(running), 1)
            self.assertGreater(len(planned), 0)

            completed_ids = {check.id for check in completed}
            expected_rerun = {
                check.id for check in crashed.checks if check.status in RERUN_STATUSES
            }
            self.assertEqual(
                expected_rerun,
                {check.id for check in crashed.checks if check.id not in completed_ids},
            )

            completed_findings = {
                finding.id: finding.to_dict()
                for finding in crashed.findings
                if finding.check_id in completed_ids
            }
            self.assertGreaterEqual(len(completed_findings), 1)
            completed_evidence = {
                check.id: list(check.evidence) for check in completed
            }

            checkpoint_path = store.evidence_root / crashed.run_id / "manifest.json"
            self.assertTrue(checkpoint_path.exists())
            checkpoint_manifest = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint_entries = [
                entry
                for entry in checkpoint_manifest.get("evidence", [])
                if entry.get("check_id") in completed_ids
            ]
            self.assertEqual(len(checkpoint_entries), len(completed_ids))

            resumed_calls = []

            def resume_runner(runner, check):
                resumed_calls.append(check.id)
                return original_run(runner, check)

            with patch.object(StaticCheckRunner, "run", new=resume_runner):
                resumed = Orchestrator(root, options).run(resume=True)

            self.assertEqual(resumed.status, "completed")
            self.assertEqual(set(resumed_calls), expected_rerun)
            self.assertFalse(completed_ids & set(resumed_calls))
            self.assertIn(errored[0].id, resumed_calls)
            self.assertIn(running[0].id, resumed_calls)
            self.assertIn(planned[0].id, resumed_calls)

            for check in completed:
                self.assertEqual(resumed.check(check.id).evidence, completed_evidence[check.id])

            resumed_findings = {finding.id: finding.to_dict() for finding in resumed.findings}
            for finding_id, before in completed_findings.items():
                self.assertIn(finding_id, resumed_findings)
                self.assertEqual(resumed_findings[finding_id], before)

            final_manifest = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            final_entries = [
                entry
                for entry in final_manifest.get("evidence", [])
                if entry.get("check_id") in completed_ids
            ]
            self.assertEqual(final_entries, checkpoint_entries)
            self.assertEqual(
                len(final_manifest.get("evidence", [])),
                len({entry.get("path") for entry in final_manifest.get("evidence", [])}),
            )
            self.assertEqual(
                len(resumed.findings),
                len({finding.fingerprint for finding in resumed.findings}),
            )

    def test_resume_rejects_a_different_target_with_an_actionable_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Orchestrator(
                root,
                RunOptions(
                    target=str(SAMPLE),
                    personas_dir=str(PERSONAS),
                    no_browser=True,
                ),
            ).run()

            with self.assertRaises(StateError) as raised:
                Orchestrator(
                    root,
                    RunOptions(
                        target=str(OTHER_TARGET),
                        personas_dir=str(PERSONAS),
                        no_browser=True,
                    ),
                ).run(resume=True)

            message = str(raised.exception)
            self.assertIn("Resume target does not match", message)
            self.assertIn(str(SAMPLE.resolve()), message)
            self.assertIn(str(OTHER_TARGET.resolve()), message)


if __name__ == "__main__":
    unittest.main()
