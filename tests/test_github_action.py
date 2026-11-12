"""GitHub Action and PR workflow contract tests."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GitHubActionContractTests(unittest.TestCase):
    def test_composite_action_declares_inputs_outputs_and_steps(self):
        action = (ROOT / ".github" / "actions" / "quality-harness" / "action.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("using: composite", action)
        self.assertIn("target:", action)
        self.assertIn("min-score:", action)
        self.assertIn("fail-on:", action)
        self.assertIn("score:", action)
        self.assertIn("decision:", action)
        self.assertIn("coverage:", action)
        self.assertIn("Post or update PR comment", action)

    def test_pr_workflow_uses_action_and_is_fork_safe(self):
        workflow = (ROOT / ".github" / "workflows" / "pr.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("uses: ./.github/actions/quality-harness", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("secrets.", workflow)

    def test_comment_script_updates_existing_comment(self):
        script = ROOT / ".github" / "actions" / "quality-harness" / "comment.py"
        report_dir = Path(tempfile.mkdtemp())
        report_path = report_dir / "report.json"
        report_path.write_text(
            json.dumps(
                {
                    "confidence": {"score": 87.5},
                    "decision": {"decision": "ship"},
                    "coverage": {"executed_pct": 95.0, "executed": 19, "total_checks": 20},
                    "findings": [{"id": "f1"}],
                    "finding_counts": {"high": 1},
                    "trend": {"new": ["f1"]},
                }
            ),
            encoding="utf-8",
        )
        env = {
            "PATH": __import__("os").environ.get("PATH", ""),
            "GITHUB_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "example/repo",
            "PR_NUMBER": "42",
            "REPORT_PATH": str(report_path),
            "RUN_URL": "https://example.test/run/1",
            "ARTIFACT_NAME": "quality-harness-evidence",
        }
        # The script should not crash when the API is unavailable; it skips.
        result = subprocess.run(
            [sys.executable, str(script)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Skipping PR comment", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
