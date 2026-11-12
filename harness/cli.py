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
