"""Billing sandbox and entitlement regression tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import cli
from harness.billing import (
    BillingFeatureFlag,
    BillingStore,
    InMemorySecretManager,
    SandboxBillingProvider,
)
from harness.quota import QuotaPlan
from harness.signup import SignupStore, verify_entitlement


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class StrictProvider(SandboxBillingProvider):
    """A sandbox provider that records every call for unexpected-call checks."""

    def __init__(self, root, expected_calls):
        super().__init__(root)
        self.expected_calls = list(expected_calls)
        self.actual_calls = []

    def _record(self, name):
        self.actual_calls.append(name)
        if name not in self.expected_calls:
            raise AssertionError("Unexpected provider call: {}".format(name))

    def create_customer(self, **kwargs):
        self._record("create_customer")
        return super().create_customer(**kwargs)

    def ensure_subscription(self, **kwargs):
        self._record("ensure_subscription")
        return super().ensure_subscription(**kwargs)

    def reconcile_invoice(self, **kwargs):
        self._record("reconcile_invoice")
        return super().reconcile_invoice(**kwargs)

    def cancel_subscription(self, **kwargs):
        self._record("cancel_subscription")
        return super().cancel_subscription(**kwargs)


PLAN_TABLE = [
    {
        "id": "free",
        "version": "2026-11",
        "limits": {"runs": {"soft": 1, "hard": 2}, "checks": {"hard": 10}},
    },
    {
        "id": "pro",
        "version": "2026-11",
        "limits": {"runs": {"soft": 10, "hard": 100}, "checks": {"hard": 1000}},
    },
    {
        "id": "enterprise",
        "version": "2026-11",
        "limits": {"runs": {"soft": 100, "hard": 1000}, "checks": {"hard": 10000}},
    },
]


class BillingRegressionTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.secrets = InMemorySecretManager({"billing/api_key": "secret-value"})

    def tearDown(self):
        self._temporary.cleanup()

    def _plan(self, payload):
        return QuotaPlan.from_dict(payload)

    def test_table_driven_entitlements_for_every_plan(self):
        for case in PLAN_TABLE:
            with self.subTest(plan=case["id"]):
                store = SignupStore(self.root / case["id"])
                plan = self._plan(case)
                result = store.signup(
                    "req-{}".format(case["id"]),
                    name=case["id"].title(),
                    plan=plan,
                    effective_at="2026-11-12T00:00:00Z",
                )
                entitlement = result["entitlement"]
                self.assertTrue(store.verify_entitlement(entitlement))
                self.assertEqual(entitlement["plan_id"], case["id"])
                self.assertEqual(entitlement["tier"], case["id"])
                self.assertEqual(entitlement["plan_version"], case["version"])
                self.assertEqual(entitlement["workspace_id"], result["workspace"]["id"])
                self.assertEqual(entitlement["period"], "2026-11")
                for resource, limit in case["limits"].items():
                    self.assertEqual(
                        entitlement["limits"][resource]["hard"],
                        limit.get("hard", limit.get("limit")),
                    )
                # Tampering any signed field is rejected.
                tampered = dict(entitlement)
                tampered["limits"]["runs"]["hard"] = 999999
                self.assertFalse(store.verify_entitlement(tampered))

    def test_upgrade_and_downgrade_plan_selection(self):
        store = SignupStore(self.root)
        free = self._plan(PLAN_TABLE[0])
        pro = self._plan(PLAN_TABLE[1])
        result = store.signup("req-upgrade", plan=free, effective_at="2026-11-12T00:00:00Z")
        workspace_id = result["workspace"]["id"]
        upgraded = store.select_plan(
            workspace_id, pro, effective_at="2026-12-01T00:00:00Z"
        )
        self.assertEqual(upgraded["status"], "plan_selected")
        self.assertEqual(upgraded["plan"]["id"], "pro")
        self.assertEqual(upgraded["entitlement"]["plan_id"], "pro")
        # Downgrade is allowed when current usage fits.
        downgraded = store.select_plan(
            workspace_id, free, effective_at="2027-01-01T00:00:00Z"
        )
        self.assertEqual(downgraded["plan"]["id"], "free")

    def test_downgrade_is_rejected_when_usage_exceeds_new_limit(self):
        from harness.quota import QuotaStore

        store = SignupStore(self.root)
        free = self._plan(PLAN_TABLE[0])
        pro = self._plan(PLAN_TABLE[1])
        result = store.signup("req-downgrade", plan=pro, effective_at="2026-11-12T00:00:00Z")
        workspace_id = result["workspace"]["id"]
        QuotaStore(self.root, plan=pro, workspace=workspace_id, enforce=True).consume(
            "runs", 5, now="2026-11-12T00:00:00Z"
        )
        with self.assertRaises(Exception):
            store.select_plan(
                workspace_id,
                QuotaPlan.from_dict(
                    {"id": "tiny", "limits": {"runs": {"hard": 1}}}
                ),
                effective_at="2026-11-12T00:00:00Z",
            )

    def test_subscription_cancellation(self):
        flag = BillingFeatureFlag(enabled=True, sandbox=True, workspaces=("ws_acme",))
        provider = StrictProvider(self.root, ["create_customer", "ensure_subscription", "cancel_subscription"])
        billing = BillingStore(
            self.root,
            feature_flag=flag,
            secret_manager=self.secrets,
            provider=provider,
        )
        billing.ensure_customer("ws_acme", name="Acme")
        billing.ensure_subscription("ws_acme", plan_id="pro")
        cancelled = billing.cancel_subscription("ws_acme", reason="customer request")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(billing.status("ws_acme")["last_status"], "cancelled")
        # Repeating cancellation is idempotent.
        repeated = billing.cancel_subscription("ws_acme", reason="customer request")
        self.assertEqual(repeated["status"], "already_cancelled")
        self.assertEqual(
            provider.actual_calls.count("cancel_subscription"), 1
        )

    def test_failed_payment_is_degraded_and_preserves_quota(self):
        flag = BillingFeatureFlag(enabled=True, sandbox=True, workspaces=("ws_acme",))
        provider = SandboxBillingProvider(self.root, fail_reconcile_times=10)
        billing = BillingStore(
            self.root,
            feature_flag=flag,
            secret_manager=self.secrets,
            provider=provider,
            max_retries=1,
        )
        failed = billing.record_payment_failure(
            "ws_acme", reason="card declined", amount=12.5, invoice_id="inv-1"
        )
        self.assertEqual(failed["status"], "payment_failed")
        status = billing.status("ws_acme")
        self.assertEqual(status["last_status"], "payment_failed")
        self.assertEqual(status["last_error"], "card declined")
        # A failed payment record does not create provider invoice state.
        self.assertEqual(provider._load().get("invoices", {}), {})

    def test_feature_flag_off_makes_no_provider_calls(self):
        provider = StrictProvider(self.root, [])
        billing = BillingStore(
            self.root,
            feature_flag=BillingFeatureFlag(enabled=False, sandbox=True, workspaces=("ws_acme",)),
            secret_manager=self.secrets,
            provider=provider,
        )
        self.assertEqual(billing.ensure_customer("ws_acme")["status"], "disabled")
        self.assertEqual(billing.ensure_subscription("ws_acme", plan_id="pro")["status"], "disabled")
        self.assertEqual(billing.cancel_subscription("ws_acme")["status"], "disabled")
        self.assertEqual(provider.actual_calls, [])

    def test_unexpected_provider_call_fails(self):
        provider = StrictProvider(self.root, ["create_customer"])
        billing = BillingStore(
            self.root,
            feature_flag=BillingFeatureFlag(enabled=True, sandbox=True, workspaces=("ws_acme",)),
            secret_manager=self.secrets,
            provider=provider,
        )
        result = billing.ensure_subscription("ws_acme", plan_id="pro")
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["error"]["type"], "AssertionError")

    def test_entitlement_rejects_expired_and_wrong_scope(self):
        store = SignupStore(self.root)
        plan = self._plan(PLAN_TABLE[0])
        result = store.signup("req-scope", plan=plan, effective_at="2026-11-12T00:00:00Z")
        entitlement = result["entitlement"]
        self.assertFalse(store.verify_entitlement(entitlement, workspace_id="ws_other"))
        self.assertFalse(store.verify_entitlement(entitlement, period="2026-12"))
        expired = store.issue_entitlement(
            result["workspace"]["id"],
            plan,
            period="2026-10",
            effective_at="2026-10-01T00:00:00Z",
            expires_at="2026-10-31T00:00:00Z",
        )
        self.assertFalse(store.verify_entitlement(expired, now="2026-11-12T00:00:00Z"))

    def test_module_level_verify_entitlement(self):
        store = SignupStore(self.root)
        plan = self._plan(PLAN_TABLE[1])
        result = store.signup("req-module", plan=plan, effective_at="2026-11-12T00:00:00Z")
        self.assertTrue(
            verify_entitlement(
                result["entitlement"], signing_key=store.signing_key
            )
        )


if __name__ == "__main__":
    unittest.main()
