"""Report JSON Schema and contract tests."""

import json
import tempfile
import unittest
from pathlib import Path

from harness import cli
from harness.report import REPORT_SCHEMA, REPORT_SCHEMA_PATH, is_valid_report, report_schema, validate_report

ROOT = Path(__file__).resolve().parents[1]


def run_cli(arguments):
    stdout = __import__("io").StringIO()
    stderr = __import__("io").StringIO()
    import contextlib

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class ReportSchemaContractTests(unittest.TestCase):
    def test_schema_file_matches_report_builder_schema(self):
        self.assertTrue(REPORT_SCHEMA_PATH.is_file())
        self.assertEqual(json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8")), report_schema())

    def test_completed_report_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(ROOT / "examples" / "sample-repo"),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            self.assertEqual(report["schema_version"], REPORT_SCHEMA)
            self.assertEqual(validate_report(report), [])
            self.assertTrue(is_valid_report(report))

    def test_halted_report_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(ROOT / "examples" / "sample-repo"),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--kill-on",
                    "high",
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 3)
            report = json.loads(stdout)
            self.assertEqual(report["status"], "halted")
            self.assertEqual(validate_report(report), [])

    def test_removing_or_retyping_a_field_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(ROOT / "examples" / "sample-repo"),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            report.pop("run_id")
            self.assertFalse(is_valid_report(report))
            self.assertTrue(validate_report(report))
            report = json.loads(stdout)
            report["confidence"]["score"] = "high"
            self.assertFalse(is_valid_report(report))

    def test_additive_optional_fields_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(ROOT / "examples" / "sample-repo"),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            report["future_field"] = {"nested": True}
            report["confidence"]["future_nested"] = 1
            self.assertEqual(validate_report(report), [])

    def test_schema_is_published_as_release_artifact(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("schemas/", workflow)


if __name__ == "__main__":
    unittest.main()
