"""Self-serve signup, free allowance, plan selection, and entitlement tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness import cli
from harness.quota import QuotaPlan
from harness.signup import SignupStore, verify_entitlement


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class SignupContractTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.store = SignupStore(self.root)

    def tearDown(self):
        self._temporary.cleanup()

    def test_signup_creates_one_workspace_owner_key_and_entitlement(self):
        plan = QuotaPlan(
            id="free", version="1", limits={"runs": {"soft": 1, "hard": 2}}
        )
        result = self.store.signup(
            "req-1", name="Acme", plan=plan, effective_at="2026-11-12T00:00:00Z"
        )
        self.assertEqual(result["status"], "created")
        self.assertFalse(result["replayed"])
        self.assertEqual(result["workspace"]["name"], "Acme")
        self.assertTrue(result["key"]["secret"].startswith("qh_"))
        self.assertEqual(result["key"]["scopes"], ["admin", "run", "read", "export"])
        self.assertEqual(result["plan"]["id"], "free")
        self.assertTrue(result["entitlement"]["signature"])
        self.assertEqual(result["usage"]["usage"]["runs"], 0)
        self.assertEqual(result["usage"]["remaining"]["runs"], 2)
        self.assertEqual(len(self.store._load()["workspaces"]), 1)
        self.assertEqual(len(self.store._load()["requests"]), 1)

    def test_signup_replay_is_idempotent(self):
        plan = QuotaPlan(id="free", limits={"runs": {"hard": 2}})
        first = self.store.signup("req-1", name="Acme", plan=plan)
        second = self.store.signup("req-1", name="Acme", plan=plan)
        self.assertEqual(second["status"], "replayed")
        self.assertTrue(second["replayed"])
        self.assertEqual(second["workspace"]["id"], first["workspace"]["id"])
        self.assertEqual(second["key"]["id"], first["key"]["id"])
        self.assertIsNone(second["key"]["secret"])
        self.assertEqual(len(self.store._load()["workspaces"]), 1)
        self.assertEqual(len(self.store._load()["requests"]), 1)

    def test_free_allowance_is_visible_from_first_request(self):
        plan = QuotaPlan(id="free", limits={"checks": {"soft": 5, "hard": 10}})
        result = self.store.signup("req-1", plan=plan)
        usage = self.store.usage(result["workspace"]["id"])
        self.assertEqual(usage["usage"]["checks"], 0)
        self.assertEqual(usage["remaining"]["checks"], 10)
        self.assertEqual(usage["limits"]["checks"]["hard"], 10)
        self.assertEqual(usage["plan"]["id"], "free")

    def test_no_cross_workspace_identifiers_are_returned(self):
        plan = QuotaPlan(id="free", limits={"runs": {"hard": 2}})
        first = self.store.signup("req-1", name="Acme", plan=plan)
        second = self.store.signup("req-2", name="Other", plan=plan)
        self.assertNotEqual(first["workspace"]["id"], second["workspace"]["id"])
        with self.assertRaises(Exception):
            self.store.usage("ws_does_not_exist")
        self.assertIsNone(self.store.get_signup("ws_does_not_exist"))

    def test_plan_selection_records_effective_date(self):
        plan = QuotaPlan(id="free", limits={"runs": {"hard": 2}})
        result = self.store.signup("req-1", plan=plan, effective_at="2026-11-12T00:00:00Z")
        new_plan = QuotaPlan(id="pro", limits={"runs": {"hard": 5}})
        selection = self.store.select_plan(
            result["workspace"]["id"],
            new_plan,
            effective_at="2026-12-01T00:00:00Z",
        )
        self.assertEqual(selection["status"], "plan_selected")
        self.assertEqual(selection["plan"]["id"], "pro")
        self.assertEqual(selection["effective_at"], "2026-12-01T00:00:00Z")
        self.assertEqual(selection["period"], "2026-12")
        record = self.store.get_signup(result["workspace"]["id"])
        self.assertEqual(record["plan"]["id"], "pro")
        self.assertEqual(record["effective_at"], "2026-12-01T00:00:00Z")
        self.assertEqual(len(record["plan_selections"]), 1)

    def test_plan_selection_cannot_downgrade_existing_usage(self):
        plan = QuotaPlan(id="free", limits={"runs": {"hard": 2}})
        result = self.store.signup("req-1", plan=plan, effective_at="2026-11-12T00:00:00Z")
        workspace_id = result["workspace"]["id"]
        quota = self.store._usage_snapshot  # ensure state exists
        from harness.quota import QuotaStore

        QuotaStore(self.root, plan=plan, workspace=workspace_id, enforce=True).consume(
            "runs", 2, now="2026-11-12T00:00:00Z"
        )
        with self.assertRaises(Exception):
            self.store.select_plan(
                workspace_id,
                QuotaPlan(id="tiny", limits={"runs": {"hard": 1}}),
                effective_at="2026-11-12T00:00:00Z",
            )

    def test_entitlement_is_signed_and_rejected_after_tampering_or_expiry(self):
        plan = QuotaPlan(id="free", limits={"runs": {"hard": 2}})
        result = self.store.signup("req-1", plan=plan, effective_at="2026-11-12T00:00:00Z")
        entitlement = result["entitlement"]
        self.assertTrue(self.store.verify_entitlement(entitlement))
        self.assertTrue(
            verify_entitlement(entitlement, signing_key=self.store.signing_key)
        )

        tampered = dict(entitlement)
        tampered["plan_id"] = "enterprise"
        self.assertFalse(self.store.verify_entitlement(tampered))

        expired = self.store.issue_entitlement(
            result["workspace"]["id"],
            plan,
            period="2026-10",
            effective_at="2026-10-01T00:00:00Z",
            expires_at="2026-10-31T00:00:00Z",
        )
        self.assertFalse(
            self.store.verify_entitlement(expired, now="2026-11-12T00:00:00Z")
        )
        self.assertTrue(
            self.store.verify_entitlement(expired, now="2026-10-15T00:00:00Z")
        )

    def test_entitlement_rejects_wrong_workspace_or_period(self):
        plan = QuotaPlan(id="free", limits={"runs": {"hard": 2}})
        result = self.store.signup("req-1", plan=plan, effective_at="2026-11-12T00:00:00Z")
        entitlement = result["entitlement"]
        self.assertFalse(
            self.store.verify_entitlement(
                entitlement, workspace_id="ws_other"
            )
        )
        self.assertFalse(self.store.verify_entitlement(entitlement, period="2026-12"))

    def test_entitlements_module_alias_works(self):
        from harness import entitlements

        self.assertIs(entitlements.SignupStore, self.store.__class__)


class SignupCliTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.plan_path = self.root / "free-plan.json"
        self.plan_path.write_text(
            json.dumps(
                {
                    "id": "free",
                    "version": "1",
                    "limits": {"runs": {"soft": 1, "hard": 2}},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self._temporary.cleanup()

    def test_cli_signup_create_usage_and_verify(self):
        code, stdout, _ = run_cli(
            [
                "signup",
                "create",
                "--root",
                str(self.root),
                "--request-id",
                "req-1",
                "--name",
                "Acme",
                "--plan",
                str(self.plan_path),
                "--effective-at",
                "2026-11-12T00:00:00Z",
            ]
        )
        self.assertEqual(code, 0)
        created = json.loads(stdout)
        self.assertEqual(created["status"], "created")
        workspace_id = created["workspace"]["id"]
        entitlement_path = self.root / "entitlement.json"
        entitlement_path.write_text(
            json.dumps(created["entitlement"]), encoding="utf-8"
        )

        code, stdout, _ = run_cli(
            [
                "signup",
                "usage",
                "--root",
                str(self.root),
                "--workspace",
                workspace_id,
            ]
        )
        self.assertEqual(code, 0)
        usage = json.loads(stdout)
        self.assertEqual(usage["usage"]["runs"], 0)
        self.assertEqual(usage["remaining"]["runs"], 2)

        code, stdout, _ = run_cli(
            [
                "signup",
                "verify",
                "--root",
                str(self.root),
                "--entitlement",
                str(entitlement_path),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["valid"])

        # Replay is idempotent.
        code, stdout, _ = run_cli(
            [
                "signup",
                "create",
                "--root",
                str(self.root),
                "--request-id",
                "req-1",
                "--name",
                "Acme",
                "--plan",
                str(self.plan_path),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["replayed"])


if __name__ == "__main__":
    unittest.main()
