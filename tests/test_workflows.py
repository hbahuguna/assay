"""Workflow loader, override, and phase-order contract tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from harness.cli import main
from harness.errors import HarnessError, WorkflowError
from harness.orchestrator import Orchestrator, RunOptions
from harness.state import StateStore
from harness.workflows import PHASE_ORDER, get_workflow, load_workflow_file

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"

VALID_OVERRIDE = """\
schema_version: 1
id: release-confidence
name: Release Confidence Override
version: "2.0"
description: Versioned workflow used by the loader tests.
phases:
  - name: risk_analysis
    title: Risk Analysis Override
    description: Rank risks with the override definition.
  - name: test_planning
    title: Test Planning Override
    description: Plan checks with the override definition.
  - name: execution
    title: Execution Override
    description: Execute checks with the override definition.
  - name: evidence_collection
    title: Evidence Collection Override
    description: Finalize evidence with the override definition.
  - name: confidence_scoring
    title: Confidence Scoring Override
    description: Score confidence with the override definition.
"""


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def write_override(root, text, filename="release-confidence.yaml"):
    workflow_dir = Path(root) / ".harness" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    path = workflow_dir / filename
    path.write_text(text, encoding="utf-8")
    return path


class WorkflowLoaderTests(unittest.TestCase):
    def test_builtin_workflow_has_the_fixed_phase_order(self):
        workflow = get_workflow("release-confidence")
        self.assertEqual(workflow.id, "release-confidence")
        self.assertEqual(workflow.version, "1.0")
        self.assertEqual(tuple(phase.name for phase in workflow.phases), PHASE_ORDER)
        self.assertEqual(len(workflow.phases), 5)

    def test_missing_override_directory_falls_back_to_builtin(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = get_workflow(
                "release-confidence", workflows_dir=Path(directory) / "does-not-exist"
            )
            self.assertEqual(workflow.version, "1.0")
            self.assertEqual(workflow.phases[0].title, "Risk Analysis")

    def test_valid_override_loads_titles_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_override(directory, VALID_OVERRIDE)
            workflow = load_workflow_file(path)
            self.assertEqual(workflow.version, "2.0")
            self.assertEqual(workflow.name, "Release Confidence Override")
            self.assertEqual(workflow.phases[0].title, "Risk Analysis Override")
            self.assertEqual(
                tuple(phase.name for phase in workflow.phases),
                PHASE_ORDER,
            )

    def test_unknown_workflow_is_a_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(WorkflowError):
                get_workflow("missing-workflow", workflows_dir=Path(directory))

    def test_unsupported_yaml_feature_is_a_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_override(
                directory,
                "id: release-confidence\n"
                "name: Broken\n"
                "version: '2.0'\n"
                "phases: [risk_analysis, test_planning, execution, evidence_collection, confidence_scoring]\n",
            )
            with self.assertRaises(WorkflowError):
                load_workflow_file(path)

    def test_out_of_order_phases_are_rejected(self):
        text = VALID_OVERRIDE.replace(
            "  - name: risk_analysis\n"
            "    title: Risk Analysis Override\n"
            "    description: Rank risks with the override definition.\n"
            "  - name: test_planning\n"
            "    title: Test Planning Override\n"
            "    description: Plan checks with the override definition.\n",
            "  - name: test_planning\n"
            "    title: Test Planning Override\n"
            "    description: Plan checks with the override definition.\n"
            "  - name: risk_analysis\n"
            "    title: Risk Analysis Override\n"
            "    description: Rank risks with the override definition.\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_override(directory, text)
            with self.assertRaises(WorkflowError) as raised:
                load_workflow_file(path)
            self.assertIn("phase order", str(raised.exception))

    def test_missing_phases_key_is_a_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_override(
                directory,
                "id: release-confidence\nname: Broken\nversion: '2.0'\n",
            )
            with self.assertRaises(HarnessError):
                load_workflow_file(path)

    def test_duplicate_workflow_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow_dir = Path(directory)
            (workflow_dir / "one.yaml").write_text(VALID_OVERRIDE, encoding="utf-8")
            (workflow_dir / "two.yaml").write_text(VALID_OVERRIDE, encoding="utf-8")
            with self.assertRaises(WorkflowError):
                get_workflow("release-confidence", workflows_dir=workflow_dir)


class WorkflowRunTests(unittest.TestCase):
    def test_override_is_used_and_reported_by_status_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_override(root, VALID_OVERRIDE)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
            )
            state = Orchestrator(root, options).run()

            self.assertEqual(state.workflow_id, "release-confidence")
            self.assertEqual(state.workflow_version, "2.0")
            self.assertEqual(state.phases[0].title, "Risk Analysis Override")
            self.assertEqual(state.status, "completed")

            report = StateStore(root).load_report()
            self.assertEqual(report["workflow"]["id"], "release-confidence")
            self.assertEqual(report["workflow"]["version"], "2.0")
            self.assertEqual(report["workflow"]["name"], "Release Confidence Override")
            self.assertEqual(report["phases"][0]["title"], "Risk Analysis Override")

            code, stdout, _ = run_cli(["status", "--root", directory])
            self.assertEqual(code, 0)
            status = json.loads(stdout)
            self.assertEqual(status["workflow_id"], "release-confidence")
            self.assertEqual(status["workflow_version"], "2.0")

    def test_cli_can_select_a_custom_versioned_workflow(self):
        custom = VALID_OVERRIDE.replace(
            "id: release-confidence", "id: strict-release"
        ).replace('version: "2.0"', 'version: "3.0"')
        with tempfile.TemporaryDirectory() as directory:
            write_override(directory, custom, filename="strict-release.yaml")
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--quiet",
                    "--root",
                    directory,
                    "--personas-dir",
                    str(PERSONAS),
                    "--workflow",
                    "strict-release",
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            self.assertEqual(report["workflow"]["id"], "strict-release")
            self.assertEqual(report["workflow"]["version"], "3.0")

    def test_cli_reports_a_malformed_override_as_a_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            write_override(
                directory,
                "id: release-confidence\n"
                "name: Broken\n"
                "version: '2.0'\n"
                "phases: [risk_analysis, test_planning, execution, evidence_collection, confidence_scoring]\n",
            )
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--quiet",
                    "--root",
                    directory,
                    "--personas-dir",
                    str(PERSONAS),
                ]
            )
            self.assertEqual(code, 2)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "error")
            self.assertEqual(payload["error"]["type"], "WorkflowError")
            self.assertNotIn("Traceback", stdout)


if __name__ == "__main__":
    unittest.main()
