"""Packaging and release workflow contract tests."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackagingContractTests(unittest.TestCase):
    def test_console_script_is_declared(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('quality-harness = "harness.cli:main"', pyproject)
        self.assertIn('name = "quality-harness"', pyproject)

    def test_core_and_browser_dockerfiles_are_separate(self):
        self.assertTrue((ROOT / "Dockerfile").is_file())
        self.assertTrue((ROOT / "Dockerfile.browser").is_file())
        core = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        browser = (ROOT / "Dockerfile.browser").read_text(encoding="utf-8")
        self.assertNotIn("playwright", core.lower())
        self.assertIn("playwright", browser.lower())

    def test_release_workflow_signs_and_attests_artifacts(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python -m sigstore sign", workflow)
        self.assertIn("attest-build-provenance", workflow)
        self.assertIn("verify identity", workflow)
        self.assertIn("Dockerfile.browser", workflow)
        self.assertIn("cosign sign", workflow)

    def test_installed_cli_reports_version(self):
        executable = shutil.which("quality-harness")
        if executable is None:
            self.skipTest("quality-harness console script is not installed in this environment")
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("quality-harness", result.stdout.lower() + result.stderr.lower())

    def test_module_entrypoint_reports_version(self):
        result = subprocess.run(
            [sys.executable, "-m", "harness", "--version"],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
