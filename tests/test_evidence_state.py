"""Evidence integrity and durable state tests."""

import tempfile
import unittest
from pathlib import Path

from harness.evidence import EvidenceStore, sha256_bytes
from harness.models import RunState, Target
from harness.state import StateStore


class EvidenceTests(unittest.TestCase):
    def test_artifacts_are_hashed_and_verifiable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = EvidenceStore(root, "run-1")
            text_ref = store.save_text("note", "hello", kind="text")
            image_ref = store.save_screenshot("shot", b"\x89PNG\r\n\x1a\nfake-image")
            self.assertTrue(text_ref.endswith("note.txt"))
            self.assertTrue(image_ref.endswith("shot.png"))
            self.assertEqual(store.summary()["count"], 2)

            manifest = store.finalize({"run_id": "run-1"})
            self.assertTrue((root / manifest["manifest_path"]).exists())
            verification = store.verify()
            self.assertEqual(verification["checked"], 2)
            self.assertEqual(verification["mismatches"], [])
            self.assertEqual(verification["missing"], [])

    def test_tampering_and_deletion_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = EvidenceStore(root, "run-2")
            first = store.save_text("first", "original")
            second = store.save_text("second", "will-delete")
            store.finalize()
            (root / first).write_text("tampered", encoding="utf-8")
            (root / second).unlink()
            verification = store.verify()
            self.assertEqual(len(verification["mismatches"]), 1)
            self.assertEqual(verification["missing"], [second])

    def test_existing_manifest_is_reloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = EvidenceStore(root, "run-3")
            store.save_text("kept", "value")
            store.finalize()
            reopened = EvidenceStore(root, "run-3")
            self.assertEqual(reopened.summary()["count"], 1)

    def test_hash_helper_matches_bytes(self):
        self.assertEqual(
            sha256_bytes(b"hello"),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )


class StateTests(unittest.TestCase):
    def test_progress_roundtrip_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = StateStore(root)
            state = RunState(
                run_id="run-1",
                workflow_id="release-confidence",
                target=Target(raw=".", kind="directory", resolved=str(root)),
                intent="Verify state survives a restart.",
            )
            store.save(state)
            self.assertTrue(store.exists())
            loaded = store.load()
            self.assertEqual(loaded.run_id, "run-1")
            self.assertEqual(loaded.intent, "Verify state survives a restart.")
            self.assertEqual(loaded.target.kind, "directory")
            self.assertTrue(store.report_path.parent.name == ".harness")

    def test_manual_kill_file_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            self.assertIsNone(store.kill_request())
            store.request_kill("stop before touching production")
            self.assertEqual(store.kill_request(), "stop before touching production")
            store.clear_kill()
            self.assertIsNone(store.kill_request())

    def test_events_are_append_only_json_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            store.append_event({"event": "started"})
            store.append_event({"event": "finished"})
            lines = store.events_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn('"event": "started"', lines[0])
            self.assertIn('"timestamp"', lines[0])


if __name__ == "__main__":
    unittest.main()
