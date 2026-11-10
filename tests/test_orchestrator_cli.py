"""End-to-end orchestrator and CLI contract tests."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from harness.cli import main
from harness.models import CheckStatus
from harness.orchestrator import Orchestrator, RunOptions
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class OrchestratorTests(unittest.TestCase):
    def test_critical_finding_halts_and_skips_remaining_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            (target / "src").mkdir(parents=True)
            (target / "tests").mkdir()
            (target / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            (target / "src" / "key.py").write_text(
                'PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"""\n',
                encoding="utf-8",
            )
            (target / "tests" / "test_smoke.py").write_text(
                "def test_ok():\n    assert True\n", encoding="utf-8"
            )
            options = RunOptions(
                target=str(target),
                intent="Check whether this release is safe to ship.",
                personas_dir=str(PERSONAS),
                kill_on="critical",
                no_browser=True,
            )
            orchestrator = Orchestrator(root / "state", options)
            state = orchestrator.run()

            self.assertEqual(state.status, "halted")
            self.assertTrue(state.kill_switch.triggered)
            self.assertFalse(state.kill_switch.manual)
            self.assertIn("critical", state.halted_reason.lower())
            self.assertEqual(state.check("static.inventory").status, CheckStatus.PASSED.value)
            self.assertEqual(state.check("static.secrets").status, CheckStatus.FAILED.value)
            self.assertEqual(state.check("static.unsafe").status, CheckStatus.SKIPPED.value)
            self.assertLessEqual(state.confidence.score, 25.0)
            self.assertEqual(state.confidence.gate["status"], "fail")

            store = StateStore(root / "state")
            report = store.load_report()
            self.assertEqual(report["status"], "halted")
            self.assertEqual(report["decision"]["decision"], "hold")
            evidence_dir = root / "state" / report["evidence"]["directory"]
            self.assertTrue((evidence_dir / "manifest.json").exists())

    def test_secret_values_are_redacted_in_state_evidence_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
            )
            state = Orchestrator(root, options).run()
            self.assertEqual(state.status, "completed")
            self.assertGreaterEqual(len(state.findings), 1)

            harness_dir = root / ".harness"
            serialized = (harness_dir / "progress.json").read_text(encoding="utf-8")
            serialized += (harness_dir / "report.json").read_text(encoding="utf-8")
            for path in (harness_dir / "evidence").rglob("*"):
                if path.is_file():
                    serialized += path.read_text(encoding="utf-8", errors="ignore")

            raw_fixture_value = "a8f3k2m9q1z7"
            self.assertNotIn(raw_fixture_value, serialized)
            self.assertIn("[REDACTED", serialized)

    def test_kill_on_never_records_findings_without_halting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "project"
            target.mkdir()
            (target / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            (target / "tests").mkdir()
            (target / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            (target / ".env").write_text("FEATURE_FLAG=on\n", encoding="utf-8")
            (target / "poetry.lock").write_text("fixture==1.0\n", encoding="utf-8")
            (target / ".github" / "workflows").mkdir(parents=True)
            (target / ".github" / "workflows" / "ci.yml").write_text(
                "name: ci\non: [pull_request]\n", encoding="utf-8"
            )
            options = RunOptions(
                target=str(target),
                personas_dir=str(PERSONAS),
                kill_on="never",
                no_browser=True,
            )
            state = Orchestrator(root / "state", options).run()
            self.assertEqual(state.status, "completed")
            self.assertFalse(state.kill_switch.triggered)
            self.assertEqual(state.confidence.gate["status"], "warn")


class CliContractTests(unittest.TestCase):
    def test_status_without_a_run_reports_no_run(self):
        with tempfile.TemporaryDirectory() as directory:
            code, stdout, _ = run_cli(["status", "--root", directory])
            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "no_run")

    def test_run_emits_parseable_json_and_persists_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
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
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            self.assertEqual(report["schema_version"], "quality-harness.report/v1")
            self.assertEqual(report["status"], "completed")
            self.assertIn("confidence", report)
            self.assertIsInstance(report["confidence"]["score"], float)
            self.assertGreaterEqual(report["confidence"]["score"], 0.0)
            self.assertLessEqual(report["confidence"]["score"], 100.0)
            self.assertGreaterEqual(len(report["findings"]), 1)
            self.assertEqual(report["coverage"]["planned_not_run"], 0)
            self.assertEqual(report["coverage"]["executed"], report["coverage"]["total_checks"])

            store = StateStore(Path(directory))
            self.assertTrue(store.report_path.exists())
            self.assertEqual(store.load().run_id, report["run_id"])
            manifest = Path(directory) / report["evidence"]["directory"] / "manifest.json"
            self.assertTrue(manifest.exists())
            manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["summary"]["count"], len(report["checks"]))

    def test_relative_personas_dir_resolves_from_working_directory(self):
        relative = os.path.relpath(PERSONAS, Path.cwd())
        with tempfile.TemporaryDirectory() as directory:
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
                    relative,
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            self.assertEqual(report["status"], "completed")
            self.assertEqual(len(report["personas"]), 3)

    def test_fail_on_policy_returns_exit_code_one(self):
        with tempfile.TemporaryDirectory() as directory:
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--quiet",
                    "--fail-on",
                    "high",
                    "--root",
                    directory,
                    "--personas-dir",
                    str(PERSONAS),
                ]
            )
            self.assertEqual(code, 1)
            report = json.loads(stdout)
            self.assertEqual(report["status"], "completed")
            self.assertGreaterEqual(report["finding_counts"]["high"], 1)

    def test_status_and_report_read_the_saved_run(self):
        with tempfile.TemporaryDirectory() as directory:
            run_cli(
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
            code, stdout, _ = run_cli(["status", "--root", directory])
            self.assertEqual(code, 0)
            status = json.loads(stdout)
            self.assertEqual(status["status"], "completed")
            self.assertGreater(status["checks_by_status"]["passed"], 0)

            code, stdout, _ = run_cli(["report", "--root", directory])
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            self.assertEqual(report["schema_version"], "quality-harness.report/v1")

    def test_abort_writes_the_manual_kill_file(self):
        with tempfile.TemporaryDirectory() as directory:
            code, stdout, _ = run_cli(["abort", "--root", directory, "--reason", "operator stop"])
            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "kill_requested")
            store = StateStore(Path(directory))
            self.assertEqual(store.kill_request(), "operator stop")

    def test_unknown_persona_returns_a_structured_error(self):
        with tempfile.TemporaryDirectory() as directory:
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--quiet",
                    "--personas",
                    "does-not-exist",
                    "--root",
                    directory,
                    "--personas-dir",
                    str(PERSONAS),
                ]
            )
            self.assertEqual(code, 2)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("Unknown persona", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
