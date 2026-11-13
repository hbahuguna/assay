"""Tests for QH-100: narrower unsafe patterns and test-credential allowlist."""

import tempfile
import unittest
from pathlib import Path

from harness.orchestrator import Orchestrator, RunOptions
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"


class StaticPrecisionTests(unittest.TestCase):
    def _run(self, target: Path, base: Path):
        options = RunOptions(
            target=str(target),
            intent="Assess release readiness.",
            personas_dir=str(PERSONAS),
            no_browser=True,
            kill_on="never",
        )
        state = Orchestrator(base / "harness", options).run()
        return state, StateStore(base / "harness").load_report()

    def test_member_exec_calls_are_not_reported_but_bare_exec_is(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            (target / "src").mkdir(parents=True)
            (target / "tests").mkdir()
            (target / "src" / "db.js").write_text(
                'const row = db.exec("SELECT 1");\n', encoding="utf-8"
            )
            (target / "src" / "regex.ts").write_text(
                "const match = pattern.exec(value);\n", encoding="utf-8"
            )
            (target / "src" / "dynamic.py").write_text(
                "def run(code, ns):\n    exec(code, ns)\n", encoding="utf-8"
            )
            (target / "src" / "child.js").write_text(
                'const child_process = require("child_process")\nchild_process.exec(command)\n',
                encoding="utf-8",
            )
            (target / "tests" / "test_app.py").write_text(
                "def test_ok():\n    assert True\n", encoding="utf-8"
            )
            (target / "pyproject.toml").write_text(
                "[project]\nname = 'fixture'\nversion = '0.1.0'\n", encoding="utf-8"
            )

            state, report = self._run(target, base)
            self.assertEqual(state.status, "completed")

            unsafe_findings = [
                finding
                for finding in report["findings"]
                if finding["domain"] == "security"
                and "code execution" in finding["title"].lower()
                or (
                    finding["domain"] == "security"
                    and "child_process" in finding["title"].lower()
                )
            ]
            locations = [finding["location"] for finding in unsafe_findings]
            self.assertTrue(any("src/dynamic.py" in location for location in locations))
            self.assertTrue(any("src/child.js" in location for location in locations))
            self.assertFalse(any("src/db.js" in location for location in locations))
            self.assertFalse(any("src/regex.ts" in location for location in locations))

    def test_obvious_test_credentials_are_skipped_and_real_looking_values_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            (target / "src").mkdir(parents=True)
            (target / "tests").mkdir()
            (target / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (target / "src" / "config.py").write_text(
                'api_key = "9f8a7b6c5d4e3f2a1b0c"\n', encoding="utf-8"
            )
            (target / "tests" / "test_billing.py").write_text(
                "secret = 'sk_test_dummy'\n"
                "api_key = 'whsec_test_secret'\n"
                "token = 'test-key'\n",
                encoding="utf-8",
            )
            (target / "pyproject.toml").write_text(
                "[project]\nname = 'fixture'\nversion = '0.1.0'\n", encoding="utf-8"
            )

            state, report = self._run(target, base)
            self.assertEqual(state.status, "completed")

            secret_findings = [
                finding
                for finding in report["findings"]
                if finding["title"].startswith("Credential-like value")
            ]
            locations = [finding["location"] for finding in secret_findings]
            self.assertTrue(any("src/config.py" in location for location in locations))
            self.assertFalse(any("tests/test_billing.py" in location for location in locations))

            secrets_check = next(
                check for check in report["checks"] if check["id"] == "static.secrets"
            )
            self.assertGreaterEqual(
                secrets_check["metadata"]["ignored_placeholder_or_test_fixture_matches"], 3
            )

    def test_inner_html_is_still_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            (target / "src").mkdir(parents=True)
            (target / "tests").mkdir()
            (target / "src" / "dashboard.js").write_text(
                "function render(el, title) { el.innerHTML = title }\n", encoding="utf-8"
            )
            (target / "tests" / "test_app.py").write_text(
                "def test_ok():\n    assert True\n", encoding="utf-8"
            )
            (target / "pyproject.toml").write_text(
                "[project]\nname = 'fixture'\nversion = '0.1.0'\n", encoding="utf-8"
            )

            state, report = self._run(target, base)
            self.assertEqual(state.status, "completed")
            inner_html = [
                finding
                for finding in report["findings"]
                if "innerHTML" in finding["title"]
            ]
            self.assertTrue(inner_html)
            self.assertIn("src/dashboard.js", inner_html[0]["location"])


if __name__ == "__main__":
    unittest.main()
