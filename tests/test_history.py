"""Run history, status --all, and retention pruning tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import cli
from harness.orchestrator import Orchestrator, RunOptions
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"


def run_once(root: Path, seed: int, clock: str, kill_on: str = "critical"):
    options = RunOptions(
        target=str(SAMPLE),
        personas_dir=str(PERSONAS),
        no_browser=True,
        seed=seed,
        clock_mode="frozen",
        frozen_clock=clock,
        kill_on=kill_on,
    )
    return Orchestrator(root, options, log=lambda message: None).run()


def run_cli(root: Path, clock: str, seed: int, keep_runs=None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    command = [
        "run",
        "--target",
        str(SAMPLE),
        "--no-browser",
        "--quiet",
        "--root",
        str(root),
        "--personas-dir",
        str(PERSONAS),
        "--seed",
        str(seed),
        "--clock-mode",
        "frozen",
        "--frozen-clock",
        clock,
    ]
    if keep_runs is not None:
        command.extend(["--keep-runs", str(keep_runs)])
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(command)
    return code, json.loads(stdout.getvalue())


class HistoryTests(unittest.TestCase):
    def test_history_records_runs_and_status_all_lists_newest_first(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            states = [
                run_once(root, index, "2026-01-01T00:00:0{}Z".format(index))
                for index in range(1, 4)
            ]

            history = StateStore(root).load_history()
            self.assertEqual(
                [entry["run_id"] for entry in history["runs"]],
                [states[2].run_id, states[1].run_id, states[0].run_id],
            )
            for entry in history["runs"]:
                self.assertIn("target", entry)
                self.assertIn("status", entry)
                self.assertIn("score", entry)
                self.assertIn("decision", entry)
                self.assertIn("finding_counts", entry)
                self.assertIn("evidence_path", entry)
                self.assertTrue((root / entry["evidence_path"]).is_dir())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["status", "--all", "--root", str(root)])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["count"], 3)
        self.assertEqual(
            [entry["run_id"] for entry in payload["runs"]],
            [states[2].run_id, states[1].run_id, states[0].run_id],
        )

    def test_keep_runs_prunes_evidence_and_history_but_retains_latest_and_halted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = run_once(root, 1, "2026-01-01T00:00:01Z")
            halted = run_once(root, 2, "2026-01-01T00:00:02Z", kill_on="high")
            third = run_once(root, 3, "2026-01-01T00:00:03Z")
            fourth = run_once(root, 4, "2026-01-01T00:00:04Z")
            self.assertEqual(halted.status, "halted")

            result = StateStore(root).prune(keep_runs=2)

            self.assertEqual(set(result["removed"]), {first.run_id})
            self.assertEqual(
                set(result["retained"]),
                {halted.run_id, third.run_id, fourth.run_id},
            )
            self.assertFalse((root / first.evidence_dir).exists())
            self.assertTrue((root / halted.evidence_dir).exists())
            self.assertTrue((root / third.evidence_dir).exists())
            self.assertTrue((root / fourth.evidence_dir).exists())
            history = StateStore(root).load_history()
            self.assertEqual(
                {entry["run_id"] for entry in history["runs"]},
                {halted.run_id, third.run_id, fourth.run_id},
            )
            self.assertTrue((root / ".harness" / "progress.json").exists())
            self.assertTrue((root / ".harness" / "report.json").exists())

    def test_older_than_days_prunes_old_runs_but_retains_halted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_halted = run_once(root, 1, "2020-01-01T00:00:00Z", kill_on="high")
            old_completed = run_once(root, 2, "2020-01-02T00:00:00Z")
            latest = run_once(root, 3, "2026-01-10T00:00:00Z")

            result = StateStore(root).prune(
                older_than_days=5, now="2026-01-11T00:00:00Z"
            )

            self.assertIn(old_completed.run_id, result["removed"])
            self.assertNotIn(old_halted.run_id, result["removed"])
            self.assertNotIn(latest.run_id, result["removed"])
            self.assertFalse((root / old_completed.evidence_dir).exists())
            self.assertTrue((root / old_halted.evidence_dir).exists())
            self.assertTrue((root / latest.evidence_dir).exists())

    def test_prune_dry_run_does_not_change_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = run_once(root, 1, "2026-01-01T00:00:01Z")
            run_once(root, 2, "2026-01-01T00:00:02Z")

            result = StateStore(root).prune(keep_runs=1, dry_run=True)

            self.assertIn(first.run_id, result["removed"])
            self.assertTrue((root / first.evidence_dir).exists())
            self.assertEqual(len(StateStore(root).load_history()["runs"]), 2)

    def test_run_keep_runs_cli_prunes_after_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, first_report = run_cli(root, "2026-01-01T00:00:01Z", 1, keep_runs=1)
            _, second_report = run_cli(root, "2026-01-01T00:00:02Z", 2, keep_runs=1)

            history = StateStore(root).load_history()
            self.assertEqual(
                [entry["run_id"] for entry in history["runs"]],
                [second_report["run_id"]],
            )
            self.assertFalse(
                (root / ".harness" / "evidence" / first_report["run_id"]).exists()
            )
            self.assertTrue(
                (root / ".harness" / "evidence" / second_report["run_id"]).exists()
            )

    def test_prune_cli_reports_removed_and_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = run_once(root, 1, "2026-01-01T00:00:01Z")
            second = run_once(root, 2, "2026-01-01T00:00:02Z")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["prune", "--root", str(root), "--keep-runs", "1"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "pruned")
        self.assertIn(first.run_id, payload["removed"])
        self.assertIn(second.run_id, payload["retained"])

    def test_status_all_with_no_history(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["status", "--all", "--root", directory])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["runs"], [])

    def test_failed_run_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                seed=1,
                clock_mode="frozen",
                frozen_clock="2026-01-01T00:00:01Z",
            )
            orchestrator = Orchestrator(root, options, log=lambda message: None)
            with patch.object(
                Orchestrator, "_finalize_run", side_effect=RuntimeError("boom")
            ):
                with self.assertRaises(RuntimeError):
                    orchestrator.run()

            history = StateStore(root).load_history()
            self.assertEqual(len(history["runs"]), 1)
            self.assertEqual(history["runs"][0]["status"], "failed")
            self.assertEqual(history["runs"][0]["decision"], "hold")

    def test_history_is_written_without_temp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_once(root, 1, "2026-01-01T00:00:01Z")
            harness_dir = root / ".harness"
            self.assertTrue((harness_dir / "history.json").exists())
            self.assertEqual(
                [path for path in harness_dir.iterdir() if path.name.endswith(".tmp")],
                [],
            )


if __name__ == "__main__":
    unittest.main()
