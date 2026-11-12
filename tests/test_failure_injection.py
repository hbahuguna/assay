"""Failure-injection tests for state, evidence, and kill-switch handling."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.errors import StateError
from harness.orchestrator import Orchestrator, RunOptions
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-repo"
PERSONAS = ROOT / "personas"


class FailureInjectionTests(unittest.TestCase):
    def test_corrupt_progress_produces_typed_error_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            Orchestrator(root, options).run()
            progress_path = root / ".harness" / "progress.json"
            original = progress_path.read_text(encoding="utf-8")
            progress_path.write_text("{not valid json", encoding="utf-8")
            store = StateStore(root)
            with self.assertRaises(StateError):
                store.load()
            # The corrupt bytes remain for inspection; the last good copy is not
            # silently overwritten by the failed read.
            self.assertEqual(progress_path.read_text(encoding="utf-8"), "{not valid json")
            # The original report remains available for recovery.
            self.assertTrue((root / ".harness" / "report.json").is_file())
            # Restore and confirm the run can be loaded again.
            progress_path.write_text(original, encoding="utf-8")
            self.assertEqual(store.load().status, "completed")

    def test_failed_evidence_write_does_not_produce_passing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )

            def fail_save_json(self, *args, **kwargs):
                raise RuntimeError("simulated evidence write failure")

            with mock.patch("harness.evidence.EvidenceStore.save_json", fail_save_json):
                try:
                    Orchestrator(root, options).run()
                except Exception:
                    pass

            report_path = root / ".harness" / "report.json"
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertNotEqual(report.get("decision", {}).get("decision"), "ship")
                self.assertEqual(report.get("confidence", {}).get("score"), 0.0)
            else:
                self.assertFalse(report_path.exists())

    def test_kill_during_evidence_collection_writes_hold_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )

            original_phase = Orchestrator._phase_evidence

            def kill_during_evidence(self, phase):
                self._trigger_kill("injected kill during evidence")
                return original_phase(self, phase)

            with mock.patch("harness.orchestrator.Orchestrator._phase_evidence", kill_during_evidence):
                state = Orchestrator(root, options).run()

            self.assertEqual(state.status, "halted")
            report = json.loads((root / ".harness" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "halted")
            self.assertEqual(report["decision"]["decision"], "hold")
            self.assertIn("kill", report["halted_reason"].lower())

    def test_kill_switch_failure_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="high",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "halted")
            report = json.loads((root / ".harness" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["decision"]["decision"], "hold")
            self.assertLessEqual(report["confidence"]["score"], 25.0)

    def test_partial_manifest_is_rejected_by_evidence_verify(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            Orchestrator(root, options).run()
            manifest_path = None
            for path in (root / ".harness" / "evidence").rglob("manifest.json"):
                manifest_path = path
            self.assertIsNotNone(manifest_path)
            # Truncate the manifest to simulate a partial write.
            data = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(data[: len(data) // 2], encoding="utf-8")
            from harness.evidence import EvidenceStore

            run_id = json.loads((root / ".harness" / "progress.json").read_text())["run_id"]
            result = EvidenceStore(root, run_id).verify()
            self.assertTrue(result["tampered"])
            self.assertFalse(result["signature_valid"])


if __name__ == "__main__":
    unittest.main()
