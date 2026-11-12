"""Tenant retention, signed export, and deletion tests."""

import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from harness import cli
from harness.queue import JobQueue
from harness.retention import RetentionStore


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class RetentionContractTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.personas = (Path.cwd() / "personas").resolve()
        self.target = (Path.cwd() / "examples" / "sample-repo").resolve()
        self.store = RetentionStore(self.root)

    def tearDown(self):
        self._temporary.cleanup()

    def test_default_retention_is_explicit(self):
        policy = self.store.policy("ws_acme")
        self.assertEqual(policy["retention_days"], 30)
        self.assertEqual(policy["grace_period_days"], 7)
        self.assertTrue(policy["default"])

    def test_set_retention_updates_policy(self):
        policy = self.store.set_retention("ws_acme", 14)
        self.assertEqual(policy["retention_days"], 14)
        self.assertFalse(policy["default"])

    def test_export_creates_signed_manifest_and_no_cross_tenant_paths(self):
        # Seed a tenant root with a file.
        tenant_root = self.root / ".harness" / "tenants" / "ws_acme"
        (tenant_root / "evidence").mkdir(parents=True, exist_ok=True)
        (tenant_root / "evidence" / "a.txt").write_text("secret", encoding="utf-8")

        result = self.store.export("ws_acme")
        self.assertEqual(result["status"], "exported")
        archive_path = Path(result["archive"])
        self.assertTrue(archive_path.is_file())
        verification = self.store.verify_export(archive_path)
        self.assertTrue(verification["valid"])
        self.assertEqual(verification["workspace_id"], "ws_acme")

        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            self.assertIn("manifest.json", names)
            self.assertTrue(any(name.startswith("tenant/") for name in names))
            for name in names:
                self.assertNotIn("ws_other", name)
                self.assertFalse(name.startswith("/"))
                self.assertNotIn("..", name.split("/"))

    def test_verify_export_rejects_tampering(self):
        tenant_root = self.root / ".harness" / "tenants" / "ws_acme"
        tenant_root.mkdir(parents=True, exist_ok=True)
        (tenant_root / "state.json").write_text("{}", encoding="utf-8")
        archive_path = Path(self.store.export("ws_acme")["archive"])
        # Tamper with the archive copy.
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(archive_path) as source:
            with zipfile.ZipFile(tampered, "w") as target:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename == "manifest.json":
                        manifest = json.loads(data)
                        manifest["workspace_id"] = "ws_other"
                        data = json.dumps(manifest).encode("utf-8")
                    target.writestr(info, data)
        self.assertFalse(self.store.verify_export(tampered)["valid"])

    def test_soft_delete_then_hard_delete_after_grace(self):
        result = self.store.soft_delete("ws_acme", grace_days=1, actor="tester")
        self.assertEqual(result["status"], "soft_deleted")
        with self.assertRaises(Exception):
            self.store.hard_delete("ws_acme", now=result["deleted_at"])
        hard = self.store.hard_delete(
            "ws_acme", force=True, actor="tester"
        )
        self.assertEqual(hard["status"], "deleted")
        self.assertTrue(
            any(event["event"] == "hard_deleted" for event in self.store.audit_log())
        )

    def test_hard_delete_removes_tenant_state_queue_and_exports(self):
        queue = JobQueue(self.root, tenant_root=self.root / "tenants")
        queue.submit("ws_acme", str(self.target), options={"no_browser": True})
        tenant_root = self.root / ".harness" / "tenants" / "ws_acme"
        tenant_root.mkdir(parents=True, exist_ok=True)
        (tenant_root / "state.json").write_text("{}", encoding="utf-8")
        export = self.store.export("ws_acme")
        export_path = Path(export["archive"])
        self.assertTrue(export_path.is_file())

        result = self.store.hard_delete("ws_acme", force=True)
        self.assertEqual(result["removed"]["tenant_root"], True)
        self.assertGreaterEqual(result["removed"]["queue_jobs"], 1)
        self.assertGreaterEqual(result["removed"]["cached_exports"], 1)
        self.assertFalse(tenant_root.exists())
        self.assertFalse(export_path.exists())
        self.assertEqual(queue.list_jobs("ws_acme"), [])
        # Audit survives deletion.
        self.assertTrue(self.store.audit_log())

    def test_hard_delete_does_not_touch_other_tenant(self):
        left_root = self.root / ".harness" / "tenants" / "ws_left"
        right_root = self.root / ".harness" / "tenants" / "ws_right"
        left_root.mkdir(parents=True, exist_ok=True)
        right_root.mkdir(parents=True, exist_ok=True)
        (left_root / "left.txt").write_text("left", encoding="utf-8")
        (right_root / "right.txt").write_text("right", encoding="utf-8")

        self.store.hard_delete("ws_left", force=True)
        self.assertFalse(left_root.exists())
        self.assertTrue(right_root.exists())
        self.assertEqual((right_root / "right.txt").read_text(), "right")

    def test_deletion_of_tenant_with_runs(self):
        queue = self.store.queue
        queue.personas_dir = self.personas
        job = queue.submit("ws_acme", str(self.target), options={"no_browser": True, "personas_dir": str(self.personas)})
        self.assertEqual(queue.process_next()["status"], "completed")
        result = self.store.hard_delete("ws_acme", force=True)
        self.assertEqual(result["status"], "deleted")
        self.assertFalse((self.root / ".harness" / "tenants" / "ws_acme").exists())


if __name__ == "__main__":
    unittest.main()
