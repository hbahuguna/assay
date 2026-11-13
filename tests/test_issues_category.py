"""Tests for QH-043: /assay-issues test category support."""

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


class IssuesCategoryTests(unittest.TestCase):
    def test_issues_intent_plans_and_executes_issues_category_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                intent="Assay issues assessment",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")

            check = next((c for c in state.checks if c.id == "static.issues_category"), None)
            self.assertIsNotNone(check, "issues category check should be planned")
            self.assertEqual(check.status, "passed")
            self.assertEqual(check.persona, "functional")
            self.assertEqual(check.domain, "functional")
            self.assertGreater(len(check.evidence), 0)
            self.assertEqual(check.metadata.get("category"), "issues")

            report = StateStore(root).load_report()
            report_check = next(c for c in report["checks"] if c["id"] == "static.issues_category")
            self.assertEqual(report_check["status"], "passed")
            self.assertTrue(report_check["evidence"])

            evidence_dir = root / report["evidence"]["directory"]
            manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest.get("evidence") or manifest.get("artifacts") or manifest.get("files") or []
            issues_artifacts = [a for a in artifacts if "issues-category-scope" in a["path"]]
            self.assertTrue(issues_artifacts)
            self.assertEqual(issues_artifacts[0]["check_id"], "static.issues_category")
            self.assertTrue(issues_artifacts[0]["sha256"])

            artifact_file = root / issues_artifacts[0]["path"]
            if not artifact_file.exists():
                artifact_file = Path(issues_artifacts[0]["path"])
            self.assertTrue(artifact_file.exists())
            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["category"], "issues")
            self.assertIn("issue_markers", payload)
            self.assertIsInstance(payload["issue_markers"], dict)
            self.assertIsInstance(payload["files_with_issue_markers"], list)

            result = EvidenceStore(root, report["run_id"]).verify()
            self.assertFalse(result["tampered"])
            self.assertTrue(result["signature_valid"])

            self.assertIn("static.issues_category", [c["id"] for c in report["checks"]])
            self.assertGreater(report["confidence"]["breakdown"]["total_weight"], 0)

    def test_issues_category_phrase_also_plans_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                intent="Run the issues category assessment.",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")
            self.assertIsNotNone(
                next((c for c in state.checks if c.id == "static.issues_category"), None)
            )

    def test_non_issues_intent_does_not_plan_issues_category_check(self):
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
                next((c for c in state.checks if c.id == "static.issues_category"), None)
            )
            report = StateStore(root).load_report()
            self.assertNotIn("static.issues_category", [c["id"] for c in report["checks"]])


if __name__ == "__main__":
    unittest.main()
