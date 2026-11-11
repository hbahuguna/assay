"""SARIF, JUnit, and report signature tests."""

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from harness.cli import main
from harness.exports import build_junit, build_sarif, sign_report, verify_report_signature
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-repo"
SECRET = "not-a-real-token-value"


class ExportTests(unittest.TestCase):
    def _run(self, root):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(root),
                ]
            )
        self.assertEqual(code, 0)
        return StateStore(root).load_report()

    def test_sarif_maps_findings_to_rules_with_severity_and_remediation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._run(root)
            sarif = build_sarif(report)

        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(len(sarif["runs"]), 1)
        run = sarif["runs"][0]
        self.assertTrue(run["tool"]["driver"]["rules"])
        self.assertEqual(len(run["results"]), len(report["findings"]))
        result = run["results"][0]
        self.assertIn("ruleId", result)
        self.assertIn("level", result)
        self.assertIn("message", result)
        self.assertIn("locations", result)
        self.assertTrue(result["message"]["text"])
        rule = run["tool"]["driver"]["rules"][0]
        self.assertTrue(rule["shortDescription"]["text"])
        self.assertTrue(rule["help"]["text"])

    def test_junit_maps_checks_to_test_cases_with_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._run(root)
            report["checks"][0]["status"] = "blocked"
            report["checks"][1]["status"] = "skipped"
            xml_text = build_junit(report)

        root_element = ET.fromstring(xml_text.split("?>", 1)[-1])
        self.assertEqual(root_element.tag, "testsuite")
        cases = {case.get("name"): case for case in root_element.findall("testcase")}
        self.assertIn("blocked", cases[report["checks"][0]["id"]].get("status"))
        self.assertIn("skipped", cases[report["checks"][1]["id"]].get("status"))
        self.assertIsNotNone(cases[report["checks"][0]["id"]].find("skipped"))
        self.assertIsNotNone(cases[report["checks"][1]["id"]].find("skipped"))

        failed_case = next(case for case in root_element.findall("testcase") if case.get("status") == "failed")
        self.assertIsNotNone(failed_case.find("failure"))

    def test_signature_verifies_and_fails_after_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._run(root)
            signature = sign_report(report, "test-key")
            self.assertTrue(verify_report_signature(report, signature, "test-key"))
            self.assertFalse(verify_report_signature(report, "bad-signature", "test-key"))

            tampered = json.loads(json.dumps(report))
            tampered["confidence"]["score"] = 999.0
            self.assertFalse(verify_report_signature(tampered, signature, "test-key"))

    def test_exporters_redact_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._run(root)
            report["findings"][0]["location"] = "token={}".format(SECRET)
            report["checks"][1]["actual"] = "authorization: {}".format(SECRET)

            sarif = build_sarif(report)
            junit = build_junit(report)
            sarif_text = json.dumps(sarif)
            self.assertNotIn(SECRET, sarif_text)
            self.assertNotIn(SECRET, junit)
            self.assertIn("[REDACTED", sarif_text)
            self.assertIn("[REDACTED", junit)


if __name__ == "__main__":
    unittest.main()
