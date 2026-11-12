"""Evidence manifest signing and tamper detection tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness import cli
from harness.evidence import EvidenceStore


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class EvidenceSigningTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self):
        self._temporary.cleanup()

    def _store(self, run_id="run-1"):
        store = EvidenceStore(self.root, run_id)
        store.save_text("note", "hello", check_id="static.tests")
        store.finalize({"status": "completed"})
        return store

    def test_manifest_is_signed_and_verifies(self):
        store = self._store()
        manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        self.assertIn("signature", manifest)
        self.assertIn("signature_algorithm", manifest)
        result = store.verify()
        self.assertFalse(result["tampered"])
        self.assertTrue(result["signature_valid"])
        self.assertEqual(result["checked"], 1)

    def test_tampering_one_byte_in_artifact_fails_with_path_and_check_id(self):
        store = self._store()
        artifact = self.root / ".harness" / "evidence" / "run-1" / "note.txt"
        artifact.write_text("tampered", encoding="utf-8")
        result = store.verify()
        self.assertTrue(result["tampered"])
        self.assertEqual(result["signature_valid"], True)
        self.assertEqual(result["affected"][0]["path"], ".harness/evidence/run-1/note.txt")
        self.assertEqual(result["affected"][0]["check_id"], "static.tests")
        self.assertEqual(result["affected"][0]["reason"], "hash_mismatch")
        self.assertEqual(result["confidence_cap"], 0.0) if "confidence_cap" in result else True

    def test_tampering_manifest_signature_fails(self):
        store = self._store()
        manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        manifest["signature"] = "0" * 64
        store.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        result = store.verify()
        self.assertTrue(result["tampered"])
        self.assertFalse(result["signature_valid"])
        self.assertTrue(any(item["reason"] == "signature_invalid" for item in result["affected"]))

    def test_missing_artifact_is_reported(self):
        store = self._store()
        artifact = self.root / ".harness" / "evidence" / "run-1" / "note.txt"
        artifact.unlink()
        result = store.verify()
        self.assertTrue(result["tampered"])
        self.assertIn(".harness/evidence/run-1/note.txt", result["missing"])
        self.assertEqual(result["affected"][0]["reason"], "missing")

    def test_verification_works_offline(self):
        store = self._store()
        # No network calls are made by verify.
        result = store.verify()
        self.assertIn("alert", result)
        self.assertEqual(result["alert"]["level"], "ok")

    def test_cli_verify_evidence_reports_tamper_and_caps_confidence(self):
        store = self._store()
        artifact = self.root / ".harness" / "evidence" / "run-1" / "note.txt"
        artifact.write_text("tampered", encoding="utf-8")
        code, stdout, _ = run_cli(
            ["verify-evidence", "--root", str(self.root), "--run-id", "run-1"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertTrue(payload["tampered"])
        self.assertEqual(payload["confidence_cap"], 0.0)
        self.assertEqual(payload["decision"], "hold")
        self.assertEqual(payload["affected"][0]["check_id"], "static.tests")

    def test_checkpoint_manifest_is_signed(self):
        store = EvidenceStore(self.root, "run-2")
        store.save_text("note", "hello", check_id="static.tests")
        store.checkpoint()
        manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
        self.assertIn("signature", manifest)
        self.assertTrue(store.verify()["signature_valid"])


if __name__ == "__main__":
    unittest.main()
