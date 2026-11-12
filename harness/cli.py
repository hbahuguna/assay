"""Command-line interface.

The interface is intentionally machine-first: run, status, report, abort, and
personas all emit JSON to stdout.  Human progress goes to stderr so an agent or
CI job can pipe stdout directly into a JSON parser.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .calibration import run_calibration, write_calibration_report
from .errors import HarnessError, PersonaError
from .exports import build_junit, build_sarif, sign_report, verify_report_signature
from .exports import build_junit, build_sarif, sign_report, verify_report_signature
from .metering import EVENT_EXPORT_GENERATED, MeteringEmitter
from .models import CheckStatus, Severity, severity_rank, utc_now
from .orchestrator import Orchestrator, RunOptions
from .personas import lint_personas, load_personas, scaffold_persona
from .plugins import build_registry
from .report import REPORT_SCHEMA
from .state import StateStore, SuppressionStore


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _error_payload(exc: Exception, command: str) -> Dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "status": "error",
        "command": command,
        "error": {"type": type(exc).__name__, "message": str(exc)},
        "generated_at": utc_now(),
    }


def _csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quality-harness",
        description=(
            "Deterministic risk-based AI testing harness: Inspect source, Operate a live "
            "target, collect evidence, and score release confidence."
        ),
    )
    parser.add_argument("--version", action="version", version="%(prog)s {}".format(__version__))
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="Run the five-phase release-confidence workflow.")
    run.add_argument("--target", "-t", required=True, help="Target URL, directory, or file.")
    run.add_argument("--source", help="Optional source directory to combine with a URL target.")
    run.add_argument(
        "--intent",
        "-i",
        default="Assess release readiness of the target.",
        help="The customer outcome this release must still deliver.",
    )
    run.add_argument("--personas", help="Comma-separated persona ids to include.")
    run.add_argument("--personas-dir", default="personas", help="Directory containing persona Markdown files.")
    run.add_argument(
        "--workflow",
        default="release-confidence",
        help="Workflow id to run; .harness/workflows can override the built-in definition.",
    )
    run.add_argument("--max-checks", type=int, default=20, help="Maximum check budget (default: 20).")
    run.add_argument("--no-browser", action="store_true", help="Skip Playwright Operate checks.")
    run.add_argument("--headed", action="store_true", help="Show the browser while running.")
    run.add_argument("--timeout", type=float, default=30.0, help="Navigation timeout in seconds (default: 30).")
    run.add_argument("--viewport", default="1440x900", help="Browser viewport, e.g. 1440x900.")
    run.add_argument(
        "--kill-on",
        choices=["critical", "high", "never"],
        default="critical",
        help="Automatic kill-switch severity threshold (default: critical).",
    )
    run.add_argument(
        "--fail-on",
        choices=["none", "critical", "high", "medium", "low"],
        default="none",
        help="Exit 1 when a finding at or above this severity exists.",
    )
    run.add_argument("--min-score", type=int, default=None, help="Exit 1 when confidence is below this score.")
    run.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for deterministic finding ids and ordering.",
    )
    run.add_argument(
        "--clock-mode",
        choices=["real", "frozen"],
        default="real",
        help="Clock provider for the run (default: real UTC).",
    )
    run.add_argument(
        "--frozen-clock",
        help="ISO-8601 timestamp used when --clock-mode=frozen.",
    )
    run.add_argument(
        "--generated-at",
        help="Override the report generated_at timestamp with an ISO-8601 value.",
    )
    run.add_argument(
        "--workspace",
        default="local",
        help="Workspace id recorded on usage events (default: local for OSS).",
    )
    run.add_argument(
        "--metering-sink",
        help="Path to the usage-event JSON Lines sink (default: .harness/metering/events.jsonl).",
    )
    run.add_argument("--resume", action="store_true", help="Resume the last incomplete run.")
    run.add_argument(
        "--keep-runs",
        type=int,
        default=None,
        help="Keep at most this many historical runs after the run completes.",
    )
    run.add_argument("--mobile", dest="mobile", action="store_true", help="Enable the responsive/mobile Operate check pack.")
    run.add_argument("--mobile-pack", dest="mobile", action="store_true", help="Alias for --mobile.")
    run.add_argument("--responsive", dest="mobile", action="store_true", help="Alias for --mobile.")
    run.add_argument("--mobile-viewport", default="390x844", help="Mobile viewport for the responsive pack, e.g. 390x844.")
    run.add_argument("--browser-har", action="store_true", help="Capture a redacted HTTP Archive for browser runs.")
    run.add_argument("--browser-trace", action="store_true", help="Capture a Playwright trace for browser runs.")
    run.add_argument("--browser-video", action="store_true", help="Capture browser video for browser runs.")
    run.add_argument("--storage-state", help="Path or env:VAR reference to a Playwright storage state file.")
    run.add_argument("--policy", help="Path to a versioned policy JSON file.")
    run.add_argument(
        "--fail-on-score-regression",
        dest="score_regression_threshold",
        type=float,
        default=None,
        help="Exit 1 when the score drops by at least this many points versus the previous run.",
    )
    run.add_argument(
        "--score-regression-threshold",
        dest="score_regression_threshold",
        type=float,
        help="Alias for --fail-on-score-regression.",
    )
    run.add_argument("--root", default=".", help="Project root for .harness state (default: current directory).")
    run.add_argument("--output", help="Also write the JSON report to this path.")
    run.add_argument("--quiet", action="store_true", help="Suppress progress lines on stderr.")

    status = subparsers.add_parser("status", help="Print the latest persisted run state as JSON.")
    status.add_argument("--root", default=".")
    status.add_argument(
        "--all",
        action="store_true",
        help="List every historical run newest-first instead of the latest run.",
    )

    prune = subparsers.add_parser(
        "prune", help="Prune historical runs and their evidence by count or age."
    )
    prune.add_argument("--root", default=".")
    prune.add_argument(
        "--keep-runs",
        type=int,
        default=None,
        help="Keep at most this many runs; protected runs are always retained.",
    )
    prune.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="Prune runs whose completion timestamp is older than this many days.",
    )
    prune.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be pruned without changing history or evidence.",
    )

    report = subparsers.add_parser("report", help="Print the latest saved report as JSON.")
    report.add_argument("--root", default=".")

    abort = subparsers.add_parser("abort", help="Request a manual kill of the current run.")
    abort.add_argument("--reason", default="Manual kill requested by the operator.")
    abort.add_argument("--root", default=".")

    personas = subparsers.add_parser(
        "personas",
        help="List, lint, or scaffold persona contracts as JSON.",
    )
    personas.add_argument(
        "personas_action",
        nargs="?",
        choices=("list", "lint", "scaffold"),
        default="list",
        help="Persona operation (default: list).",
    )
    personas.add_argument(
        "persona_id",
        nargs="?",
        help="Persona id for 'personas scaffold <id>'.",
    )
    personas.add_argument("--personas-dir", default="personas")
    personas.add_argument("--root", default=".")
    personas.add_argument("--select", help="Comma-separated persona ids to validate.")
    personas.add_argument("--name", help="Display name for a scaffolded persona.")
    personas.add_argument(
        "--domain",
        action="append",
        help="Domain owned by a scaffolded persona; repeat for multiple domains.",
    )
    personas.add_argument(
        "--check",
        action="append",
        help="Check id owned by a scaffolded persona; repeat for multiple checks.",
    )
    personas.add_argument(
        "--force",
        action="store_true",
        help="Allow 'personas scaffold' to overwrite an existing contract.",
    )

    sarif = subparsers.add_parser("sarif", help="Export the latest report as SARIF JSON.")
    sarif.add_argument("--root", default=".")
    sarif.add_argument("--output", help="Write SARIF to this path instead of stdout.")

    junit = subparsers.add_parser("junit", help="Export the latest report as JUnit XML.")
    junit.add_argument("--root", default=".")
    junit.add_argument("--output", help="Write JUnit XML to this path instead of stdout.")

    sign_report_parser = subparsers.add_parser("sign-report", help="Create a detached HMAC signature for the latest report.")
    sign_report_parser.add_argument("--root", default=".")
    sign_report_parser.add_argument("--key", required=True, help="Signing key (string or path).")
    sign_report_parser.add_argument("--output", help="Write the signature to this path.")

    verify_report = subparsers.add_parser("verify-report", help="Verify a detached report signature.")
    verify_report.add_argument("--root", default=".")
    verify_report.add_argument("--signature", required=True, help="Signature hex string or path to a signature file.")
    verify_report.add_argument("--key", required=True, help="Verification key (string or path).")

    for command_name in ("suppress", "suppressions"):
        suppress = subparsers.add_parser(command_name, help="Manage baseline finding suppressions.")
        suppress_subparsers = suppress.add_subparsers(dest="suppress_action", required=True)
        add = suppress_subparsers.add_parser("add", help="Add or replace a suppression.")
        add.add_argument("fingerprint")
        add.add_argument("--owner", required=True)
        add.add_argument("--reason", required=True)
        add.add_argument("--ticket", default="")
        add.add_argument("--expires", required=True)
        add.add_argument("--root", default=".")
        listed = suppress_subparsers.add_parser("list", help="List suppressions.")
        listed.add_argument("--root", default=".")
        removed = suppress_subparsers.add_parser("remove", help="Remove a suppression.")
        removed.add_argument("fingerprint")
        removed.add_argument("--root", default=".")

    auth = subparsers.add_parser(
        "auth",
        help="Manage cloud workspaces, scoped API keys, and run ownership as JSON.",
    )
    auth_subparsers = auth.add_subparsers(dest="auth_action", required=True)

    auth_workspace = auth_subparsers.add_parser(
        "create-workspace", help="Create a workspace."
    )
    auth_workspace.add_argument("--name", default="")
    auth_workspace.add_argument("--id", dest="workspace_id", default=None)
    auth_workspace.add_argument("--root", default=".")

    auth_workspaces = auth_subparsers.add_parser(
        "list-workspaces", help="List workspaces."
    )
    auth_workspaces.add_argument("--root", default=".")

    auth_key = auth_subparsers.add_parser("create-key", help="Issue a scoped API key.")
    auth_key.add_argument("--workspace", required=True)
    auth_key.add_argument(
        "--scope",
        action="append",
        help="Scope for the key (run, read, export, admin); repeat for multiple.",
    )
    auth_key.add_argument("--label", default="")
    auth_key.add_argument("--expires", default=None)
    auth_key.add_argument("--root", default=".")

    auth_keys = auth_subparsers.add_parser("list-keys", help="List public key metadata.")
    auth_keys.add_argument("--workspace", default=None)
    auth_keys.add_argument("--root", default=".")

    auth_revoke = auth_subparsers.add_parser("revoke-key", help="Revoke an API key.")
    auth_revoke.add_argument("--key", required=True)
    auth_revoke.add_argument("--reason", default="")
    auth_revoke.add_argument("--root", default=".")

    auth_rotate = auth_subparsers.add_parser(
        "rotate-key", help="Rotate an API key and return the new secret once."
    )
    auth_rotate.add_argument("--key", required=True)
    auth_rotate.add_argument(
        "--scope",
        action="append",
        help="Replacement scopes; omit to keep the current scopes.",
    )
    auth_rotate.add_argument("--label", default=None)
    auth_rotate.add_argument("--expires", default=None)
    auth_rotate.add_argument("--root", default=".")

    auth_authenticate = auth_subparsers.add_parser(
        "authenticate", help="Resolve a token to one workspace context."
    )
    auth_authenticate.add_argument("--token", required=True, help="Token or env:VAR.")
    auth_authenticate.add_argument("--scope", default=None)
    auth_authenticate.add_argument("--root", default=".")

    auth_claim = auth_subparsers.add_parser(
        "claim-run", help="Bind a run id to one workspace."
    )
    auth_claim.add_argument("--workspace", required=True)
    auth_claim.add_argument("--run", required=True)
    auth_claim.add_argument("--root", default=".")

    auth_authorize = auth_subparsers.add_parser(
        "authorize-run", help="Authorize a token for one workspace-owned run id."
    )
    auth_authorize.add_argument("--token", required=True, help="Token or env:VAR.")
    auth_authorize.add_argument("--run", required=True)
    auth_authorize.add_argument("--scope", default="read")
    auth_authorize.add_argument("--root", default=".")

    quota = subparsers.add_parser(
        "quota",
        help="Inspect or consume monthly workspace quota as JSON.",
    )
    quota_subparsers = quota.add_subparsers(dest="quota_action", required=True)

    quota_show = quota_subparsers.add_parser(
        "show", help="Show current-period usage, limits, and reset time."
    )
    quota_show.add_argument("--workspace", default="local")
    quota_show.add_argument("--plan", default=None, help="Path to a quota-plan JSON file.")
    quota_show.add_argument(
        "--enforce",
        action="store_true",
        help="Enforce the plan even for the synthetic local workspace.",
    )
    quota_show.add_argument("--now", default=None, help="Evaluate at an ISO-8601 time.")
    quota_show.add_argument("--root", default=".")

    quota_consume = quota_subparsers.add_parser(
        "consume", help="Consume one action against a workspace's quota."
    )
    quota_consume.add_argument("--workspace", default="local")
    quota_consume.add_argument("--resource", required=True, help="run, check, browser_minute, or evidence_byte.")
    quota_consume.add_argument("--quantity", type=float, default=1)
    quota_consume.add_argument("--plan", default=None, help="Path to a quota-plan JSON file.")
    quota_consume.add_argument(
        "--enforce",
        action="store_true",
        help="Enforce the plan even for the synthetic local workspace.",
    )
    quota_consume.add_argument("--now", default=None, help="Evaluate at an ISO-8601 time.")
    quota_consume.add_argument("--root", default=".")

    ledger = subparsers.add_parser(
        "ledger",
        help="Aggregate metering events into a ledger and export invoices as JSON or CSV.",
    )
    ledger_subparsers = ledger.add_subparsers(dest="ledger_action", required=True)

    ledger_ingest = ledger_subparsers.add_parser(
        "ingest", help="Append new metering events to the usage ledger."
    )
    ledger_ingest.add_argument(
        "--events",
        default=".harness/metering/events.jsonl",
        help="Metering JSON Lines file, relative to --root when not absolute.",
    )
    ledger_ingest.add_argument("--workspace", default=None, help="Only ingest this workspace.")
    ledger_ingest.add_argument("--prices", default=None, help="Path to a price-book JSON file.")
    ledger_ingest.add_argument(
        "--strict",
        action="store_true",
        help="Stop at the first rejected event instead of reporting a partial ingest.",
    )
    ledger_ingest.add_argument("--root", default=".")

    ledger_entries = ledger_subparsers.add_parser(
        "entries", help="List immutable ledger entries."
    )
    ledger_entries.add_argument("--workspace", default=None)
    ledger_entries.add_argument("--period", default=None)
    ledger_entries.add_argument("--root", default=".")

    ledger_periods = ledger_subparsers.add_parser(
        "periods", help="List closed billing periods."
    )
    ledger_periods.add_argument("--workspace", default=None)
    ledger_periods.add_argument("--root", default=".")

    ledger_close = ledger_subparsers.add_parser(
        "close", help="Close a billing period so late events require an adjustment."
    )
    ledger_close.add_argument("--workspace", required=True)
    ledger_close.add_argument("--period", required=True)
    ledger_close.add_argument("--by", default="")
    ledger_close.add_argument("--at", default=None, help="Close timestamp (ISO-8601).")
    ledger_close.add_argument("--root", default=".")

    ledger_adjust = ledger_subparsers.add_parser(
        "adjust", help="Append an explicit correction or late-period adjustment."
    )
    ledger_adjust.add_argument("--workspace", required=True)
    ledger_adjust.add_argument("--period", required=True)
    ledger_adjust.add_argument(
        "--event-type",
        default="",
        help="Metering event type; derived from the referenced entry when omitted.",
    )
    ledger_adjust.add_argument("--quantity", type=float, required=True)
    ledger_adjust.add_argument("--reason", required=True)
    ledger_adjust.add_argument(
        "--reference",
        dest="references",
        action="append",
        required=True,
        help="Original entry id or period close id; repeat for multiple references.",
    )
    ledger_adjust.add_argument("--unit", default=None)
    ledger_adjust.add_argument("--unit-price", type=float, default=None)
    ledger_adjust.add_argument("--prices", default=None, help="Path to a price-book JSON file.")
    ledger_adjust.add_argument("--idempotency-key", default="")
    ledger_adjust.add_argument("--at", default=None, help="Adjustment timestamp (ISO-8601).")
    ledger_adjust.add_argument("--root", default=".")

    ledger_invoice = ledger_subparsers.add_parser(
        "invoice", help="Render a workspace period as JSON or CSV."
    )
    ledger_invoice.add_argument("--workspace", required=True)
    ledger_invoice.add_argument("--period", required=True)
    ledger_invoice.add_argument("--format", choices=["json", "csv"], default="json")
    ledger_invoice.add_argument("--output", default=None, help="Write the invoice to this path.")
    ledger_invoice.add_argument("--invoice-id", default=None)
    ledger_invoice.add_argument("--generated-at", default=None)
    ledger_invoice.add_argument("--root", default=".")

    ledger_verify = ledger_subparsers.add_parser(
        "verify", help="Verify the append-only sequence and hash chain."
    )
    ledger_verify.add_argument("--root", default=".")

    ledger_reconcile = ledger_subparsers.add_parser(
        "reconcile",
        help="Compare an invoice total with the ledger's quantity-price total.",
    )
    ledger_reconcile.add_argument("--workspace", required=True)
    ledger_reconcile.add_argument("--period", required=True)
    ledger_reconcile.add_argument("--invoice-id", default=None)
    ledger_reconcile.add_argument("--root", default=".")

    signup = subparsers.add_parser(
        "signup", help="Self-serve workspace signup, plan selection, and entitlements."
    )
    signup_subparsers = signup.add_subparsers(dest="signup_action", required=True)

    signup_create = signup_subparsers.add_parser(
        "create", help="Create a workspace, owner key, and signed entitlement."
    )
    signup_create.add_argument("--request-id", required=True)
    signup_create.add_argument("--name", default="")
    signup_create.add_argument("--plan", default=None, help="Path to a quota plan JSON file.")
    signup_create.add_argument("--effective-at", default=None)
    signup_create.add_argument("--root", default=".")

    signup_select = signup_subparsers.add_parser(
        "select-plan", help="Select or change a workspace plan."
    )
    signup_select.add_argument("--workspace", required=True)
    signup_select.add_argument("--plan", required=True, help="Path to a quota plan JSON file.")
    signup_select.add_argument("--effective-at", default=None)
    signup_select.add_argument("--root", default=".")

    signup_usage = signup_subparsers.add_parser(
        "usage", help="Show signup, plan, and quota usage for a workspace."
    )
    signup_usage.add_argument("--workspace", required=True)
    signup_usage.add_argument("--plan", default=None, help="Optional plan JSON overriding the stored plan.")
    signup_usage.add_argument("--root", default=".")

    signup_verify = signup_subparsers.add_parser(
        "verify", help="Verify a signed entitlement."
    )
    signup_verify.add_argument("--entitlement", required=True, help="Path to an entitlement JSON file.")
    signup_verify.add_argument("--root", default=".")
    signup_verify.add_argument("--signing-key", default=None)
    signup_verify.add_argument("--workspace", default=None)
    signup_verify.add_argument("--period", default=None)

    billing = subparsers.add_parser(
        "billing", help="Feature-gated metered billing operations."
    )
    billing_subparsers = billing.add_subparsers(dest="billing_action", required=True)

    billing_status = billing_subparsers.add_parser(
        "status", help="Show billing status for a workspace."
    )
    billing_status.add_argument("--workspace", required=True)
    billing_status.add_argument("--root", default=".")

    billing_sync = billing_subparsers.add_parser(
        "sync", help="Reconcile a ledger invoice with the billing provider."
    )
    billing_sync.add_argument("--workspace", required=True)
    billing_sync.add_argument("--period", required=True)
    billing_sync.add_argument("--invoice-id", default=None)
    billing_sync.add_argument("--secret-ref", default="billing/api_key")
    billing_sync.add_argument("--secret-store", default=None)
    billing_sync.add_argument("--root", default=".")

    billing_disable = billing_subparsers.add_parser(
        "disable", help="Activate the billing kill switch."
    )
    billing_disable.add_argument("--reason", default="operator disabled billing writes")
    billing_disable.add_argument("--root", default=".")

    billing_enable = billing_subparsers.add_parser(
        "enable", help="Clear the billing kill switch."
    )
    billing_enable.add_argument("--root", default=".")

    calibrate = subparsers.add_parser(
        "calibrate",
        help="Run labeled confidence calibration fixtures and write a report.",
    )
    calibrate.add_argument("--fixtures-dir", default="calibration/fixtures")
    calibrate.add_argument("--output", default="calibration/report.json")
    calibrate.add_argument("--root", default=".")

    return parser


def _run(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    options = RunOptions(
        target=args.target,
        source=args.source,
        intent=args.intent,
        workflow_id=args.workflow,
        personas_dir=args.personas_dir,
        selected_personas=_csv(args.personas),
        max_checks=args.max_checks,
        no_browser=args.no_browser,
        headless=not args.headed,
        timeout_ms=int(args.timeout * 1000),
        viewport=args.viewport,
        kill_on=args.kill_on,
        mobile=args.mobile,
        mobile_viewport=args.mobile_viewport,
        browser_har=args.browser_har,
        browser_trace=args.browser_trace,
        browser_video=args.browser_video,
        storage_state=args.storage_state,
        policy=args.policy,
        require_personas=3,
        seed=args.seed,
        clock_mode=args.clock_mode,
        frozen_clock=args.frozen_clock,
        generated_at=args.generated_at,
        workspace=args.workspace,
        metering_sink=args.metering_sink,
    )

    def log(message: str) -> None:
        if not args.quiet:
            print("[quality-harness] {}".format(message), file=sys.stderr)

    orchestrator = Orchestrator(root, options, log=log)
    state = orchestrator.run(resume=args.resume)
    if args.keep_runs is not None:
        orchestrator.state_store.prune(keep_runs=args.keep_runs)
    report = orchestrator.state_store.load_report()
    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _print_json(report)

    policy_failed = False
    if args.fail_on and args.fail_on != "none":
        threshold = severity_rank(args.fail_on)
        if any(severity_rank(finding.get("severity", "")) >= threshold for finding in report.get("findings", [])):
            policy_failed = True
    if args.min_score is not None and report.get("confidence", {}).get("score", 0) < args.min_score:
        policy_failed = True
    trend = report.get("trend", {})
    if (
        args.score_regression_threshold is not None
        and not bool(trend.get("baseline", True))
        and float(trend.get("score_delta", 0.0)) < -args.score_regression_threshold
    ):
        policy_failed = True
    if policy_failed:
        return 1
    if state.status == "halted":
        return 3
    if state.status == "failed":
        return 2
    return 0


def _status(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    if getattr(args, "all", False):
        history = store.load_history()
        runs = history.get("runs", [])
        _print_json(
            {
                "schema_version": REPORT_SCHEMA,
                "status": "ok",
                "count": len(runs),
                "runs": runs,
                "root": str(store.root),
            }
        )
        return 0
    if not store.exists():
        _print_json(
            {
                "schema_version": REPORT_SCHEMA,
                "status": "no_run",
                "message": "No .harness/progress.json exists yet.",
                "blockers": [],
                "blockers_by_persona": {},
                "blockers_by_check": {},
                "root": str(store.root),
            }
        )
        return 0
    state = store.load()
    checks_by_status: Dict[str, int] = {}
    for check in state.checks:
        checks_by_status[check.status] = checks_by_status.get(check.status, 0) + 1
    findings_by_severity: Dict[str, int] = {}
    for finding in state.findings:
        findings_by_severity[finding.severity] = findings_by_severity.get(finding.severity, 0) + 1
    blockers_by_persona: Dict[str, int] = {}
    blockers_by_check: Dict[str, int] = {}
    for blocker in state.blockers:
        blockers_by_persona[blocker.persona] = (
            blockers_by_persona.get(blocker.persona, 0) + 1
        )
        blockers_by_check[blocker.check_id] = (
            blockers_by_check.get(blocker.check_id, 0) + 1
        )
    payload = {
        "schema_version": REPORT_SCHEMA,
        "status": state.status,
        "run_id": state.run_id,
        "workflow_id": state.workflow_id,
        "workflow_version": state.workflow_version,
        "target": state.target.to_dict(),
        "intent": state.intent,
        "current_phase": state.current_phase,
        "confidence": state.confidence.to_dict(),
        "checks_by_status": checks_by_status,
        "findings_by_severity": findings_by_severity,
        "blockers": [blocker.to_dict() for blocker in state.blockers],
        "blockers_by_persona": blockers_by_persona,
        "blockers_by_check": blockers_by_check,
        "kill_switch": state.kill_switch.to_dict(),
        "halted_reason": state.halted_reason,
        "report_path": str(store.report_path),
        "progress_path": str(store.progress_path),
        "started_at": state.started_at,
        "updated_at": state.updated_at,
        "completed_at": state.completed_at,
    }
    _print_json(payload)
    return 0


def _prune(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    result = store.prune(
        keep_runs=args.keep_runs,
        older_than_days=args.older_than_days,
        dry_run=args.dry_run,
    )
    _print_json(
        {
            "schema_version": REPORT_SCHEMA,
            "status": "dry_run" if args.dry_run else "pruned",
            "root": str(store.root),
            "removed": result["removed"],
            "retained": result["retained"],
            "count": len(result["retained"]),
        }
    )
    return 0


def _report(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    _print_json(report)
    return 0


def _abort(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    store.request_kill(args.reason)
    store.append_event({"event": "manual_kill_requested", "reason": args.reason})
    _print_json(
        {
            "schema_version": REPORT_SCHEMA,
            "status": "kill_requested",
            "reason": args.reason,
            "kill_path": str(store.kill_path),
            "requested_at": utc_now(),
        }
    )
    return 0


def _resolve_personas_directory(args: argparse.Namespace) -> Path:
    root = Path(args.root).expanduser().resolve()
    personas_dir = Path(args.personas_dir).expanduser()
    if not personas_dir.is_absolute():
        cwd_candidate = Path.cwd() / personas_dir
        root_candidate = root / personas_dir
        personas_dir = cwd_candidate if cwd_candidate.is_dir() else root_candidate
    return personas_dir


def _personas_list(args: argparse.Namespace) -> int:
    personas_dir = _resolve_personas_directory(args)
    personas = load_personas(
        personas_dir, selected=_csv(args.select) or None, require_minimum=0
    )
    payload = {
        "schema_version": "quality-harness.personas/v1",
        "directory": str(personas_dir),
        "count": len(personas),
        "personas": [
            {
                "id": persona.id,
                "name": persona.name,
                "version": persona.version,
                "domains": persona.domains,
                "tools": persona.tools,
                "checks": persona.checks,
                "path": str(persona.path),
                "output_schema": persona.output_schema,
                "blocker_schema": persona.blocker_schema,
                "refusal_handling": persona.refusal,
            }
            for persona in personas
        ],
    }
    _print_json(payload)
    return 0


def _personas_lint(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    personas_dir = _resolve_personas_directory(args)
    registry = build_registry(root, log=lambda message: None)
    result = lint_personas(personas_dir, known_check_ids=registry.ids())
    result["root"] = str(root)
    _print_json(result)
    return 0 if result["status"] == "valid" else 1


def _personas_scaffold(args: argparse.Namespace) -> int:
    if not args.persona_id:
        raise PersonaError("personas scaffold requires a persona id.")
    personas_dir = _resolve_personas_directory(args)
    result = scaffold_persona(
        args.persona_id,
        personas_dir,
        name=args.name,
        domains=getattr(args, "domain", None),
        checks=getattr(args, "check", None),
        force=args.force,
    )
    _print_json(result)
    return 0 if result["status"] in ("created", "overwritten") else 2


def _personas(args: argparse.Namespace) -> int:
    action = getattr(args, "personas_action", "list") or "list"
    if action != "scaffold" and args.persona_id:
        raise PersonaError(
            "A persona id is only valid with 'personas scaffold <id>'."
        )
    if action == "list":
        return _personas_list(args)
    if action == "lint":
        return _personas_lint(args)
    if action == "scaffold":
        return _personas_scaffold(args)
    raise PersonaError("Unknown personas action: {}".format(action))


def _calibrate(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    fixtures_dir = Path(args.fixtures_dir).expanduser()
    if not fixtures_dir.is_absolute():
        cwd_candidate = Path.cwd() / fixtures_dir
        root_candidate = root / fixtures_dir
        fixtures_dir = cwd_candidate if cwd_candidate.is_dir() else root_candidate
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = Path.cwd() / output
    report = write_calibration_report(fixtures_dir, output)
    _print_json(report)
    return 0


def _metering_emitter_for_report(store: StateStore, report: Dict[str, Any]) -> Optional[MeteringEmitter]:
    run_id = str(report.get("run_id", "")).strip()
    if not run_id:
        return None
    info = report.get("metering", {})
    if not isinstance(info, dict):
        info = {}
    sink_value = str(info.get("sink", "") or "").strip()
    sink = Path(sink_value).expanduser() if sink_value else None
    return MeteringEmitter(
        store.root,
        run_id,
        workspace=str(info.get("workspace", "local") or "local"),
        sink=sink,
    )


def _emit_export(
    store: StateStore,
    report: Dict[str, Any],
    export_format: str,
    output: str,
) -> None:
    try:
        emitter = _metering_emitter_for_report(store, report)
        if emitter is None:
            return
        emitter.emit(
            EVENT_EXPORT_GENERATED,
            1,
            "export",
            idempotency_key="{}:{}:{}:{}".format(
                report.get("run_id", ""), EVENT_EXPORT_GENERATED, export_format, output
            ),
            metadata={"format": export_format, "output": output},
        )
    except Exception as exc:
        print(
            "[quality-harness] WARNING: export metering event could not be written: {}".format(exc),
            file=sys.stderr,
        )


def _read_key(value: str) -> str:
    path = Path(value).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return value


def _sarif(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    sarif = build_sarif(report)
    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(sarif, indent=2, ensure_ascii=False), encoding="utf-8")
        _emit_export(store, report, "sarif", str(output_path))
        _print_json({"schema_version": REPORT_SCHEMA, "status": "written", "path": str(output_path)})
    else:
        _emit_export(store, report, "sarif", "stdout")
        _print_json(sarif)
    return 0


def _junit(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    xml_text = build_junit(report)
    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(xml_text, encoding="utf-8")
        _emit_export(store, report, "junit", str(output_path))
        _print_json({"schema_version": REPORT_SCHEMA, "status": "written", "path": str(output_path)})
    else:
        _emit_export(store, report, "junit", "stdout")
        print(xml_text)
    return 0


def _sign_report(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    signature = sign_report(report, _read_key(args.key))
    output_path = Path(args.output).expanduser() if args.output else store.root / "report.sig"
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(signature, encoding="utf-8")
    _emit_export(store, report, "signature", str(output_path))
    _print_json(
        {
            "schema_version": REPORT_SCHEMA,
            "status": "signed",
            "signature": signature,
            "path": str(output_path),
        }
    )
    return 0


def _verify_report(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    signature_value = args.signature
    signature_path = Path(signature_value).expanduser()
    if signature_path.is_file():
        signature_value = signature_path.read_text(encoding="utf-8").strip()
    valid = verify_report_signature(report, signature_value, _read_key(args.key))
    _print_json(
        {
            "schema_version": REPORT_SCHEMA,
            "status": "valid" if valid else "invalid",
            "verified": valid,
        }
    )
    return 0 if valid else 1


def _sarif(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    sarif = build_sarif(report)
    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(sarif, indent=2, ensure_ascii=False), encoding="utf-8")
        _emit_export(store, report, "sarif", str(output_path))
        _print_json({"schema_version": REPORT_SCHEMA, "status": "written", "path": str(output_path)})
    else:
        _emit_export(store, report, "sarif", "stdout")
        _print_json(sarif)
    return 0


def _junit(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    xml_text = build_junit(report)
    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(xml_text, encoding="utf-8")
        _emit_export(store, report, "junit", str(output_path))
        _print_json({"schema_version": REPORT_SCHEMA, "status": "written", "path": str(output_path)})
    else:
        _emit_export(store, report, "junit", "stdout")
        print(xml_text)
    return 0


def _read_key(value: str) -> str:
    path = Path(value).expanduser()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return value


def _sign_report(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    signature = sign_report(report, _read_key(args.key))
    output_path = Path(args.output).expanduser() if args.output else store.root / "report.sig"
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(signature, encoding="utf-8")
    _emit_export(store, report, "signature", str(output_path))
    _print_json(
        {
            "schema_version": REPORT_SCHEMA,
            "status": "signed",
            "signature": signature,
            "path": str(output_path),
        }
    )
    return 0


def _verify_report(args: argparse.Namespace) -> int:
    store = StateStore(Path(args.root).expanduser().resolve())
    report = store.load_report()
    signature_value = args.signature
    signature_path = Path(signature_value).expanduser()
    if signature_path.is_file():
        signature_value = signature_path.read_text(encoding="utf-8").strip()
    valid = verify_report_signature(report, signature_value, _read_key(args.key))
    _print_json(
        {
            "schema_version": REPORT_SCHEMA,
            "status": "valid" if valid else "invalid",
            "verified": valid,
        }
    )
    return 0 if valid else 1


def _suppress(args: argparse.Namespace) -> int:
    store = SuppressionStore(Path(args.root).expanduser().resolve())
    action = getattr(args, "suppress_action", None)
    if action == "add":
        record = store.add(
            args.fingerprint,
            owner=args.owner,
            reason=args.reason,
            ticket=args.ticket,
            expires_at=args.expires,
        )
        _print_json({"schema_version": "quality-harness.suppressions/v1", "status": "added", "suppression": record})
        return 0
    if action == "list":
        _print_json(
            {
                "schema_version": "quality-harness.suppressions/v1",
                "status": "ok",
                "suppressions": store.load(),
            }
        )
        return 0
    if action == "remove":
        record = store.remove(args.fingerprint)
        _print_json({"schema_version": "quality-harness.suppressions/v1", "status": "removed", "suppression": record})
        return 0
    raise PersonaError("Unknown suppression action: {}".format(action))


def _auth_token(value: str) -> str:
    if value.startswith("env:"):
        import os

        name = value[len("env:") :]
        token = os.environ.get(name, "").strip()
        if not token:
            raise HarnessError("Environment variable is empty or missing: {}".format(name))
        return token
    return _read_key(value)


def _auth(args: argparse.Namespace) -> int:
    from .auth import AUTH_SCHEMA, AuthError, AuthStore

    store = AuthStore(Path(args.root).expanduser().resolve())
    action = getattr(args, "auth_action", None)
    try:
        if action == "create-workspace":
            workspace = store.create_workspace(args.name, workspace_id=args.workspace_id)
            _print_json(
                {
                    "schema_version": AUTH_SCHEMA,
                    "status": "created",
                    "workspace": workspace,
                }
            )
            return 0
        if action == "list-workspaces":
            _print_json(
                {
                    "schema_version": AUTH_SCHEMA,
                    "status": "ok",
                    "workspaces": store.list_workspaces(),
                }
            )
            return 0
        if action == "create-key":
            key = store.create_key(
                args.workspace,
                scopes=args.scope or ["run"],
                label=args.label,
                expires_at=args.expires,
            )
            _print_json(
                {"schema_version": AUTH_SCHEMA, "status": "created", "key": key}
            )
            return 0
        if action == "list-keys":
            _print_json(
                {
                    "schema_version": AUTH_SCHEMA,
                    "status": "ok",
                    "keys": store.list_keys(args.workspace),
                }
            )
            return 0
        if action == "revoke-key":
            key = store.revoke_key(args.key, reason=args.reason)
            _print_json(
                {"schema_version": AUTH_SCHEMA, "status": "revoked", "key": key}
            )
            return 0
        if action == "rotate-key":
            kwargs = {"scopes": args.scope}
            if args.label is not None:
                kwargs["label"] = args.label
            if args.expires is not None:
                kwargs["expires_at"] = args.expires
            key = store.rotate_key(args.key, **kwargs)
            _print_json(
                {"schema_version": AUTH_SCHEMA, "status": "rotated", "key": key}
            )
            return 0
        if action == "authenticate":
            context = store.authenticate(
                _auth_token(args.token), required_scope=args.scope or None
            )
            _print_json(
                {
                    "schema_version": AUTH_SCHEMA,
                    "status": "authenticated",
                    "context": context.to_dict(),
                }
            )
            return 0
        if action == "claim-run":
            run = store.claim_run(args.workspace, args.run)
            _print_json(
                {"schema_version": AUTH_SCHEMA, "status": "claimed", "run": run}
            )
            return 0
        if action == "authorize-run":
            context = store.authenticate(_auth_token(args.token))
            run = store.authorize_run(context, args.run, required_scope=args.scope or None)
            _print_json(
                {
                    "schema_version": AUTH_SCHEMA,
                    "status": "authorized",
                    "context": context.to_dict(),
                    "run": run,
                }
            )
            return 0
        raise HarnessError("Unknown auth action: {}".format(action))
    except AuthError as exc:
        payload = {
            "schema_version": AUTH_SCHEMA,
            "status": "error",
            "command": "auth",
            "http_status": exc.status_code,
            "status_code": exc.status_code,
            "error": exc.to_dict()["error"],
        }
        _print_json(payload)
        return 1


def _quota(args: argparse.Namespace) -> int:
    from .quota import SCHEMA_VERSION, QuotaError, QuotaExceeded, QuotaStore

    action = getattr(args, "quota_action", None)
    try:
        store = QuotaStore(
            Path(args.root).expanduser().resolve(),
            plan=args.plan,
            workspace=args.workspace,
            enforce=True if args.enforce else None,
        )
        if action == "show":
            payload = store.snapshot(now=args.now)
            payload["command"] = "quota"
            _print_json(payload)
            return 0
        if action == "consume":
            try:
                decision = store.consume(
                    args.resource, args.quantity, now=args.now
                )
            except QuotaExceeded as exc:
                payload = exc.to_dict()
                payload["command"] = "quota"
                _print_json(payload)
                return 1
            payload = decision.to_dict()
            payload["command"] = "quota"
            _print_json(payload)
            return 0
        raise HarnessError("Unknown quota action: {}".format(action))
    except (QuotaError, ValueError) as exc:
        _print_json(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "error",
                "command": "quota",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        return 2


def _ledger(args: argparse.Namespace) -> int:
    from .ledger import (
        INVOICE_SCHEMA_VERSION,
        SCHEMA_VERSION,
        LedgerError,
        LedgerStore,
        PriceBook,
        invoice_to_csv,
    )
    from .metering import MeteringEmitter

    root = Path(args.root).expanduser().resolve()
    store = LedgerStore(root)
    action = getattr(args, "ledger_action", None)
    try:
        if action == "ingest":
            emitter = MeteringEmitter(root, "ledger-ingest", sink=args.events)
            price_book = PriceBook.load(args.prices) if args.prices else None
            result = store.ingest_events(
                emitter.events(),
                workspace=args.workspace,
                price_book=price_book,
                strict=args.strict,
            )
            result["command"] = "ledger"
            result["events_path"] = str(emitter.sink)
            _print_json(result)
            return 0 if not result.get("rejected") else 1

        if action == "entries":
            entries = store.entries(
                workspace=args.workspace, period=args.period
            )
            _print_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "ok",
                    "command": "ledger",
                    "count": len(entries),
                    "entries": entries,
                }
            )
            return 0

        if action == "periods":
            periods = store.periods(workspace=args.workspace)
            _print_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "ok",
                    "command": "ledger",
                    "count": len(periods),
                    "periods": periods,
                }
            )
            return 0

        if action == "close":
            record = store.close_period(
                args.workspace,
                args.period,
                closed_by=args.by,
                closed_at=args.at,
            )
            _print_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "closed" if record.get("created") else "already_closed",
                    "command": "ledger",
                    "period": record,
                }
            )
            return 0

        if action == "adjust":
            price_book = PriceBook.load(args.prices) if args.prices else None
            result = store.add_adjustment(
                args.workspace,
                args.period,
                args.event_type,
                args.quantity,
                reason=args.reason,
                references=args.references,
                unit=args.unit,
                unit_price=args.unit_price,
                price_book=price_book,
                idempotency_key=args.idempotency_key,
                timestamp=args.at,
            )
            result["command"] = "ledger"
            _print_json(result)
            return 0

        if action == "invoice":
            invoice = store.invoice(
                args.workspace,
                args.period,
                invoice_id=args.invoice_id,
                generated_at=args.generated_at,
            )
            if args.format == "csv":
                text = invoice_to_csv(invoice)
            else:
                text = json.dumps(invoice, indent=2, ensure_ascii=False) + "\n"
            if args.output:
                output_path = Path(args.output).expanduser()
                if not output_path.is_absolute():
                    output_path = Path.cwd() / output_path
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(text, encoding="utf-8")
                _print_json(
                    {
                        "schema_version": INVOICE_SCHEMA_VERSION,
                        "status": "written",
                        "command": "ledger",
                        "path": str(output_path),
                        "invoice": invoice,
                    }
                )
            elif args.format == "csv":
                print(text, end="")
            else:
                _print_json(invoice)
            return 0

        if action == "verify":
            result = store.verify()
            result["command"] = "ledger"
            _print_json(result)
            return 0 if result.get("ok") else 1

        if action == "reconcile":
            result = store.reconcile(
                args.workspace,
                args.period,
                invoice_id=args.invoice_id,
            )
            result["command"] = "ledger"
            _print_json(result)
            return 0 if result.get("ok") else 1

        raise HarnessError("Unknown ledger action: {}".format(action))
    except (LedgerError, ValueError, OSError) as exc:
        _print_json(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "error",
                "command": "ledger",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        return getattr(exc, "exit_code", 2)


def _signup(args: argparse.Namespace) -> int:
    from .quota import QuotaPlan
    from .signup import SignupStore, verify_entitlement

    root = Path(args.root).expanduser().resolve()
    store = SignupStore(root)
    action = getattr(args, "signup_action", None)
    try:
        if action == "create":
            plan = None
            if args.plan:
                plan = QuotaPlan.load(args.plan)
            result = store.signup(
                args.request_id,
                name=args.name,
                plan=plan,
                effective_at=args.effective_at,
            )
            _print_json(result)
            return 0
        if action == "select-plan":
            plan = QuotaPlan.load(args.plan)
            result = store.select_plan(
                args.workspace,
                plan,
                effective_at=args.effective_at,
            )
            _print_json(result)
            return 0
        if action == "usage":
            plan = QuotaPlan.load(args.plan) if args.plan else None
            result = store.usage(args.workspace, plan=plan)
            _print_json(result)
            return 0
        if action == "verify":
            entitlement = json.loads(
                Path(args.entitlement).expanduser().read_text(encoding="utf-8")
            )
            result = store.verify_entitlement(
                entitlement,
                signing_key=args.signing_key,
                workspace_id=args.workspace,
                period=args.period,
            )
            _print_json(result)
            return 0 if result.get("valid") else 1
        raise HarnessError("Unknown signup action: {}".format(action))
    except Exception as exc:  # SignupError is a HarnessError subclass.
        _print_json(
            {
                "schema_version": "quality-harness.signup/v1",
                "status": "error",
                "command": "signup",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        return getattr(exc, "exit_code", 2)


def _billing(args: argparse.Namespace) -> int:
    from .billing import (
        BillingFeatureFlag,
        BillingStore,
        EnvSecretManager,
        FileSecretManager,
        SandboxBillingProvider,
    )

    root = Path(args.root).expanduser().resolve()
    flag_path = root / ".harness" / "billing" / "flag.json"
    if flag_path.exists():
        try:
            flag = BillingFeatureFlag.from_dict(
                json.loads(flag_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, Exception):
            flag = BillingFeatureFlag.from_env()
    else:
        flag = BillingFeatureFlag.from_env()
    if getattr(args, "secret_store", None):
        secret_manager = FileSecretManager(args.secret_store)
    else:
        secret_manager = EnvSecretManager()
    provider = SandboxBillingProvider(root)
    store = BillingStore(
        root,
        feature_flag=flag,
        secret_manager=secret_manager,
        provider=provider,
    )
    action = getattr(args, "billing_action", None)
    try:
        if action == "status":
            _print_json(store.status(args.workspace))
            return 0
        if action == "sync":
            result = store.sync_invoice(
                args.workspace,
                args.period,
                invoice_id=args.invoice_id,
                secret_ref=args.secret_ref,
            )
            _print_json(result)
            return 0 if result.get("status") in {"reconciled", "already_reconciled", "disabled"} else 1
        if action == "disable":
            result = store.disable(args.reason)
            result["kill_switch_active"] = store.kill_switch_active()
            _print_json(result)
            return 0
        if action == "enable":
            store.enable_writes()
            _print_json(
                {
                    "schema_version": "quality-harness.billing/v1",
                    "status": "enabled",
                    "kill_switch_active": store.kill_switch_active(),
                }
            )
            return 0
        raise HarnessError("Unknown billing action: {}".format(action))
    except Exception as exc:
        _print_json(
            {
                "schema_version": "quality-harness.billing/v1",
                "status": "error",
                "command": "billing",
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        return getattr(exc, "exit_code", 2)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "status":
            return _status(args)
        if args.command == "prune":
            return _prune(args)
        if args.command == "report":
            return _report(args)
        if args.command == "abort":
            return _abort(args)
        if args.command == "personas":
            return _personas(args)
        if args.command == "calibrate":
            return _calibrate(args)
        if args.command == "sarif":
            return _sarif(args)
        if args.command == "junit":
            return _junit(args)
        if args.command == "sign-report":
            return _sign_report(args)
        if args.command == "verify-report":
            return _verify_report(args)
        if args.command in ("suppress", "suppressions"):
            return _suppress(args)
        if args.command == "auth":
            return _auth(args)
        if args.command == "quota":
            return _quota(args)
        if args.command == "ledger":
            return _ledger(args)
        if args.command == "signup":
            return _signup(args)
        if args.command == "billing":
            return _billing(args)
        parser.error("Unknown command: {}".format(args.command))
    except KeyboardInterrupt:
        try:
            store = StateStore(Path(getattr(args, "root", ".")).expanduser().resolve())
            store.request_kill("Operator interrupted with Ctrl-C.")
        except Exception:
            pass
        _print_json(_error_payload(HarnessError("Interrupted by operator"), args.command))
        return 130
    except HarnessError as exc:
        _print_json(_error_payload(exc, args.command))
        return exc.exit_code
    except Exception as exc:
        _print_json(_error_payload(exc, args.command))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
