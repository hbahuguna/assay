"""Resume guard tests for changed plugin registries."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.orchestrator import Orchestrator, RunOptions
from harness.plugins import CheckRegistry, build_registry
from harness.state import StateError

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-repo"
PERSONAS = ROOT / "personas"


def run_once(root: Path):
    options = RunOptions(
        target=str(SAMPLE),
        personas_dir=str(PERSONAS),
        no_browser=True,
        kill_on="never",
        seed=1234,
        clock_mode="frozen",
        frozen_clock="2026-01-02T03:04:05Z",
    )
    state = Orchestrator(root, options).run()
    return state, root / ".harness" / "report.json"


class RegistryResumeGuardTests(unittest.TestCase):
    def test_registry_fingerprint_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, report_path = run_once(root)
            self.assertTrue(state.options.get("registry_fingerprint"))
            self.assertEqual(
                state.options["registry_fingerprint"],
                state.options["plugin_registry_fingerprint"],
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["options"]["registry_fingerprint"],
                state.options["registry_fingerprint"],
            )

    def test_resume_succeeds_with_unchanged_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, _ = run_once(root)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
                seed=1234,
                clock_mode="frozen",
                frozen_clock="2026-01-02T03:04:05Z",
            )
            resumed = Orchestrator(root, options).run(resume=True)
            self.assertEqual(resumed.run_id, state.run_id)

    def test_resume_rejects_changed_registry_without_mutating_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, report_path = run_once(root)
            progress_path = root / ".harness" / "progress.json"
            report_before = report_path.read_bytes()
            progress_before = progress_path.read_bytes()
            evidence_files = {
                path: path.read_bytes()
                for path in (root / ".harness" / "evidence").rglob("*")
                if path.is_file()
            }

            def changed_registry(root, log=None):
                registry = build_registry(root, log=log)
                # Simulate a rotated trust key / changed plugin registry.
                registry.trust_keys["rotated"] = "rotated-secret"
                return registry

            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
                seed=1234,
                clock_mode="frozen",
                frozen_clock="2026-01-02T03:04:05Z",
            )
            with mock.patch("harness.orchestrator.build_registry", changed_registry):
                with self.assertRaises(StateError):
                    Orchestrator(root, options).run(resume=True)

            self.assertEqual(report_path.read_bytes(), report_before)
            self.assertEqual(progress_path.read_bytes(), progress_before)
            for path, content in evidence_files.items():
                self.assertEqual(path.read_bytes(), content)

    def test_resume_rejects_removed_plugin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_once(root)
            # Remove a built-in check from the registry by monkeypatching build_registry.
            def removed_registry(root, log=None):
                registry = build_registry(root, log=log)
                # Simulate a removed plugin by deleting a definition.
                if "static.inventory" in registry._definitions:
                    del registry._definitions["static.inventory"]
                    del registry._sources["static.inventory"]
                return registry

            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
                seed=1234,
                clock_mode="frozen",
                frozen_clock="2026-01-02T03:04:05Z",
            )
            with mock.patch("harness.orchestrator.build_registry", removed_registry):
                with self.assertRaises(StateError):
                    Orchestrator(root, options).run(resume=True)


if __name__ == "__main__":
    unittest.main()
