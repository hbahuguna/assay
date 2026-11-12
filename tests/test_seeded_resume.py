"""Seeded deterministic id sequence resume tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.orchestrator import Orchestrator, RunOptions

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-repo"
PERSONAS = ROOT / "personas"
FROZEN = "2026-01-02T03:04:05Z"


def make_options(root, seed=1234, clock_mode="frozen", frozen_clock=FROZEN, **kwargs):
    return RunOptions(
        target=str(SAMPLE),
        personas_dir=str(PERSONAS),
        no_browser=True,
        kill_on="never",
        seed=seed,
        clock_mode=clock_mode,
        frozen_clock=frozen_clock if clock_mode == "frozen" else None,
        **kwargs,
    )


def run_fresh(root, seed=1234):
    options = make_options(root, seed=seed)
    return Orchestrator(root, options).run()


class SeededResumeTests(unittest.TestCase):
    def test_progress_and_report_record_id_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = run_fresh(root)
            self.assertIsInstance(state.options.get("id_sequence"), int)
            report = json.loads((root / ".harness" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["options"].get("id_sequence"), state.options["id_sequence"])

    def test_resumed_seeded_run_continues_id_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fresh_state, fresh_report = None, None
            with tempfile.TemporaryDirectory() as fresh_dir:
                fresh_root = Path(fresh_dir)
                fresh_state = run_fresh(fresh_root)
                fresh_ids = [finding.id for finding in fresh_state.findings]

            # Crash after partial execution.
            original_execute = Orchestrator._phase_execution

            def crash(self, phase):
                raise RuntimeError("simulated crash")

            Orchestrator._phase_execution = crash
            try:
                try:
                    Orchestrator(root, make_options(root)).run()
                except RuntimeError:
                    pass
            finally:
                Orchestrator._phase_execution = original_execute

            partial_state = __import__("json").loads((root / ".harness" / "progress.json").read_text())
            self.assertGreater(partial_state["options"].get("id_sequence", 0), 0)

            resumed_state = Orchestrator(root, make_options(root)).run(resume=True)
            resumed_ids = [finding.id for finding in resumed_state.findings]
            self.assertEqual(resumed_ids, fresh_ids)
            self.assertEqual(resumed_state.run_id, fresh_state.run_id)

    def test_resume_restores_saved_determinism_options(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = run_fresh(root)
            # Attempt resume with a different seed/clock; saved options win.
            state = Orchestrator(
                root,
                make_options(root, seed=999, clock_mode="real"),
            ).run(resume=True)
            self.assertEqual(state.seed, original.seed)
            self.assertEqual(state.clock_mode, original.clock_mode)
            self.assertEqual(state.run_id, original.run_id)

    def test_existing_finding_ids_remain_stable_after_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_state = run_fresh(root)
            original_ids = [finding.id for finding in original_state.findings]
            original_report = (root / ".harness" / "report.json").read_bytes()
            evidence = {
                path: path.read_bytes()
                for path in (root / ".harness" / "evidence").rglob("*")
                if path.is_file()
            }

            # Resume a completed run does not rewrite finding ids or evidence.
            Orchestrator(root, make_options(root)).run(resume=True)
            resumed_state = __import__("json").loads((root / ".harness" / "progress.json").read_text()) and None
            resumed_report = (root / ".harness" / "report.json").read_bytes()
            self.assertEqual([f.id for f in original_state.findings], original_ids)
            self.assertEqual(resumed_report, original_report)
            for path, content in evidence.items():
                self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
