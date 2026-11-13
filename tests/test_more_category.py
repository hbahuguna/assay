"""Tests for QH-045: /assay-more test category support."""

import json
import tempfile
import unittest
from pathlib import Path

from harness.evidence import EvidenceStore
from harness.orchestrator import Orchestrator, RunOptions
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"


def _write_fixture(target: Path) -> None:
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "examples").mkdir()
    (target / "docs").mkdir()
    (target / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (target / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (target / "examples" / "demo.py").write_text("print('demo')\n", encoding="utf-8")
    (target / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (target / "README.md").write_text("# Sample\n", encoding="utf-8")
    (target / "CONTRIBUTING.md").write_text("# Contributing\n", encoding="utf-8")
    (target / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    (target / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest", "lint": "eslint ."}}),
        encoding="utf-8",
    )


class MoreCategoryTests(unittest.TestCase):
    def test_more_intent_plans_and_executes_more_category_check(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "fixture"
            target.mkdir()
            _write_fixture(target)
            root = base / "harness"

            options = RunOptions(
                target=str(target),
                intent="Assay more assessment",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")

            check = next((c for c in state.checks if c.id == "static.more_category"), None)
            self.assertIsNotNone(check, "more category check should be planned")
            self.assertEqual(check.status, "passed")
            self.assertEqual(check.persona, "functional")
            self.assertEqual(check.domain, "functional")
            self.assertGreater(len(check.evidence), 0)
            self.assertEqual(check.metadata.get("category"), "more")
            self.assertGreaterEqual(check.metadata.get("docs", 0), 1)
            self.assertGreaterEqual(check.metadata.get("examples", 0), 1)
            self.assertGreaterEqual(check.metadata.get("scripts", 0), 1)

            report = StateStore(root).load_report()
            report_check = next(c for c in report["checks"] if c["id"] == "static.more_category")
            self.assertEqual(report_check["status"], "passed")
            self.assertTrue(report_check["evidence"])

            evidence_dir = root / report["evidence"]["directory"]
            manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest.get("evidence") or manifest.get("artifacts") or manifest.get("files") or []
            more_artifacts = [a for a in artifacts if "more-category-scope" in a["path"]]
            self.assertTrue(more_artifacts)
            self.assertEqual(more_artifacts[0]["check_id"], "static.more_category")
            self.assertTrue(more_artifacts[0]["sha256"])

            artifact_file = root / more_artifacts[0]["path"]
            if not artifact_file.exists():
                artifact_file = Path(more_artifacts[0]["path"])
            self.assertTrue(artifact_file.exists())
            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["category"], "more")
            self.assertIn("docs", payload)
            self.assertIn("examples", payload)
            self.assertIn("configs", payload)
            self.assertIn("scripts", payload)

            result = EvidenceStore(root, report["run_id"]).verify()
            self.assertFalse(result["tampered"])
            self.assertTrue(result["signature_valid"])
            self.assertGreater(report["confidence"]["breakdown"]["total_weight"], 0)

    def test_more_category_phrase_also_plans_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(ROOT / "examples" / "sample-repo"),
                intent="Run the more category assessment.",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")
            self.assertIsNotNone(
                next((c for c in state.checks if c.id == "static.more_category"), None)
            )

    def test_non_more_intent_does_not_plan_more_category_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(ROOT / "examples" / "sample-repo"),
                intent="Assess release readiness of the target.",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")
            self.assertIsNone(
                next((c for c in state.checks if c.id == "static.more_category"), None)
            )


if __name__ == "__main__":
    unittest.main()
