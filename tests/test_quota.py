"""Monthly quota enforcement, response, and concurrency contract tests."""

import contextlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from harness import cli
from harness.auth import AuthStore
from harness.quota import (
    EVENT_HARD_LIMIT,
    EVENT_SOFT_LIMIT,
    RESOURCES,
    SCHEMA_VERSION,
    QuotaError,
    QuotaExceeded,
    QuotaLimit,
    QuotaPlan,
    QuotaStore,
    workspace_from_context,
)


NOVEMBER = "2026-11-12T00:00:00Z"
DECEMBER = "2026-12-01T00:00:00Z"


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def free_plan():
    return QuotaPlan.from_dict(
        {
            "id": "free",
            "version": "2026-11",
            "limits": {
                "runs": 1,
                "checks": {"soft": 2, "hard": 4},
                "browser_minutes": 0.5,
                "evidence_bytes": 100,
            },
        }
    )


class QuotaPlanTests(unittest.TestCase):
    def test_plan_accepts_flat_and_nested_resource_shapes(self):
        plan = QuotaPlan.from_dict(
            {"id": "flat", "checks": 10, "runs": {"soft": 1, "hard": 2}}
        )

        self.assertEqual(plan.limit("checks"), QuotaLimit(hard=10))
        self.assertEqual(plan.limit("run"), QuotaLimit(soft=1, hard=2))

    def test_plan_rejects_unknown_resources_and_inverted_limits(self):
        with self.assertRaises(ValueError):
            QuotaPlan.from_dict({"limits": {"widgets": 2}})
        with self.assertRaises(ValueError):
            QuotaPlan.from_dict({"limits": {"checks": {"soft": 5, "hard": 2}}})

    def test_plan_round_trips_through_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "plan.json"
            plan = free_plan()
            path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")

            loaded = QuotaPlan.load(path)

            self.assertEqual(loaded.id, "free")
            self.assertEqual(loaded.limit("checks"), QuotaLimit(soft=2, hard=4))


class QuotaStoreTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.plan = free_plan()

    def tearDown(self):
        self._temporary.cleanup()

    def test_soft_limit_warns_once_per_resource_per_billing_period(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")

        first = store.consume("checks", 1, now=NOVEMBER)
        second = store.consume("checks", 1, now=NOVEMBER)
        third = store.consume("checks", 1, now=NOVEMBER)

        self.assertIsNone(first.warning)
        self.assertIsNotNone(second.warning)
        self.assertEqual(second.warning["soft_limit"], 2)
        self.assertIsNone(third.warning)
        warnings = store.warnings(now=NOVEMBER)
        self.assertEqual(warnings, ["checks"])
        soft_events = [
            event for event in store.events() if event["event_type"] == EVENT_SOFT_LIMIT
        ]
        self.assertEqual(len(soft_events), 1)
        self.assertEqual(soft_events[0]["workspace"], "ws_acme")
        self.assertEqual(soft_events[0]["resource"], "checks")

    def test_soft_warning_is_emitted_again_after_period_rollover(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")
        store.consume("checks", 2, now=NOVEMBER)
        self.assertEqual(store.warnings(now=NOVEMBER), ["checks"])

        rolled = store.consume("checks", 2, now=DECEMBER)

        self.assertIsNotNone(rolled.warning)
        self.assertEqual(rolled.used, 2)
        self.assertEqual(store.usage(now=DECEMBER)["checks"], 2)

    def test_workspace_usage_and_warnings_are_isolated(self):
        acme = QuotaStore(self.root, self.plan, workspace="ws_acme")
        globex = QuotaStore(self.root, self.plan, workspace="ws_globex")

        acme.consume("checks", 2, now=NOVEMBER)
        globex.consume("checks", 1, now=NOVEMBER)

        self.assertEqual(acme.usage(now=NOVEMBER)["checks"], 2)
        self.assertEqual(globex.usage(now=NOVEMBER)["checks"], 1)
        self.assertEqual(acme.warnings(now=NOVEMBER), ["checks"])
        self.assertEqual(globex.warnings(now=NOVEMBER), [])

    def test_auth_context_resolves_workspace_and_rejects_mismatch(self):
        auth = AuthStore(self.root)
        workspace = auth.create_workspace("Acme", workspace_id="ws_acme")
        issued = auth.create_key(workspace["id"], ["run"])
        context = auth.authenticate(issued["secret"], required_scope="run")

        store = QuotaStore.from_auth_context(self.root, context, self.plan)
        decision = store.consume_for(context, "runs", 1, now=NOVEMBER)

        self.assertEqual(store.workspace, "ws_acme")
        self.assertEqual(workspace_from_context(context), "ws_acme")
        self.assertTrue(decision.allowed)
        self.assertEqual(store.usage(now=NOVEMBER)["runs"], 1)
        bound = QuotaStore.from_auth_context(
            self.root, context, self.plan, workspace="ws_attacker"
        )
        self.assertEqual(bound.workspace, "ws_acme")
        other = QuotaStore(self.root, self.plan, workspace="ws_other")
        with self.assertRaises(QuotaError):
            other.consume_for(context, "runs", 1, now=NOVEMBER)
        with self.assertRaises(QuotaError):
            workspace_from_context(None)

    def test_hard_limit_returns_429_with_exact_limit_and_reset(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")
        store.consume("checks", 4, now=NOVEMBER)

        with self.assertRaises(QuotaExceeded) as caught:
            store.consume("checks", 1, now=NOVEMBER)

        error = caught.exception
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.limit, 4)
        self.assertEqual(error.used, 4)
        self.assertEqual(error.requested, 1)
        self.assertEqual(error.reset_at, "2026-12-01T00:00:00Z")
        payload = error.to_dict()
        self.assertEqual(payload["http_status"], 429)
        self.assertEqual(payload["error"]["code"], "quota_exceeded")
        self.assertEqual(payload["error"]["limit"], 4)
        self.assertEqual(payload["error"]["reset_at"], "2026-12-01T00:00:00Z")
        self.assertEqual(payload["headers"]["X-QH-Quota-Remaining-Checks"], "0")
        self.assertIn("Retry-After", payload["headers"])

    def test_denied_action_does_not_discard_completed_usage(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")

        for _ in range(4):
            store.consume("checks", 1, now=NOVEMBER)
        with self.assertRaises(QuotaExceeded):
            store.consume("checks", 1, now=NOVEMBER)

        snapshot = store.snapshot(now=NOVEMBER)
        self.assertEqual(snapshot["usage"]["checks"], 4)
        self.assertEqual(snapshot["remaining"]["checks"], 0)
        hard_events = [
            event for event in store.events() if event["event_type"] == EVENT_HARD_LIMIT
        ]
        self.assertEqual(len(hard_events), 1)
        self.assertEqual(hard_events[0]["used"], 4)

    def test_try_consume_returns_a_blocked_decision_instead_of_raising(self):
        store = QuotaStore(
            self.root,
            {"id": "tiny", "limits": {"runs": 1}},
            workspace="ws_acme",
        )
        store.consume("runs", 1, now=NOVEMBER)

        decision = store.try_consume("runs", 1, now=NOVEMBER)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status_code, 429)
        self.assertEqual(store.usage(now=NOVEMBER)["runs"], 1)

    def test_allowed_decision_carries_remaining_quota_headers(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")

        decision = store.consume("checks", 1, now=NOVEMBER)

        headers = decision.headers
        self.assertEqual(headers["X-QH-Quota-Limit-Checks"], "4")
        self.assertEqual(headers["X-QH-Quota-Remaining-Checks"], "3")
        self.assertEqual(headers["X-QH-Quota-Used-Checks"], "1")
        self.assertEqual(headers["X-QH-Quota-Soft-Remaining"], "1")
        self.assertEqual(headers["X-QH-Quota-Reset"], "2026-12-01T00:00:00Z")
        self.assertEqual(headers["X-Quota-Remaining-Checks"], "3")

    def test_browser_minutes_and_evidence_bytes_accept_fractional_and_large_units(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")

        browser = store.consume("browser_minute", 0.25, now=NOVEMBER)
        evidence = store.consume("evidence_byte", 60, now=NOVEMBER)

        self.assertTrue(browser.allowed)
        self.assertEqual(browser.remaining, 0.25)
        self.assertTrue(evidence.allowed)
        self.assertEqual(evidence.remaining, 40)

        decimal_store = QuotaStore(
            self.root,
            {"id": "decimal", "limits": {"browser_minutes": 0.3}},
            workspace="ws_decimal",
        )
        for _ in range(3):
            self.assertTrue(
                decimal_store.consume("browser_minutes", 0.1, now=NOVEMBER).allowed
            )
        with self.assertRaises(QuotaExceeded):
            decimal_store.consume("browser_minutes", 0.1, now=NOVEMBER)
        self.assertEqual(
            decimal_store.usage(now=NOVEMBER)["browser_minutes"], 0.3
        )

    def test_metering_event_names_map_to_quota_resources(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")

        decision = store.consume_metering_event(
            {
                "event_type": "check_executed",
                "quantity": 1,
                "run": "run-1",
                "timestamp": NOVEMBER,
            }
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.resource, "checks")
        self.assertEqual(store.usage(now=NOVEMBER)["checks"], 1)
        export = store.consume_event("export_generated", 1, now=NOVEMBER)
        self.assertFalse(export.enforced)
        self.assertEqual(store.usage(now=NOVEMBER)["checks"], 1)

    def test_local_mode_is_unlimited_and_does_not_create_state(self):
        store = QuotaStore(self.root, self.plan, workspace="local")

        for _ in range(10):
            decision = store.consume("checks", 100, now=NOVEMBER)

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.enforced)
        self.assertIsNone(decision.limit)
        self.assertIsNone(decision.remaining)
        self.assertFalse((self.root / ".harness" / "quota" / "usage.json").exists())

    def test_local_mode_can_be_explicitly_enforced_for_tests(self):
        store = QuotaStore(
            self.root, self.plan, workspace="local", enforce=True
        )

        store.consume("checks", 4, now=NOVEMBER)
        with self.assertRaises(QuotaExceeded):
            store.consume("checks", 1, now=NOVEMBER)

        self.assertTrue((self.root / ".harness" / "quota" / "usage.json").exists())

    def test_supplying_a_plan_without_explicit_workspace_opts_in(self):
        store = QuotaStore(self.root, self.plan)

        store.consume("checks", 4, now=NOVEMBER)
        with self.assertRaises(QuotaExceeded):
            store.consume("checks", 1, now=NOVEMBER)

        self.assertTrue(store.enforced)
        self.assertEqual(store.workspace, "local")

    def test_snapshot_exposes_all_resources_and_reset_time(self):
        store = QuotaStore(self.root, self.plan, workspace="ws_acme")
        store.consume("runs", 1, now=NOVEMBER)

        snapshot = store.snapshot(now=NOVEMBER)

        self.assertEqual(snapshot["schema_version"], SCHEMA_VERSION)
        self.assertTrue(snapshot["enforced"])
        self.assertEqual(set(snapshot["usage"]), set(RESOURCES))
        self.assertEqual(snapshot["usage"]["runs"], 1)
        self.assertEqual(snapshot["remaining"]["runs"], 0)
        self.assertEqual(snapshot["period"]["reset_at"], "2026-12-01T00:00:00Z")
        self.assertEqual(snapshot["plan"]["id"], "free")

    def test_parallel_requests_cannot_overspend_a_hard_limit(self):
        attempts = 20
        barrier = threading.Barrier(attempts)

        def attempt():
            store = QuotaStore(self.root, self.plan, workspace="ws_parallel")
            barrier.wait()
            try:
                store.consume("checks", 1, now=NOVEMBER)
                return True
            except QuotaExceeded:
                return False

        with ThreadPoolExecutor(max_workers=attempts) as pool:
            results = list(pool.map(lambda _: attempt(), range(attempts)))

        self.assertEqual(sum(1 for result in results if result), 4)
        self.assertEqual(
            QuotaStore(self.root, self.plan, workspace="ws_parallel").usage(
                now=NOVEMBER
            )["checks"],
            4,
        )


class QuotaCliTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(
            json.dumps(
                {
                    "id": "free",
                    "limits": {"runs": 1, "checks": {"soft": 1, "hard": 2}},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self._temporary.cleanup()

    def test_quota_consume_reports_429_and_keeps_partial_usage(self):
        base = [
            "quota",
            "consume",
            "--root",
            str(self.root),
            "--workspace",
            "ws_acme",
            "--plan",
            str(self.plan_path),
            "--resource",
            "checks",
            "--now",
            NOVEMBER,
        ]
        first_code, first_out, _ = run_cli(base)
        second_code, second_out, _ = run_cli(base)
        blocked_code, blocked_out, _ = run_cli(base)

        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(blocked_code, 1)
        first = json.loads(first_out)
        second = json.loads(second_out)
        blocked = json.loads(blocked_out)
        self.assertTrue(first["allowed"])
        self.assertIsNotNone(first.get("warning"))
        self.assertIsNone(second.get("warning"))
        self.assertEqual(blocked["http_status"], 429)
        self.assertEqual(blocked["error"]["limit"], 2)
        self.assertEqual(blocked["error"]["reset_at"], "2026-12-01T00:00:00Z")

        show_code, show_out, _ = run_cli(
            [
                "quota",
                "show",
                "--root",
                str(self.root),
                "--workspace",
                "ws_acme",
                "--plan",
                str(self.plan_path),
                "--now",
                NOVEMBER,
            ]
        )
        self.assertEqual(show_code, 0)
        shown = json.loads(show_out)
        self.assertEqual(shown["usage"]["checks"], 2)
        self.assertEqual(shown["remaining"]["checks"], 0)

    def test_quota_consume_in_local_mode_is_unlimited(self):
        code, output, _ = run_cli(
            [
                "quota",
                "consume",
                "--root",
                str(self.root),
                "--workspace",
                "local",
                "--plan",
                str(self.plan_path),
                "--resource",
                "runs",
                "--quantity",
                "1000",
                "--now",
                NOVEMBER,
            ]
        )

        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertTrue(payload["allowed"])
        self.assertFalse(payload["enforced"])
        self.assertFalse((self.root / ".harness" / "quota" / "usage.json").exists())

    def test_invalid_plan_exits_two_with_a_machine_readable_error(self):
        bad = self.root / "bad.json"
        bad.write_text('{"limits": {"checks": {"soft": 5, "hard": 1}}}', encoding="utf-8")

        code, output, _ = run_cli(
            [
                "quota",
                "show",
                "--root",
                str(self.root),
                "--workspace",
                "ws_acme",
                "--plan",
                str(bad),
            ]
        )

        self.assertEqual(code, 2)
        payload = json.loads(output)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["command"], "quota")


if __name__ == "__main__":
    unittest.main()
