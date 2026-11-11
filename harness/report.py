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
        "decision": decision,
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
