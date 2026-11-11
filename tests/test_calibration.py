"""Confidence calibration fixture tests."""

import json
import tempfile
import unittest
from pathlib import Path

from harness.calibration import load_calibration_fixtures, run_calibration, write_calibration_report
from harness.cli import main

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "calibration" / "fixtures"


class CalibrationTests(unittest.TestCase):
    def test_at_least_twelve_labeled_fixtures_cover_all_outcomes(self):
        fixtures = load_calibration_fixtures(FIXTURES)
        self.assertGreaterEqual(len(fixtures), 12)
        expected = {fixture["expected_decision"] for fixture in fixtures}
        self.assertEqual(expected, {"ship", "review", "hold"})

    def test_calibration_report_contains_required_fields(self):
        report = run_calibration(FIXTURES)
        self.assertEqual(report["schema_version"], "quality-harness.calibration/v1")
        self.assertEqual(report["formula"], "weighted-coverage-v1")
        self.assertEqual(report["fixture_count"], len(list(FIXTURES.glob("*.json"))))
        self.assertGreaterEqual(report["summary"]["decision_matches"], 1)
        self.assertEqual(
            report["summary"]["decision_mismatches"],
            len(report["summary"]["mismatched_fixtures"]),
        )
        for fixture in report["fixtures"]:
            self.assertIn("score", fixture)
            self.assertIn("band", fixture)
            self.assertIn("gate", fixture)
            self.assertIn("expected_decision", fixture)
            self.assertIn("actual_decision", fixture)
            self.assertIn("decision_match", fixture)
            self.assertIn("breakdown", fixture)
            self.assertTrue(isinstance(fixture["score"], (int, float)))
            self.assertTrue(isinstance(fixture["decision_match"], bool))

    def test_calibration_cli_writes_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "calibration-report.json"
            import io
            from contextlib import redirect_stderr, redirect_stdout

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "calibrate",
                        "--fixtures-dir",
                        str(FIXTURES),
                        "--output",
                        str(output),
                        "--root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(output.exists())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["fixture_count"], len(list(FIXTURES.glob("*.json"))))
            cli_report = json.loads(stdout.getvalue())
            self.assertEqual(cli_report["fixture_count"], report["fixture_count"])


if __name__ == "__main__":
    unittest.main()
