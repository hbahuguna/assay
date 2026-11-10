"""Deterministic seed, clock, and report reproducibility tests."""

import contextlib
import datetime
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness import cli
from harness.determinism import (
    PRODUCTION,
    RealClock,
    RealEntropy,
    SeededEntropy,
    make_determinism,
    new_id,
    reset_determinism,
    set_determinism,
)
from harness.errors import HarnessError
from harness.models import RunState, Target, utc_now
from harness.orchestrator import Orchestrator, RunOptions

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"
FROZEN = "2026-01-02T03:04:05Z"


def run_report(root: Path, **overrides):
    options = RunOptions(
        target=str(SAMPLE),
        personas_dir=str(PERSONAS),
        no_browser=True,
        **overrides,
    )
    orchestrator = Orchestrator(root, options)
    state = orchestrator.run()
    report = json.loads(
        (root / ".harness" / "report.json").read_text(encoding="utf-8")
    )
    return state, report


class DeterminismUnitTests(unittest.TestCase):
    def test_frozen_clock_and_seed_are_deterministic(self):
        first = make_determinism(seed=42, clock_mode="frozen", frozen_clock=FROZEN)
        second = make_determinism(seed=42, clock_mode="frozen", frozen_clock=FROZEN)
        self.assertEqual(first.now(), FROZEN)
        self.assertEqual(first.run_id(), second.run_id())
        self.assertEqual(first.new_id("finding"), second.new_id("finding"))
        self.assertNotEqual(
            first.new_id("finding"),
            make_determinism(seed=43, clock_mode="frozen", frozen_clock=FROZEN).new_id(
                "finding"
            ),
        )

    def test_active_determinism_controls_models_helpers(self):
        provider = make_determinism(seed=7, clock_mode="frozen", frozen_clock=FROZEN)
        expected_id = make_determinism(
            seed=7, clock_mode="frozen", frozen_clock=FROZEN
        ).new_id("finding")
        token = set_determinism(provider)
        try:
            self.assertEqual(utc_now(), FROZEN)
            self.assertEqual(new_id("finding"), expected_id)
        finally:
            reset_determinism(token)

    def test_production_defaults_use_real_clock_and_uuid_entropy(self):
        self.assertIsNone(PRODUCTION.seed)
        self.assertEqual(PRODUCTION.clock_mode, "real")
        self.assertIsInstance(PRODUCTION.clock, RealClock)
        self.assertIsInstance(PRODUCTION.entropy, RealEntropy)
        self.assertNotIsInstance(PRODUCTION.entropy, SeededEntropy)
        self.assertNotEqual(new_id("finding"), new_id("finding"))

        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        stamp = utc_now()
        after = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        self.assertGreaterEqual(stamp, before.isoformat().replace("+00:00", "Z"))
        self.assertLessEqual(stamp, after.isoformat().replace("+00:00", "Z"))

    def test_invalid_determinism_options_raise(self):
        with self.assertRaises(HarnessError):
            make_determinism(seed=1, clock_mode="frozen")
        with self.assertRaises(HarnessError):
            make_determinism(seed=1, clock_mode="frozen", frozen_clock="not-a-time")
        with self.assertRaises(HarnessError):
            make_determinism(
                seed=1, clock_mode="real", frozen_clock="2026-01-01T00:00:00Z"
            )
        with self.assertRaises(HarnessError):
            make_determinism(seed="123")

    def test_run_state_round_trips_seed_and_clock_mode(self):
        state = RunState(
            run_id="run-1",
            workflow_id="release-confidence",
            target=Target(raw=".", kind="directory", resolved=".", source="."),
            intent="test",
            seed=7,
            clock_mode="frozen",
        )
        loaded = RunState.from_dict(state.to_dict())
        self.assertEqual(loaded.seed, 7)
        self.assertEqual(loaded.clock_mode, "frozen")


class ReproducibleRunTests(unittest.TestCase):
    def test_same_seed_and_frozen_clock_produce_identical_reports(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first_state, first_report = run_report(
                Path(first_dir),
                seed=1234,
                clock_mode="frozen",
                frozen_clock=FROZEN,
            )
            second_state, second_report = run_report(
                Path(second_dir),
                seed=1234,
                clock_mode="frozen",
                frozen_clock=FROZEN,
            )

        self.assertEqual(first_state.run_id, second_state.run_id)
        self.assertEqual(
            first_report["determinism"],
            {"seed": 1234, "clock_mode": "frozen", "entropy": "seeded"},
        )
        self.assertEqual(first_report["generated_at"], FROZEN)
        self.assertEqual(first_report["confidence"], second_report["confidence"])
        self.assertEqual(first_report["coverage"], second_report["coverage"])
        self.assertEqual(first_report["findings"], second_report["findings"])
        self.assertEqual(first_report["checks"], second_report["checks"])
        self.assertEqual(first_report["phases"], second_report["phases"])
        self.assertEqual(
            json.dumps(first_report, sort_keys=True, indent=2),
            json.dumps(second_report, sort_keys=True, indent=2),
        )

    def test_generated_at_override_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            state, report = run_report(
                Path(directory),
                seed=1234,
                clock_mode="frozen",
                frozen_clock=FROZEN,
                generated_at="2026-02-03T04:05:06Z",
            )

        self.assertEqual(state.options["generated_at"], "2026-02-03T04:05:06Z")
        self.assertEqual(report["generated_at"], "2026-02-03T04:05:06Z")
        self.assertEqual(report["started_at"], FROZEN)
        self.assertEqual(report["completed_at"], FROZEN)
        self.assertEqual(report["determinism"]["seed"], 1234)
        self.assertEqual(report["determinism"]["clock_mode"], "frozen")

    def test_production_runs_are_not_reproducible(self):
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first_state, first_report = run_report(Path(first_dir))
            second_state, second_report = run_report(Path(second_dir))

        self.assertNotEqual(first_state.run_id, second_state.run_id)
        self.assertIsNone(first_report["determinism"]["seed"])
        self.assertEqual(first_report["determinism"]["clock_mode"], "real")
        self.assertEqual(first_report["determinism"]["entropy"], "uuid4")

    def test_cli_records_determinism_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(
                    [
                        "run",
                        "--target",
                        str(SAMPLE),
                        "--no-browser",
                        "--quiet",
                        "--root",
                        directory,
                        "--personas-dir",
                        str(PERSONAS),
                        "--seed",
                        "1234",
                        "--clock-mode",
                        "frozen",
                        "--frozen-clock",
                        FROZEN,
                        "--generated-at",
                        "2026-02-03T04:05:06Z",
                    ]
                )
            report = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(report["determinism"]["seed"], 1234)
        self.assertEqual(report["determinism"]["clock_mode"], "frozen")
        self.assertEqual(report["generated_at"], "2026-02-03T04:05:06Z")


if __name__ == "__main__":
    unittest.main()
