"""Baseline suppression tests."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from harness.cli import main
from harness.errors import StateError
from harness.state import StateStore, SuppressionStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-repo"


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class SuppressionTests(unittest.TestCase):
    def _future(self, days=365):
        return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def test_suppression_requires_owner_reason_and_future_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SuppressionStore(root)
            with self.assertRaises(StateError):
                store.add("fp", owner="", reason="reason", expires_at=self._future())
            with self.assertRaises(StateError):
                store.add("fp", owner="owner", reason="", expires_at=self._future())
            with self.assertRaises(StateError):
                store.add("fp", owner="owner", reason="reason", expires_at="2000-01-01T00:00:00Z")
            with self.assertRaises(StateError):
                store.add("fp", owner="owner", reason="reason", expires_at="not-a-date")

            code, stdout, _ = run_cli(
                [
                    "suppress",
                    "add",
                    "fp",
                    "--owner",
                    "",
                    "--reason",
                    "reason",
                    "--expires",
                    self._future(),
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 2)
            payload = json.loads(stdout)
            self.assertEqual(payload["status"], "error")

    def test_suppressed_finding_stays_visible_without_penalty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "state"
            code, _, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(run_root),
                ]
            )
            self.assertEqual(code, 0)
            report = (run_root / ".harness" / "report.json").read_text(encoding="utf-8")
            report = __import__("json").loads(report)
            fingerprint = report["findings"][0]["fingerprint"]
            original_score = report["confidence"]["score"]
            original_penalty = report["confidence"]["penalties"]["severity_total"]

            code, _, _ = run_cli(
                [
                    "suppress",
                    "add",
                    fingerprint,
                    "--owner",
                    "alice",
                    "--reason",
                    "known and accepted",
                    "--ticket",
                    "T-1",
                    "--expires",
                    self._future(),
                    "--root",
                    str(run_root),
                ]
            )
            self.assertEqual(code, 0)

            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(run_root),
                ]
            )
            self.assertEqual(code, 0)
            report = __import__("json").loads(stdout)
            suppressed = next(
                finding for finding in report["findings"] if finding["fingerprint"] == fingerprint
            )
            self.assertTrue(suppressed["suppressed"])
            self.assertEqual(suppressed["suppression_owner"], "alice")
            self.assertEqual(suppressed["suppression_ticket"], "T-1")
            self.assertGreater(report["confidence"]["score"], original_score)
            self.assertLess(report["confidence"]["penalties"]["severity_total"], original_penalty)

    def test_expired_suppression_raises_a_low_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "state"
            store = SuppressionStore(run_root)
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / ".harness").mkdir(parents=True, exist_ok=True)
            store._save(
                [
                    {
                        "fingerprint": "expired-fingerprint",
                        "owner": "alice",
                        "reason": "known",
                        "ticket": "T-2",
                        "expires_at": "2000-01-01T00:00:00Z",
                        "created_at": "2000-01-01T00:00:00Z",
                    }
                ]
            )

            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(run_root),
                ]
            )
            self.assertEqual(code, 0)
            report = __import__("json").loads(stdout)
            expired = [
                finding for finding in report["findings"] if finding["fingerprint"] == "suppression-expired:expired-fingerprint"
            ]
            self.assertEqual(len(expired), 1)
            self.assertEqual(expired[0]["severity"], "low")
            self.assertIn("Expired suppression", expired[0]["title"])

    def test_every_suppression_change_appends_an_audit_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_root = root / "state"
            run_root.mkdir(parents=True, exist_ok=True)
            (run_root / ".harness").mkdir(parents=True, exist_ok=True)
            code, _, _ = run_cli(
                [
                    "suppress",
                    "add",
                    "fp-audit",
                    "--owner",
                    "alice",
                    "--reason",
                    "known",
                    "--expires",
                    self._future(),
                    "--root",
                    str(run_root),
                ]
            )
            self.assertEqual(code, 0)
            code, _, _ = run_cli(
                [
                    "suppress",
                    "remove",
                    "fp-audit",
                    "--root",
                    str(run_root),
                ]
            )
            self.assertEqual(code, 0)
            events = (run_root / ".harness" / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn('"event": "suppression_added"', events)
            self.assertIn('"event": "suppression_removed"', events)


if __name__ == "__main__":
    unittest.main()
