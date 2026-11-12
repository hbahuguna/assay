"""Tests for QH-040: /assay-test test category support."""

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


class TestCategoryTests(unittest.TestCase):
    def test_test_intent_plans_and_executes_test_category_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                intent="Assay test assessment",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")

            check = next((c for c in state.checks if c.id == "static.test_category"), None)
            self.assertIsNotNone(check, "test category check should be planned")
            self.assertEqual(check.status, "passed")
            self.assertEqual(check.persona, "functional")
            self.assertEqual(check.domain, "functional")
            self.assertGreater(len(check.evidence), 0)

            report = StateStore(root).load_report()
            report_check = next(c for c in report["checks"] if c["id"] == "static.test_category")
            self.assertEqual(report_check["status"], "passed")
            self.assertTrue(report_check["evidence"])

            evidence_dir = root / report["evidence"]["directory"]
            manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest.get("evidence") or manifest.get("artifacts") or manifest.get("files") or []
            test_artifacts = [a for a in artifacts if "test-category-scope" in a["path"]]
            self.assertTrue(test_artifacts)
            self.assertEqual(test_artifacts[0]["check_id"], "static.test_category")
            self.assertTrue(test_artifacts[0]["sha256"])

            artifact_file = root / test_artifacts[0]["path"]
            if not artifact_file.exists():
                artifact_file = Path(test_artifacts[0]["path"])
            self.assertTrue(artifact_file.exists())
            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["category"], "test")

            result = EvidenceStore(root, report["run_id"]).verify()
            self.assertFalse(result["tampered"])
            self.assertTrue(result["signature_valid"])

            self.assertIn("static.test_category", [c["id"] for c in report["checks"]])
            self.assertGreater(report["confidence"]["breakdown"]["total_weight"], 0)

    def test_non_test_intent_does_not_plan_test_category_check(self):
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
            self.assertIsNone(next((c for c in state.checks if c.id == "static.test_category"), None))
            report = StateStore(root).load_report()
            self.assertNotIn("static.test_category", [c["id"] for c in report["checks"]])


if __name__ == "__main__":
    unittest.main()
