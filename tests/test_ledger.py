"""Append-only usage ledger, period close, correction, and invoice tests."""

import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness import cli
from harness.auth import SCOPE_READ, SCOPE_RUN, AuthStore
from harness.errors import LedgerError, LedgerPeriodClosed
from harness.ledger import (
    ENTRY_ADJUSTMENT,
    ENTRY_CORRECTION,
    INVOICE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    LedgerStore,
    PriceBook,
    invoice_to_csv,
    normalize_period,
    period_bounds,
)
from harness.metering import (
    EVENT_BROWSER_MINUTE_CONSUMED,
    EVENT_CHECK_EXECUTED,
    EVENT_EVIDENCE_BYTE_STORED,
    EVENT_EXPORT_GENERATED,
    EVENT_RUN_STARTED,
    MeteringEmitter,
    build_event,
)

WORKSPACE = "ws_acme"
PERIOD = "2026-11"
TIMESTAMP = "2026-11-12T00:00:00Z"
METADATA = {
    EVENT_RUN_STARTED: {"workflow_id": "release-confidence"},
    EVENT_CHECK_EXECUTED: {"check_id": "static.tests", "status": "passed"},
    EVENT_BROWSER_MINUTE_CONSUMED: {"mode": "desktop"},
    EVENT_EVIDENCE_BYTE_STORED: {
        "kind": "text",
        "path": ".harness/evidence/run/check.txt",
    },
    EVENT_EXPORT_GENERATED: {"format": "sarif"},
}


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def make_event(
    event_type,
    key,
    *,
    quantity=1,
    workspace=WORKSPACE,
    timestamp=TIMESTAMP,
    run="run-1",
    metadata=None,
):
    return build_event(
        event_type,
        run=run,
        workspace=workspace,
        quantity=quantity,
        idempotency_key=key,
        timestamp=timestamp,
        metadata=metadata or METADATA[event_type],
    )


def price_book(**overrides):
    prices = {
        EVENT_RUN_STARTED: 1.50,
        EVENT_CHECK_EXECUTED: 0.25,
        EVENT_BROWSER_MINUTE_CONSUMED: 0.10,
        EVENT_EVIDENCE_BYTE_STORED: 0.001,
        EVENT_EXPORT_GENERATED: 0.05,
    }
    prices.update(overrides)
    return PriceBook(prices=prices, currency="USD")


class LedgerContractTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.ledger = LedgerStore(self.root)

    def tearDown(self):
        self._temporary.cleanup()

    def test_price_book_parses_flat_and_nested_prices(self):
        book = PriceBook.from_dict(
            {
                "schema_version": "quality-harness.price-book/v1",
                "currency": "EUR",
                "prices": {
                    EVENT_RUN_STARTED: 1.5,
                    EVENT_CHECK_EXECUTED: {"unit_price": 0.25},
                },
            }
        )
        self.assertEqual(book.currency, "EUR")
        self.assertEqual(book.price(EVENT_RUN_STARTED), 1.5)
        self.assertEqual(book.price(EVENT_CHECK_EXECUTED), 0.25)
        self.assertEqual(book.price(EVENT_EXPORT_GENERATED), 0.0)

        flat = PriceBook.from_dict({EVENT_RUN_STARTED: 2.0})
        self.assertEqual(flat.price(EVENT_RUN_STARTED), 2.0)
        with self.assertRaises(LedgerError):
            PriceBook.from_dict({"prices": {"not_an_event": 1}})
        with self.assertRaises(LedgerError):
            PriceBook.from_dict({"prices": {EVENT_RUN_STARTED: -1}})

    def test_usage_entry_is_appended_immutably_and_duplicate_is_ignored(self):
        event = make_event(EVENT_RUN_STARTED, "run-1:start")
        first = self.ledger.ingest_event(event, price_book())
        self.assertTrue(first["created"])
        self.assertEqual(first["entry"]["workspace"], WORKSPACE)
        self.assertEqual(first["entry"]["period"], PERIOD)
        self.assertEqual(first["entry"]["quantity"], 1)
        self.assertEqual(first["entry"]["unit_price"], 1.5)
        self.assertEqual(first["entry"]["amount"], 1.5)
        self.assertEqual(first["entry"]["sequence"], 1)
        self.assertEqual(first["entry"]["previous_hash"], "")

        duplicate = self.ledger.ingest_event(event, price_book())
        self.assertFalse(duplicate["created"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["entry"]["entry_id"], first["entry"]["entry_id"])
        self.assertEqual(len(self.ledger.entries()), 1)

        conflict = dict(event)
        conflict["quantity"] = 2
        conflict["event_id"] = "usage-conflict"
        with self.assertRaises(LedgerError):
            self.ledger.ingest_event(conflict, price_book())

        entries = self.ledger.entries()
        entries[0]["quantity"] = 999
        self.assertEqual(self.ledger.entries()[0]["quantity"], 1)
        self.assertTrue(self.ledger.verify()["ok"])

    def test_hash_chain_detects_tampering(self):
        self.ledger.ingest_event(make_event(EVENT_RUN_STARTED, "run-1:one"), price_book())
        self.ledger.ingest_event(
            make_event(EVENT_CHECK_EXECUTED, "run-1:two"), price_book()
        )
        verification = self.ledger.verify()
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["entry_count"], 2)

        lines = self.ledger.entries_path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[0])
        tampered["quantity"] = 99
        lines[0] = json.dumps(tampered, sort_keys=True)
        self.ledger.entries_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        verification = self.ledger.verify()
        self.assertFalse(verification["ok"])
        self.assertEqual(verification["status"], "broken")
        with self.assertRaises(LedgerError):
            self.ledger.ingest_event(
                make_event(EVENT_EXPORT_GENERATED, "run-1:three"), price_book()
            )

    def test_closed_period_rejects_late_usage_but_allows_explicit_adjustment(self):
        self.ledger.ingest_event(make_event(EVENT_RUN_STARTED, "run-1:start"), price_book())
        close = self.ledger.close_period(
            WORKSPACE, PERIOD, closed_at="2026-12-01T00:00:00Z", closed_by="finance"
        )
        self.assertTrue(close["created"])
        self.assertEqual(close["entry_count"], 1)
        self.assertEqual(close["subtotal"], 1.5)
        self.assertIn("close_id", close)

        late = make_event(
            EVENT_CHECK_EXECUTED,
            "run-1:late",
            timestamp="2026-11-30T23:59:59Z",
        )
        with self.assertRaises(LedgerPeriodClosed):
            self.ledger.ingest_event(late, price_book())

        batch = self.ledger.ingest_events([late], price_book=price_book())
        self.assertEqual(batch["status"], "partial")
        self.assertEqual(batch["rejected"][0]["code"], "period_closed")
        self.assertEqual(len(self.ledger.entries()), 1)

        adjustment = self.ledger.add_late_adjustment(
            WORKSPACE,
            PERIOD,
            close["close_id"],
            EVENT_CHECK_EXECUTED,
            1,
            reason="late metering event accepted after close",
            unit_price=0.25,
        )
        self.assertEqual(adjustment["entry"]["entry_type"], ENTRY_ADJUSTMENT)
        self.assertEqual(
            adjustment["entry"]["references"], [close["close_id"]]
        )
        invoice = self.ledger.invoice(WORKSPACE, PERIOD)
        self.assertTrue(invoice["closed"])
        self.assertEqual(invoice["close_id"], close["close_id"])
        self.assertEqual(invoice["subtotal"], 1.5)
        self.assertEqual(invoice["credits"], 0.0)
        self.assertEqual(invoice["total"], 1.75)

        # Repeating the close is idempotent and never moves the close record.
        repeated = self.ledger.close_period(WORKSPACE, PERIOD)
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["close_id"], close["close_id"])

    def test_correction_references_original_entry_and_reduces_the_invoice(self):
        for index in range(3):
            result = self.ledger.ingest_event(
                make_event(EVENT_CHECK_EXECUTED, "run-1:check:{}".format(index)),
                price_book(),
            )
            if index == 0:
                original = result["entry"]
        self.ledger.close_period(
            WORKSPACE, PERIOD, closed_at="2026-12-01T00:00:00Z"
        )
        correction = self.ledger.add_correction(
            WORKSPACE,
            PERIOD,
            original["entry_id"],
            -1,
            reason="one check was a duplicate dispatch",
        )
        self.assertEqual(correction["entry"]["entry_type"], ENTRY_CORRECTION)
        self.assertEqual(
            correction["entry"]["references"], [original["entry_id"]]
        )
        self.assertEqual(correction["entry"]["quantity"], -1)
        self.assertEqual(correction["entry"]["unit_price"], 0.25)
        self.assertEqual(correction["entry"]["amount"], -0.25)

        invoice = self.ledger.invoice(WORKSPACE, PERIOD)
        self.assertEqual(invoice["subtotal"], 0.75)
        self.assertEqual(invoice["credits"], 0.25)
        self.assertEqual(invoice["adjustments_total"], -0.25)
        self.assertEqual(invoice["total"], 0.5)
        self.assertEqual(invoice["line_items"][0]["quantity"], 3.0)
        self.assertEqual(
            self.ledger.get(original["entry_id"])["quantity"], 1
        )

    def test_invoice_totals_reconcile_quantities_prices_and_credits(self):
        events = [
            make_event(EVENT_RUN_STARTED, "run-1:run"),
            make_event(EVENT_CHECK_EXECUTED, "run-1:check:1"),
            make_event(EVENT_CHECK_EXECUTED, "run-1:check:2"),
            make_event(EVENT_CHECK_EXECUTED, "run-1:check:3"),
            make_event(
                EVENT_BROWSER_MINUTE_CONSUMED,
                "run-1:browser",
                quantity=1.5,
            ),
            make_event(
                EVENT_EVIDENCE_BYTE_STORED,
                "run-1:bytes",
                quantity=100,
            ),
            make_event(EVENT_EXPORT_GENERATED, "run-1:export"),
        ]
        result = self.ledger.ingest_events(events, price_book=price_book())
        self.assertEqual(result["ingested"], 7)
        self.assertEqual(result["duplicates"], 0)

        original = self.ledger.entries(WORKSPACE, PERIOD)[1]
        self.ledger.add_correction(
            WORKSPACE,
            PERIOD,
            original["entry_id"],
            -1,
            reason="duplicate check",
        )
        invoice = self.ledger.invoice(
            WORKSPACE,
            PERIOD,
            invoice_id="inv-test",
            generated_at="2026-12-01T00:00:00Z",
        )
        self.assertEqual(invoice["schema_version"], INVOICE_SCHEMA_VERSION)
        self.assertEqual(invoice["workspace"], WORKSPACE)
        self.assertEqual(invoice["period"], PERIOD)
        self.assertEqual(invoice["subtotal"], 2.55)
        self.assertEqual(invoice["credits"], 0.25)
        self.assertEqual(invoice["total"], 2.3)
        self.assertEqual(
            {line["event_type"] for line in invoice["line_items"]},
            {
                EVENT_RUN_STARTED,
                EVENT_CHECK_EXECUTED,
                EVENT_BROWSER_MINUTE_CONSUMED,
                EVENT_EVIDENCE_BYTE_STORED,
                EVENT_EXPORT_GENERATED,
            },
        )

        reconciliation = self.ledger.reconcile(
            WORKSPACE, PERIOD, invoice=invoice
        )
        self.assertTrue(reconciliation["ok"])
        self.assertEqual(reconciliation["difference"], 0.0)
        self.assertEqual(reconciliation["invoice_total"], 2.3)

        csv_text = invoice_to_csv(invoice)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertTrue(rows)
        self.assertEqual(rows[0]["workspace"], WORKSPACE)
        self.assertEqual(rows[0]["period"], PERIOD)
        row_types = {row["row_type"] for row in rows}
        self.assertTrue({"line_item", "correction", "subtotal", "credits", "total"} <= row_types)
        self.assertEqual(
            [row["amount"] for row in rows if row["row_type"] == "total"], ["2.300000"]
        )

    def test_reconciliation_covers_duplicate_late_and_corrected_events(self):
        first = make_event(EVENT_CHECK_EXECUTED, "run-1:check:1")
        second = make_event(EVENT_CHECK_EXECUTED, "run-1:check:2")
        self.ledger.ingest_event(first, price_book())
        self.ledger.ingest_event(second, price_book())
        duplicate = self.ledger.ingest_event(first, price_book())
        self.assertTrue(duplicate["duplicate"])

        close = self.ledger.close_period(
            WORKSPACE, PERIOD, closed_at="2026-12-01T00:00:00Z"
        )
        late = make_event(
            EVENT_CHECK_EXECUTED,
            "run-1:late",
            timestamp="2026-11-20T00:00:00Z",
        )
        with self.assertRaises(LedgerPeriodClosed):
            self.ledger.ingest_event(late, price_book())
        self.ledger.add_late_adjustment(
            WORKSPACE,
            PERIOD,
            close["close_id"],
            EVENT_CHECK_EXECUTED,
            1,
            reason="late event",
            unit_price=0.25,
        )
        original = self.ledger.entries(WORKSPACE, PERIOD)[0]
        self.ledger.add_correction(
            WORKSPACE,
            PERIOD,
            original["entry_id"],
            -1,
            reason="duplicate event",
        )

        invoice = self.ledger.invoice(WORKSPACE, PERIOD)
        usage = 2 * 0.25 + 1 * 0.25
        credits = 1 * 0.25
        self.assertEqual(invoice["subtotal"], 0.5)
        self.assertEqual(invoice["credits"], credits)
        self.assertEqual(invoice["total"], usage - credits)
        self.assertEqual(self.ledger.reconcile(WORKSPACE, PERIOD, invoice=invoice)["ok"], True)

    def test_period_helpers_and_invalid_inputs(self):
        self.assertEqual(normalize_period(PERIOD), PERIOD)
        self.assertEqual(normalize_period("2026-11-12T00:00:00Z"), PERIOD)
        bounds = period_bounds(PERIOD)
        self.assertEqual(bounds["start"], "2026-11-01T00:00:00Z")
        self.assertEqual(bounds["end"], "2026-12-01T00:00:00Z")
        with self.assertRaises(LedgerError):
            normalize_period("2026-13")
        with self.assertRaises(LedgerError):
            self.ledger.invoice(WORKSPACE, "not-a-period")

    def test_auth_context_binds_the_ledger_to_one_workspace(self):
        auth = AuthStore(self.root)
        workspace = auth.create_workspace("Acme", workspace_id=WORKSPACE)
        other = auth.create_workspace("Other", workspace_id="ws_other")
        issued = auth.create_key(workspace["id"], scopes=(SCOPE_RUN, SCOPE_READ))
        context = auth.authenticate(issued["secret"], required_scope=SCOPE_RUN)

        bound = LedgerStore.from_auth_context(self.root, context)
        self.assertEqual(bound.workspace, WORKSPACE)
        event = make_event(EVENT_RUN_STARTED, "run-1:bound")
        self.assertTrue(
            bound.ingest_for_context(context, event, price_book=price_book())["created"]
        )
        self.assertEqual(len(bound.entries()), 1)
        self.assertEqual(bound.invoice_for(context, PERIOD)["subtotal"], 1.5)

        other_event = make_event(
            EVENT_RUN_STARTED, "run-2:other", workspace=other["id"], run="run-2"
        )
        with self.assertRaises(LedgerError):
            bound.ingest_for_context(context, other_event, price_book=price_book())
        with self.assertRaises(LedgerError):
            bound.add_adjustment_for(
                context,
                PERIOD,
                EVENT_RUN_STARTED,
                -1,
                reason="cross workspace",
                references=["missing"],
            )
        self.assertEqual(bound.entries_for(context), bound.entries())


class LedgerCliTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.ledger = LedgerStore(self.root)
        self.emitter = MeteringEmitter(
            self.root, "run-1", workspace=WORKSPACE, sink=".harness/metering/events.jsonl"
        )

    def tearDown(self):
        self._temporary.cleanup()

    def _seed_events(self):
        self.emitter.emit(
            EVENT_RUN_STARTED,
            1,
            "run",
            idempotency_key="run-1:run",
            metadata=METADATA[EVENT_RUN_STARTED],
            timestamp=TIMESTAMP,
        )
        self.emitter.emit(
            EVENT_CHECK_EXECUTED,
            1,
            "check",
            idempotency_key="run-1:check",
            metadata=METADATA[EVENT_CHECK_EXECUTED],
            timestamp=TIMESTAMP,
        )
        return self.emitter.events()

    def test_cli_ingest_close_adjust_invoice_entries_and_verify(self):
        self._seed_events()
        prices_path = self.root / "prices.json"
        prices_path.write_text(
            json.dumps(price_book().to_dict()), encoding="utf-8"
        )

        code, stdout, _ = run_cli(
            [
                "ledger",
                "ingest",
                "--root",
                str(self.root),
                "--workspace",
                WORKSPACE,
                "--prices",
                str(prices_path),
            ]
        )
        self.assertEqual(code, 0)
        ingested = json.loads(stdout)
        self.assertEqual(ingested["schema_version"], SCHEMA_VERSION)
        self.assertEqual(ingested["ingested"], 2)
        reference = ingested["entries"][1]["entry_id"]

        code, stdout, _ = run_cli(
            [
                "ledger",
                "close",
                "--root",
                str(self.root),
                "--workspace",
                WORKSPACE,
                "--period",
                PERIOD,
                "--at",
                "2026-12-01T00:00:00Z",
            ]
        )
        self.assertEqual(code, 0)
        closed = json.loads(stdout)
        self.assertEqual(closed["status"], "closed")
        self.assertTrue(closed["period"]["created"])

        code, stdout, _ = run_cli(
            [
                "ledger",
                "adjust",
                "--root",
                str(self.root),
                "--workspace",
                WORKSPACE,
                "--period",
                PERIOD,
                "--quantity",
                "-1",
                "--reason",
                "duplicate check",
                "--reference",
                reference,
            ]
        )
        self.assertEqual(code, 0)
        adjusted = json.loads(stdout)
        self.assertEqual(adjusted["entry"]["entry_type"], ENTRY_CORRECTION)

        code, stdout, _ = run_cli(
            [
                "ledger",
                "invoice",
                "--root",
                str(self.root),
                "--workspace",
                WORKSPACE,
                "--period",
                PERIOD,
                "--format",
                "json",
            ]
        )
        self.assertEqual(code, 0)
        invoice = json.loads(stdout)
        self.assertEqual(invoice["total"], 1.5)
        self.assertEqual(invoice["credits"], 0.25)

        code, stdout, _ = run_cli(
            [
                "ledger",
                "invoice",
                "--root",
                str(self.root),
                "--workspace",
                WORKSPACE,
                "--period",
                PERIOD,
                "--format",
                "csv",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("workspace,period,currency,row_type", stdout)
        self.assertIn(WORKSPACE, stdout)

        output = self.root / "invoice.json"
        code, stdout, _ = run_cli(
            [
                "ledger",
                "invoice",
                "--root",
                str(self.root),
                "--workspace",
                WORKSPACE,
                "--period",
                PERIOD,
                "--output",
                str(output),
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue(output.is_file())
        self.assertEqual(json.loads(stdout)["status"], "written")

        code, stdout, _ = run_cli(
            ["ledger", "entries", "--root", str(self.root), "--workspace", WORKSPACE]
        )
        self.assertEqual(code, 0)
        entries_payload = json.loads(stdout)
        self.assertEqual(entries_payload["count"], 3)

        code, stdout, _ = run_cli(
            ["ledger", "periods", "--root", str(self.root), "--workspace", WORKSPACE]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["count"], 1)

        code, stdout, _ = run_cli(["ledger", "reconcile", "--root", str(self.root), "--workspace", WORKSPACE, "--period", PERIOD])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["ok"])

        code, stdout, _ = run_cli(["ledger", "verify", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["ok"])

    def test_cli_ingest_reports_partial_after_period_close(self):
        self._seed_events()
        self.ledger.ingest_events(
            self.emitter.events(), workspace=WORKSPACE, price_book=price_book()
        )
        self.ledger.close_period(
            WORKSPACE, PERIOD, closed_at="2026-12-01T00:00:00Z"
        )
        self.emitter.emit(
            EVENT_EXPORT_GENERATED,
            1,
            "export",
            idempotency_key="run-1:late-export",
            metadata=METADATA[EVENT_EXPORT_GENERATED],
            timestamp="2026-11-30T00:00:00Z",
        )
        code, stdout, _ = run_cli(
            [
                "ledger",
                "ingest",
                "--root",
                str(self.root),
                "--workspace",
                WORKSPACE,
                "--prices",
                str(self._write_prices()),
            ]
        )
        self.assertEqual(code, 1)
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["duplicates"], 2)
        self.assertEqual(payload["rejected"][0]["code"], "period_closed")

    def _write_prices(self):
        path = self.root / "prices.json"
        path.write_text(json.dumps(price_book().to_dict()), encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
