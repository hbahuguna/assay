"""Tests for QH-100: tracked-file and first-party scope handling."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.orchestrator import Orchestrator, RunOptions
from harness.static_analysis import SourceInspector
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"


def _base_fixture(target: Path) -> None:
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (target / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (target / "pyproject.toml").write_text(
        "[project]\nname = 'fixture'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    (target / "README.md").write_text("# Fixture\n", encoding="utf-8")


def _run(base: Path, target: Path, intent: str = "Assess release readiness."):
    options = RunOptions(
        target=str(target),
        intent=intent,
        personas_dir=str(PERSONAS),
        no_browser=True,
        kill_on="never",
    )
    state = Orchestrator(base / "harness", options).run()
    report = StateStore(base / "harness").load_report()
    return state, report


@unittest.skipUnless(shutil.which("git"), "git is required for tracked-file checks")
class TrackedFileScopeTests(unittest.TestCase):
    def test_ignored_env_file_is_not_reported_as_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir()
            _base_fixture(target)
            (target / ".gitignore").write_text(".env.local\n", encoding="utf-8")
            (target / ".env").write_text("EXAMPLE_VALUE=fixture\n", encoding="utf-8")
            (target / ".env.local").write_text("LOCAL_VALUE=fixture\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(target), "init", "-q"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "add",
                    ".gitignore",
                    ".env",
                    "src",
                    "tests",
                    "pyproject.toml",
                    "README.md",
                ],
                check=True,
            )

            state, report = _run(base, target)
            self.assertEqual(state.status, "completed")

            dependency_check = next(
                check for check in report["checks"] if check["id"] == "static.dependencies"
            )
            self.assertEqual(dependency_check["metadata"]["tracked_available"], True)
            self.assertEqual(dependency_check["metadata"]["env_files_tracked"], 1)
            self.assertEqual(dependency_check["metadata"]["env_files_ignored"], 1)

            locations = [finding["location"] for finding in report["findings"]]
            self.assertIn(".env", locations)
            self.assertNotIn(".env.local", locations)

            inventory = StateStore(base / "harness").load().inventory
            self.assertEqual(inventory["env_files_tracked"], [".env"])
            self.assertEqual(inventory["env_files_ignored"], [".env.local"])


class FirstPartyScopeTests(unittest.TestCase):
    def test_harnessignore_excludes_vendored_paths_from_static_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir()
            _base_fixture(target)
            vendor = target / "artifacts" / "vendor"
            vendor.mkdir(parents=True)
            (vendor / "bad.py").write_text(
                'import os\nos.system("echo vendored")\n', encoding="utf-8"
            )
            (target / "src" / "bad.py").write_text(
                'import os\nos.system("echo first-party")\n', encoding="utf-8"
            )
            (target / ".harnessignore").write_text("artifacts/\n", encoding="utf-8")

            state, report = _run(base, target)
            self.assertEqual(state.status, "completed")

            unsafe_check = next(
                check for check in report["checks"] if check["id"] == "static.unsafe"
            )
            self.assertEqual(unsafe_check["status"], "failed")

            unsafe_locations = [
                finding["location"]
                for finding in report["findings"]
                if finding["domain"] == "security"
            ]
            self.assertTrue(any("src/bad.py" in location for location in unsafe_locations))
            self.assertFalse(
                any("artifacts/vendor/bad.py" in location for location in unsafe_locations)
            )

            inventory = StateStore(base / "harness").load().inventory
            self.assertEqual(inventory["excluded_patterns"], ["artifacts/"])
            self.assertIn("artifacts", inventory["excluded_paths"])
            self.assertNotIn("artifacts/vendor/bad.py", inventory["source_files"])

            evidence_dir = base / "harness" / report["evidence"]["directory"]
            manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest.get("evidence") or manifest.get("artifacts") or []
            self.assertTrue(any("source-inventory" in item["path"] for item in artifacts))

    def test_harnessignore_negation_reincludes_a_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            keep = target / "artifacts" / "keep"
            vendor = target / "artifacts" / "vendor"
            keep.mkdir(parents=True)
            vendor.mkdir()
            (keep / "good.py").write_text("print('keep')\n", encoding="utf-8")
            (vendor / "bad.py").write_text("print('vendor')\n", encoding="utf-8")
            (target / ".harnessignore").write_text(
                "artifacts/\n!artifacts/keep/\n", encoding="utf-8"
            )

            inventory = SourceInspector(target).discover()
            self.assertIn("artifacts/keep/good.py", inventory.source_files)
            self.assertNotIn("artifacts/vendor/bad.py", inventory.source_files)


if __name__ == "__main__":
    unittest.main()
