"""Tests for QH-044: /assay-demo test category support."""

import json
import tempfile
import unittest
from pathlib import Path

from harness.checks import plan_checks
from harness.evidence import EvidenceStore
from harness.models import Target
from harness.orchestrator import Orchestrator, RunOptions
from harness.personas import load_personas
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"


def _write_demo_fixture(target: Path) -> None:
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "demo").mkdir()
    (target / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (target / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (target / "demo" / "index.js").write_text("console.log('demo')\n", encoding="utf-8")
    (target / "package.json").write_text(
        json.dumps({"scripts": {"demo": "node demo/index.js", "test": "jest"}}),
        encoding="utf-8",
    )
    (target / "README.md").write_text(
        "# Sample\n\nQuickstart: run `npm run demo`.\n", encoding="utf-8"
    )


class DemoCategoryTests(unittest.TestCase):
    def test_demo_intent_plans_and_executes_demo_category_check(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "fixture"
            target.mkdir()
            _write_demo_fixture(target)
            root = base / "harness"

            options = RunOptions(
                target=str(target),
                intent="Assay demo assessment",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")

            check = next((c for c in state.checks if c.id == "static.demo_category"), None)
            self.assertIsNotNone(check, "demo category check should be planned")
            self.assertEqual(check.status, "passed")
            self.assertEqual(check.persona, "functional")
            self.assertEqual(check.domain, "functional")
            self.assertGreater(len(check.evidence), 0)
            self.assertEqual(check.metadata.get("category"), "demo")
            self.assertGreaterEqual(check.metadata.get("demo_paths"), 1)
            self.assertGreaterEqual(check.metadata.get("demo_entrypoints"), 1)
            self.assertGreaterEqual(check.metadata.get("quickstart_docs"), 1)
            self.assertEqual(check.metadata.get("behavioral_evidence"), False)
            self.assertIn("no behavioral demo evidence", check.metadata.get("limitation"))

            report = StateStore(root).load_report()
            report_check = next(c for c in report["checks"] if c["id"] == "static.demo_category")
            self.assertEqual(report_check["status"], "passed")
            self.assertTrue(report_check["evidence"])
            self.assertTrue(
                any(
                    "no behavioral demo evidence" in note
                    for note in report["limitations"]
                )
            )

            evidence_dir = root / report["evidence"]["directory"]
            manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
            artifacts = manifest.get("evidence") or manifest.get("artifacts") or manifest.get("files") or []
            demo_artifacts = [a for a in artifacts if "demo-category-scope" in a["path"]]
            self.assertTrue(demo_artifacts)
            self.assertEqual(demo_artifacts[0]["check_id"], "static.demo_category")
            self.assertTrue(demo_artifacts[0]["sha256"])

            artifact_file = root / demo_artifacts[0]["path"]
            if not artifact_file.exists():
                artifact_file = Path(demo_artifacts[0]["path"])
            self.assertTrue(artifact_file.exists())
            payload = json.loads(artifact_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["category"], "demo")
            self.assertIn("demo/index.js", payload["demo_paths"])
            self.assertIn("script:demo", payload["demo_entrypoints"])
            self.assertIn("README.md", payload["quickstart_docs"])
            self.assertNotIn("node demo/index.js", json.dumps(payload["demo_entrypoints"]))

            result = EvidenceStore(root, report["run_id"]).verify()
            self.assertFalse(result["tampered"])
            self.assertTrue(result["signature_valid"])

            self.assertIn("static.demo_category", [c["id"] for c in report["checks"]])
            self.assertGreater(report["confidence"]["breakdown"]["total_weight"], 0)

    def test_demo_category_phrase_also_plans_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(ROOT / "examples" / "sample-repo"),
                intent="Run the demo category assessment.",
                personas_dir=str(PERSONAS),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")
            self.assertIsNotNone(
                next((c for c in state.checks if c.id == "static.demo_category"), None)
            )

    def test_demo_url_plans_browser_checks_and_skips_static_marker(self):
        personas = load_personas(PERSONAS, require_minimum=3)
        target = Target(
            raw="http://localhost:3000",
            kind="url",
            resolved="http://localhost:3000",
        )
        checks = plan_checks(
            target,
            {"available": False},
            personas,
            max_checks=50,
            no_browser=False,
            intent="Assay demo assessment",
        )
        ids = {check.id for check in checks}
        self.assertNotIn("static.demo_category", ids)
        self.assertTrue(any(check_id.startswith("browser.") for check_id in ids))

    def test_non_demo_intent_does_not_plan_demo_category_check(self):
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
                next((c for c in state.checks if c.id == "static.demo_category"), None)
            )
            report = StateStore(root).load_report()
            self.assertNotIn("static.demo_category", [c["id"] for c in report["checks"]])


if __name__ == "__main__":
    unittest.main()
