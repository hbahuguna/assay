"""Build the stable JSON report printed by the CLI."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import CheckStatus, RunState, Severity, severity_rank, utc_now
from .workflows import Workflow


REPORT_SCHEMA = "quality-harness.report/v1"


def _coverage(state: RunState) -> Dict[str, Any]:
    counts = {status.value: 0 for status in CheckStatus}
    blockers_by_check: Dict[str, int] = {}
    blockers_by_persona: Dict[str, int] = {}
    for blocker in state.blockers:
        blockers_by_check[blocker.check_id] = (
            blockers_by_check.get(blocker.check_id, 0) + 1
        )
        blockers_by_persona[blocker.persona] = (
            blockers_by_persona.get(blocker.persona, 0) + 1
        )
    by_domain: Dict[str, Dict[str, int]] = {}
    for check in state.checks:
        counts[check.status] = counts.get(check.status, 0) + 1
        bucket = by_domain.setdefault(
            check.domain,
            {
                "planned": 0,
                "passed": 0,
                "failed": 0,
                "blocked": 0,
                "skipped": 0,
                "error": 0,
                "blockers": 0,
            },
        )
        bucket["planned"] += 1
        bucket["blockers"] += blockers_by_check.get(check.id, 0)
        if check.status == CheckStatus.PASSED.value:
            bucket["passed"] += 1
        elif check.status == CheckStatus.FAILED.value:
            bucket["failed"] += 1
        elif check.status == CheckStatus.BLOCKED.value:
            bucket["blocked"] += 1
        elif check.status == CheckStatus.SKIPPED.value:
            bucket["skipped"] += 1
        elif check.status == CheckStatus.ERROR.value:
            bucket["error"] += 1
    total = len(state.checks)
    executed = counts.get(CheckStatus.PASSED.value, 0) + counts.get(CheckStatus.FAILED.value, 0)
    covered_blockers = len(
        [blocker for blocker in state.blockers if blocker.covered_elsewhere]
    )
    return {
        "total_checks": total,
        "executed": executed,
        "passed": counts.get(CheckStatus.PASSED.value, 0),
        "failed": counts.get(CheckStatus.FAILED.value, 0),
        "blocked": counts.get(CheckStatus.BLOCKED.value, 0),
        "skipped": counts.get(CheckStatus.SKIPPED.value, 0),
        "error": counts.get(CheckStatus.ERROR.value, 0),
        "planned_not_run": counts.get(CheckStatus.PLANNED.value, 0),
        "blockers": len(state.blockers),
        "blockers_covered_elsewhere": covered_blockers,
        "blockers_uncovered": len(state.blockers) - covered_blockers,
        "blockers_by_persona": blockers_by_persona,
        "executed_pct": round(executed / total * 100.0, 1) if total else 0.0,
        "by_domain": by_domain,
    }


def decision_for_state(state: RunState) -> Dict[str, str]:
    gate = state.confidence.gate or {}
    status = gate.get("status", "warn")
    if state.status == "halted":
        return {
            "decision": "hold",
            "rationale": "Execution was halted by the kill switch; the evidence set is intentionally incomplete.",
        }
    if state.status == "failed":
        return {"decision": "hold", "rationale": "The harness run failed before producing a complete evidence set."}
    if status == "pass":
        return {
            "decision": "ship",
            "rationale": "Executed checks support a release decision within the stated scope; gaps remain visible in the report.",
        }
    if status == "fail":
        return {"decision": "hold", "rationale": "Critical/high risk or low confidence blocks an advisory release decision."}
    return {
        "decision": "review",
        "rationale": "Evidence is useful but incomplete or accompanied by high-severity findings; review before shipping.",
    }


def build_report(
    state: RunState,
    workflow: Workflow,
    evidence_summary: Dict[str, Any],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    findings = sorted(
        state.findings,
        key=lambda item: (-severity_rank(item.severity), item.created_at, item.id),
    )
    invalid_findings = sorted(
        state.invalid_findings,
        key=lambda item: (item.recorded_at, item.id),
    )
    blockers = sorted(
        state.blockers,
        key=lambda item: (item.recorded_at, item.id),
    )
    contract_failures = {
        persona: int(count) for persona, count in state.contract_failures.items()
    }
    policy_info = state.options.get("policy", {}) if isinstance(state.options, dict) else {}
    if not isinstance(policy_info, dict):
        policy_info = {}
    policy_id = str(policy_info.get("id", "advisory-v1"))
    policy_version = str(policy_info.get("version", "1.0"))
    trend = state.options.get("trend", {}) if isinstance(state.options, dict) else {}
    if not isinstance(trend, dict):
        trend = {}
    trend = {
        "baseline": bool(trend.get("baseline", True)),
        "previous_run_id": trend.get("previous_run_id"),
        "new": list(trend.get("new", [])),
        "resolved": list(trend.get("resolved", [])),
        "persistent": list(trend.get("persistent", [])),
        "score_delta": float(trend.get("score_delta", 0.0)),
        "coverage_delta": float(trend.get("coverage_delta", 0.0)),
    }
    metering_info = state.options.get("metering", {}) if isinstance(state.options, dict) else {}
    if not isinstance(metering_info, dict):
        metering_info = {}
    metering = {
        "schema_version": str(
            metering_info.get("schema_version", "quality-harness.metering/v1")
        ),
        "sink": str(metering_info.get("sink", ".harness/metering/events.jsonl")),
        "workspace": str(metering_info.get("workspace", "local")),
        "event_count": int(metering_info.get("event_count", 0)),
        "event_types": {
            str(key): int(value)
            for key, value in dict(metering_info.get("event_types", {})).items()
        },
    }
    personas = []
    for persona in state.personas:
        entry = dict(persona)
        persona_id = str(entry.get("id", ""))
        entry["contract_failures"] = contract_failures.get(persona_id, 0)
        entry["contract_failure_streak"] = int(
            state.contract_failure_streaks.get(persona_id, 0)
        )
        entry["degraded"] = persona_id in state.degraded_personas
        personas.append(entry)
    checks = [
        {
            "id": check.id,
            "title": check.title,
            "domain": check.domain,
            "persona": check.persona,
            "kind": check.kind,
            "intent": check.intent,
            "expected": check.expected,
            "status": check.status,
            "weight": check.weight,
            "actual": check.actual,
            "evidence": check.evidence,
            "finding_ids": check.finding_ids,
            "skip_reason": check.skip_reason,
            "metadata": check.metadata,
        }
        for check in state.checks
    ]
    decision = decision_for_state(state)
    limitations = list(state.confidence.limitations)
    for check in state.checks:
        if check.metadata.get("limitation"):
            note = str(check.metadata["limitation"])
            if note not in limitations:
                limitations.append(note)
    if any(check.id == "browser.accessibility" for check in state.checks):
        note = "Automated accessibility checks do not establish WCAG conformance; human review is still required."
        if note not in limitations:
            limitations.append(note)
    if any(check.id == "browser.security" for check in state.checks):
        note = "Security checks are bounded and non-destructive; they are not a penetration test or a certification."
        if note not in limitations:
            limitations.append(note)
    if invalid_findings:
        note = (
            "{} finding(s) violated a persona output contract and were quarantined; "
            "they are excluded from scoring and kill-switch evaluation.".format(
                len(invalid_findings)
            )
        )
        if note not in limitations:
            limitations.append(note)
    for persona in state.degraded_personas:
        note = (
            "Persona '{}' produced three consecutive schema-invalid findings and is "
            "marked degraded.".format(persona)
        )
        if note not in limitations:
            limitations.append(note)

    severity_counts = {severity.value: 0 for severity in Severity}
    for finding in findings:
        severity_counts[finding.severity] = severity_counts.get(finding.severity, 0) + 1

    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": generated_at or utc_now(),
        "run_id": state.run_id,
        "determinism": {
            "seed": state.seed,
            "clock_mode": state.clock_mode,
            "entropy": "seeded" if state.seed is not None else "uuid4",
        },
        "status": state.status,
        "workflow": {
            "id": workflow.id,
            "name": workflow.name,
            "version": workflow.version,
            "phases": [phase.name for phase in workflow.phases],
        },
        "target": state.target.to_dict(),
        "intent": state.intent,
        "policy": {"id": policy_id, "version": policy_version},
        "metering": metering,
        "decision": decision,
        "trend": trend,
        "confidence": state.confidence.to_dict(),
        "findings": [finding.to_dict() for finding in findings],
        "finding_counts": severity_counts,
        "invalid_findings": [finding.to_dict() for finding in invalid_findings],
        "blockers": [blocker.to_dict() for blocker in blockers],
        "contract_failures": {
            "total": sum(contract_failures.values()),
            "by_persona": contract_failures,
            "degraded_personas": list(state.degraded_personas),
        },
        "checks": checks,
        "coverage": _coverage(state),
        "risks": [risk.to_dict() for risk in state.risks],
        "personas": personas,
        "inventory": state.inventory,
        "phases": [phase.to_dict() for phase in state.phases],
        "kill_switch": state.kill_switch.to_dict(),
        "halted_reason": state.halted_reason,
        "limitations": limitations,
        "evidence": {
            "directory": state.evidence_dir,
            "summary": evidence_summary,
        },
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "options": state.options,
        "error": state.error,
    }


import json
from pathlib import Path

REPORT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "report" / "report.v1.schema.json"
)


def report_schema() -> Dict[str, Any]:
    """Load the published JSON Schema for ``quality-harness.report/v1``."""

    return json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))


def _json_type_ok(value: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple)):
        return any(_json_type_ok(value, item) for item in expected)
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def validate_report(report: Any) -> List[str]:
    """Return validation errors for a report, or an empty list when valid."""

    schema = report_schema()
    errors: List[str] = []

    def visit(instance: Any, subschema: Dict[str, Any], path: str) -> None:
        if "const" in subschema and instance != subschema["const"]:
            errors.append(
                "{}: expected constant {}".format(path or "<root>", subschema["const"])
            )
        expected_type = subschema.get("type")
        if expected_type is not None and not _json_type_ok(instance, expected_type):
            errors.append(
                "{}: expected type {}, got {}".format(
                    path or "<root>", expected_type, type(instance).__name__
                )
            )
            return
        if isinstance(instance, dict):
            for field in subschema.get("required", []):
                if field not in instance:
                    errors.append("{}: missing required field {}".format(path or "<root>", field))
            properties = subschema.get("properties", {})
            for field, value in instance.items():
                if field in properties:
                    visit(value, properties[field], "{}/{}".format(path, field) if path else field)
        elif isinstance(instance, list) and "items" in subschema:
            for index, item in enumerate(instance):
                visit(item, subschema["items"], "{}[{}]".format(path, index))

    visit(report, schema, "")
    return errors


def is_valid_report(report: Any) -> bool:
    return not validate_report(report)
