"""Tests for QH-042: /assay-browser test category support."""

import json
import tempfile
import unittest
from pathlib import Path

from harness.evidence import EvidenceStore
from harness.orchestrator import Orchestrator, RunOptions
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"


class BrowserCategoryTests(unittest.TestCase):
    def test_browser_intent_plans_and_executes_browser_category_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                intent="Assay browser assessment",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")

            check = next((c for c in state.checks if c.id == "static.browser_category"), None)
            self.assertIsNotNone(check, "browser category check should be planned")
            self.assertEqual(check.status, "passed")
            self.assertEqual(check.persona, "functional")
            self.assertEqual(check.domain, "functional")
            self.assertGreater(len(check.evidence), 0)

            report = StateStore(root).load_report()
            report_check = next(c for c in report["checks"] if c["id"] == "static.browser_category")
            self.assertEqual(report_check["status"], "passed")
            self.assertTrue(report_check["evidence"])

            evidence_dir = root / report["evidence"]["directory"]
            manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest.get("evidence") or manifest.get("artifacts") or manifest.get("files") or []
            browser_artifacts = [a for a in artifacts if "browser-category-scope" in a["path"]]
            self.assertTrue(browser_artifacts)
            self.assertEqual(browser_artifacts[0]["check_id"], "static.browser_category")
            self.assertTrue(browser_artifacts[0]["sha256"])

            artifact_file = root / browser_artifacts[0]["path"]
            if not artifact_file.exists():
                artifact_file = Path(browser_artifacts[0]["path"])
            self.assertTrue(artifact_file.exists())
            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["category"], "browser")

            result = EvidenceStore(root, report["run_id"]).verify()
            self.assertFalse(result["tampered"])
            self.assertTrue(result["signature_valid"])

            self.assertIn("static.browser_category", [c["id"] for c in report["checks"]])
            self.assertGreater(report["confidence"]["breakdown"]["total_weight"], 0)

    def test_non_browser_intent_does_not_plan_browser_category_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                intent="Assess release readiness of the target.",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")
            self.assertIsNone(
                next((c for c in state.checks if c.id == "static.browser_category"), None)
            )
            report = StateStore(root).load_report()
            self.assertNotIn("static.browser_category", [c["id"] for c in report["checks"]])


if __name__ == "__main__":
    unittest.main()
