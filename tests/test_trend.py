"""Run-to-run trend diff tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from harness.cli import main

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class TrendTests(unittest.TestCase):
    def _project(self, root, with_secret=False):
        project = root / "project"
        (project / "src").mkdir(parents=True, exist_ok=True)
        (project / "tests").mkdir()
        (project / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
        (project / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
        (project / "src" / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
        (project / "tests" / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (project / ".github" / "workflows" / "ci.yml").write_text("name: ci\non: [pull_request]\n", encoding="utf-8")
        if with_secret:
            (project / "src" / "key.py").write_text(
                'PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"""\n',
                encoding="utf-8",
            )
        return project

    def test_first_run_is_a_baseline_without_fabricated_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(project),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root / "state"),
                    "--kill-on",
                    "never",
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            trend = report["trend"]
            self.assertTrue(trend["baseline"])
            self.assertIsNone(trend["previous_run_id"])
            self.assertEqual(trend["new"], [])
            self.assertEqual(trend["resolved"], [])
            self.assertEqual(trend["persistent"], [])
            self.assertEqual(trend["score_delta"], 0.0)

    def test_second_run_reports_persistent_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root, with_secret=True)
            code, _, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(project),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root / "state"),
                    "--kill-on",
                    "never",
                ]
            )
            self.assertEqual(code, 0)
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(project),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root / "state"),
                    "--kill-on",
                    "never",
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            trend = report["trend"]
            self.assertFalse(trend["baseline"])
            self.assertTrue(trend["persistent"])
            self.assertEqual(trend["new"], [])
            self.assertEqual(trend["resolved"], [])
            self.assertEqual(trend["score_delta"], 0.0)

    def test_score_regression_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            code, _, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(project),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root / "state"),
                    "--kill-on",
                    "never",
                ]
            )
            self.assertEqual(code, 0)

            project_secret = root / "project-secret"
            # Reuse the same target path by adding the secret to the original project.
            (project / "src" / "key.py").write_text(
                'PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"""\n',
                encoding="utf-8",
            )

            code, stdout, stderr = run_cli(
                [
                    "run",
                    "--target",
                    str(project),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root / "state"),
                    "--fail-on-score-regression",
                    "0",
                ]
            )
            self.assertEqual(code, 1, stderr)
            report = json.loads(stdout)
            trend = report["trend"]
            self.assertFalse(trend["baseline"])
            self.assertTrue(trend["new"])
            self.assertLess(trend["score_delta"], 0)


if __name__ == "__main__":
    unittest.main()
