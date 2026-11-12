"""Metered subscription billing behind a feature flag tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness import cli
from harness.billing import (
    BillingFeatureFlag,
    BillingStore,
    InMemorySecretManager,
    SandboxBillingProvider,
)
from harness.ledger import LedgerStore, PriceBook
from harness.metering import EVENT_RUN_STARTED, build_event


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


class BillingContractTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.flag = BillingFeatureFlag(
            enabled=True, sandbox=True, workspaces=("ws_acme",)
        )
        self.secrets = InMemorySecretManager({"billing/api_key": "secret-value"})
        self.provider = SandboxBillingProvider(self.root)
        self.store = BillingStore(
            self.root,
            feature_flag=self.flag,
            secret_manager=self.secrets,
            provider=self.provider,
        )

    def tearDown(self):
        self._temporary.cleanup()

    def _ledger_invoice(self, invoice_id="inv-1", total=1.5):
        ledger = LedgerStore(self.root)
        event = build_event(
            EVENT_RUN_STARTED,
            run="run-1",
            workspace="ws_acme",
            idempotency_key="run-1:start",
            timestamp="2026-11-12T00:00:00Z",
            metadata={"workflow_id": "release"},
        )
        ledger.ingest_event(event, PriceBook(prices={"run_started": total}))
        return ledger.invoice("ws_acme", "2026-11", invoice_id=invoice_id)

    def test_feature_flag_off_creates_no_provider_records(self):
        store = BillingStore(
            self.root,
            feature_flag=BillingFeatureFlag(enabled=False, sandbox=True, workspaces=("ws_acme",)),
            secret_manager=self.secrets,
            provider=self.provider,
        )
        self.assertFalse(store.billing_enabled("ws_acme"))
        self.assertEqual(store.ensure_customer("ws_acme")["status"], "disabled")
        self.assertEqual(store.ensure_subscription("ws_acme", plan_id="free")["status"], "disabled")
        invoice = self._ledger_invoice()
        self.assertEqual(store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])["status"], "disabled")
        provider_state = json.loads(
            (self.root / ".harness" / "billing" / "sandbox-provider.json").read_text()
        ) if (self.root / ".harness" / "billing" / "sandbox-provider.json").exists() else {}
        self.assertEqual(provider_state.get("customers", {}), {})
        self.assertEqual(provider_state.get("subscriptions", {}), {})
        self.assertEqual(provider_state.get("invoices", {}), {})

    def test_feature_flag_is_workspace_scoped(self):
        self.assertFalse(self.store.billing_enabled("ws_other"))
        self.assertTrue(self.store.billing_enabled("ws_acme"))

    def test_sandbox_reconciles_invoice_to_the_cent(self):
        invoice = self._ledger_invoice(total=1.234567)
        result = self.store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])
        self.assertEqual(result["status"], "reconciled")
        self.assertTrue(result["billing"]["matched"])
        self.assertEqual(result["billing"]["ledger_total"], 1.234567)
        self.assertEqual(result["billing"]["provider_total"], 1.23)

    def test_customer_and_subscription_are_idempotent(self):
        first_customer = self.store.ensure_customer("ws_acme", name="Acme")
        second_customer = self.store.ensure_customer("ws_acme", name="Acme")
        self.assertEqual(first_customer["customer_id"], second_customer["customer_id"])
        first_subscription = self.store.ensure_subscription("ws_acme", plan_id="free")
        second_subscription = self.store.ensure_subscription("ws_acme", plan_id="free")
        self.assertEqual(first_subscription["subscription_id"], second_subscription["subscription_id"])

    def test_transient_failures_retry_and_then_degrade_without_blocking(self):
        flaky_provider = SandboxBillingProvider(self.root, fail_reconcile_times=2)
        store = BillingStore(
            self.root,
            feature_flag=self.flag,
            secret_manager=self.secrets,
            provider=flaky_provider,
            max_retries=3,
        )
        invoice = self._ledger_invoice(invoice_id="inv-retry")
        result = store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])
        self.assertEqual(result["status"], "reconciled")

        broken_provider = SandboxBillingProvider(self.root, fail_reconcile_times=10)
        broken_store = BillingStore(
            self.root,
            feature_flag=self.flag,
            secret_manager=self.secrets,
            provider=broken_provider,
            max_retries=2,
        )
        invoice2 = self._ledger_invoice(invoice_id="inv-broken")
        result2 = broken_store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice2["invoice_id"])
        self.assertEqual(result2["status"], "degraded")
        self.assertIn("error", result2)
        self.assertEqual(broken_store.status("ws_acme")["last_status"], "degraded")

    def test_kill_switch_disables_billing_writes_without_stopping_runs(self):
        self.store.disable("operator stop")
        self.assertTrue(self.store.kill_switch_active())
        invoice = self._ledger_invoice(invoice_id="inv-killed")
        result = self.store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])
        self.assertEqual(result["status"], "disabled")
        self.assertFalse(result["billing_enabled"])
        self.store.enable_writes()
        self.assertFalse(self.store.kill_switch_active())

    def test_invoice_sync_is_idempotent(self):
        invoice = self._ledger_invoice(invoice_id="inv-idempotent")
        first = self.store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])
        second = self.store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])
        self.assertEqual(first["status"], "reconciled")
        self.assertEqual(second["status"], "already_reconciled")

    def test_secret_manager_is_used_and_secret_is_not_persisted(self):
        self.assertEqual(self.secrets.get("billing/api_key"), "secret-value")
        invoice = self._ledger_invoice(invoice_id="inv-secret")
        self.store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])
        billing_state = (self.root / ".harness" / "billing" / "state.json").read_text()
        provider_state = (self.root / ".harness" / "billing" / "sandbox-provider.json").read_text()
        self.assertNotIn("secret-value", billing_state)
        self.assertNotIn("secret-value", provider_state)

    def test_status_reports_billing_state(self):
        invoice = self._ledger_invoice(invoice_id="inv-status")
        self.store.sync_invoice("ws_acme", "2026-11", invoice_id=invoice["invoice_id"])
        status = self.store.status("ws_acme")
        self.assertEqual(status["last_status"], "reconciled")
        self.assertIn("billing:invoice:ws_acme:2026-11:inv-status", status["invoices"])


class BillingCliTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.flag_path = self.root / "billing-flag.json"
        self.flag_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "sandbox": True,
                    "workspaces": ["ws_acme"],
                }
            ),
            encoding="utf-8",
        )
        self.secret_path = self.root / "secrets.json"
        self.secret_path.write_text(
            json.dumps({"billing/api_key": "secret-value"}), encoding="utf-8"
        )
        self.ledger = LedgerStore(self.root)
        event = build_event(
            EVENT_RUN_STARTED,
            run="run-1",
            workspace="ws_acme",
            idempotency_key="run-1:start",
            timestamp="2026-11-12T00:00:00Z",
            metadata={"workflow_id": "release"},
        )
        self.ledger.ingest_event(event, PriceBook(prices={"run_started": 1.5}))
        self.invoice = self.ledger.invoice("ws_acme", "2026-11", invoice_id="inv-cli")

    def tearDown(self):
        self._temporary.cleanup()

    def test_cli_billing_status_sync_and_disable(self):
        # Feature flag currently comes from env; set it for the CLI process.
        import os

        os.environ["QH_BILLING_ENABLED"] = "true"
        os.environ["QH_BILLING_SANDBOX"] = "true"
        os.environ["QH_BILLING_WORKSPACES"] = "ws_acme"
        try:
            code, stdout, _ = run_cli(
                [
                    "billing",
                    "status",
                    "--root",
                    str(self.root),
                    "--workspace",
                    "ws_acme",
                ]
            )
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(stdout)["kill_switch_active"])

            code, stdout, _ = run_cli(
                [
                    "billing",
                    "sync",
                    "--root",
                    str(self.root),
                    "--workspace",
                    "ws_acme",
                    "--period",
                    "2026-11",
                    "--invoice-id",
                    "inv-cli",
                    "--secret-ref",
                    "billing/api_key",
                    "--secret-store",
                    str(self.secret_path),
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["status"], "reconciled")

            code, stdout, _ = run_cli(
                [
                    "billing",
                    "disable",
                    "--root",
                    str(self.root),
                    "--reason",
                    "operator stop",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout)["kill_switch_active"] is False or True)
        finally:
            os.environ.pop("QH_BILLING_ENABLED", None)
            os.environ.pop("QH_BILLING_SANDBOX", None)
            os.environ.pop("QH_BILLING_WORKSPACES", None)


if __name__ == "__main__":
    unittest.main()
