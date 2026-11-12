"""Tests for QH-039: /full test category support."""

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


class FullCategoryTests(unittest.TestCase):
    def test_full_intent_plans_and_executes_full_assessment_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                intent="Assay full assessment",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")

            check = state.check("static.full_assessment")
            self.assertIsNotNone(check, "full category check should be planned")
            self.assertEqual(check.status, "passed")
            self.assertEqual(check.persona, "functional")
            self.assertEqual(check.domain, "functional")
            self.assertGreater(len(check.evidence), 0)

            report = StateStore(root).load_report()
            report_check = next(c for c in report["checks"] if c["id"] == "static.full_assessment")
            self.assertEqual(report_check["status"], "passed")
            self.assertTrue(report_check["evidence"])

            evidence_dir = root / report["evidence"]["directory"]
            manifest_path = evidence_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            artifacts = manifest.get("evidence") or manifest.get("artifacts") or manifest.get("files") or []
            artifact_paths = [a["path"] for a in artifacts]
            self.assertTrue(any("full-assessment-scope" in p for p in artifact_paths))
            full_artifacts = [a for a in artifacts if "full-assessment-scope" in a["path"]]
            self.assertTrue(full_artifacts)
            self.assertEqual(full_artifacts[0]["check_id"], "static.full_assessment")
            self.assertTrue(full_artifacts[0]["sha256"])
            self.assertNotEqual(full_artifacts[0]["sha256"], "")

            # Evidence content is linked and redacted/safe.
            artifact_path = Path(full_artifacts[0]["path"])
            if not artifact_path.is_absolute():
                # manifest stores project-relative paths in this harness
                artifact_file = root / full_artifacts[0]["path"]
                if not artifact_file.exists():
                    artifact_file = artifact_path
            else:
                artifact_file = artifact_path
            self.assertTrue(artifact_file.exists())
            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["category"], "full")

            # Manifest signatures/hashes verify.
            run_id = report["run_id"]
            result = EvidenceStore(root, run_id).verify()
            self.assertFalse(result["tampered"])
            self.assertTrue(result["signature_valid"])

            # Confidence/report account for the category check.
            planned_ids = [c["id"] for c in report["checks"]]
            self.assertIn("static.full_assessment", planned_ids)
            self.assertGreater(report["confidence"]["breakdown"]["total_weight"], 0)

    def test_non_full_intent_does_not_plan_full_assessment_check(self):
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
            self.assertIsNone(next((c for c in state.checks if c.id == "static.full_assessment"), None))
            report = StateStore(root).load_report()
            planned_ids = [c["id"] for c in report["checks"]]
            self.assertNotIn("static.full_assessment", planned_ids)


if __name__ == "__main__":
    unittest.main()
