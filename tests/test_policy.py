"""Policy file and scoring policy tests."""

import json
import tempfile
import unittest
from pathlib import Path

from harness.cli import main
from harness.errors import PolicyError
from harness.scoring import Policy, load_policy, score_confidence
from harness.models import Check, CheckStatus

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies" / "advisory-v1.json"


class PolicyTests(unittest.TestCase):
    def _write_policy(self, directory, payload):
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_default_policy_reproduces_advisory_behavior(self):
        policy = load_policy(DEFAULT_POLICY)
        self.assertEqual(policy.id, "advisory-v1")
        self.assertEqual(policy.version, "1.0")
        checks = [
            Check(
                id="a",
                title="a",
                domain="functional",
                persona="functional",
                kind="static",
                intent="test",
                expected="test",
                base_weight=1.0,
                weight=1.0,
                status=CheckStatus.PASSED.value,
            )
        ]
        confidence = score_confidence(checks, [], policy=policy)
        self.assertEqual(confidence.score, 90.0)
        self.assertEqual(confidence.gate["policy"], "advisory-v1")
        self.assertEqual(confidence.breakdown["policy_id"], "advisory-v1")
        self.assertEqual(confidence.breakdown["policy_version"], "1.0")

    def test_policy_can_relax_a_gate_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_policy(
                directory,
                {
                    "schema_version": "quality-harness.policy/v1",
                    "id": "relaxed",
                    "version": "2.0",
                    "gate": {
                        "pass_score": 80.0,
                        "warn_score": 20.0,
                        "fail_on_critical": True,
                        "fail_on_high": False,
                        "coverage_warn": 0.8,
                        "coverage_fail": 0.7,
                    },
                },
            )
            self.assertEqual(load_policy(path).id, "relaxed")

    def test_invalid_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_policy(directory, {"id": "bad", "version": "1", "domain_weights": {}})
            with self.assertRaises(PolicyError):
                load_policy(path)

            unsafe = self._write_policy(
                directory,
                {
                    "schema_version": "quality-harness.policy/v1",
                    "id": "unsafe",
                    "version": "1",
                    "caps": {"critical": 50.0, "two_high": 40.0, "halted": 25.0},
                },
            )
            with self.assertRaises(PolicyError):
                load_policy(unsafe)

    def test_policy_file_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._write_policy(
                directory,
                {
                    "schema_version": "quality-harness.policy/v1",
                    "id": "bad",
                    "version": "1",
                    "unknown_field": True,
                },
            )
            import io
            from contextlib import redirect_stderr, redirect_stdout

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "run",
                        "--target",
                        "examples/sample-repo",
                        "--no-browser",
                        "--personas-dir",
                        "personas",
                        "--policy",
                        str(policy_path),
                        "--root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 2)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "error")
            self.assertIn("Unknown policy field", payload["error"]["message"])

    def test_report_names_policy_id_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path = self._write_policy(
                directory,
                {
                    "schema_version": "quality-harness.policy/v1",
                    "id": "custom-policy",
                    "version": "9.9",
                },
            )
            import io
            from contextlib import redirect_stderr, redirect_stdout

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(
                    [
                        "run",
                        "--target",
                        "examples/sample-repo",
                        "--no-browser",
                        "--personas-dir",
                        "personas",
                        "--policy",
                        str(policy_path),
                        "--root",
                        str(root),
                    ]
                )
            self.assertEqual(code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["policy"]["id"], "custom-policy")
            self.assertEqual(report["policy"]["version"], "9.9")
            self.assertEqual(report["confidence"]["gate"]["policy"], "custom-policy")


if __name__ == "__main__":
    unittest.main()
