"""Weighted, gap-aware release confidence.

The score is deliberately explainable:

    base = quality * coverage_factor * scope_factor
    final = clamp(base - severity_penalty, caps)

It is scoped engineering judgment, not the probability that no defect exists.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from .models import Check, CheckStatus, Confidence, Finding, Severity, severity_rank

FORMULA_VERSION = "weighted-coverage-v1"

DOMAIN_WEIGHTS = {
    "security": 1.5,
    "data_integrity": 1.3,
    "functional": 1.3,
    "accessibility": 1.2,
    "reliability": 1.2,
    "performance": 1.0,
    "content": 0.8,
}

SEVERITY_PENALTY = {
    Severity.CRITICAL.value: 45.0,
    Severity.HIGH.value: 20.0,
    Severity.MEDIUM.value: 8.0,
    Severity.LOW.value: 2.0,
    Severity.INFO.value: 0.0,
}

SCOPE_FACTOR = {"both": 1.0, "source": 0.9, "live": 0.9, "none": 0.75}


def _fingerprint(finding: Finding) -> str:
    if finding.fingerprint:
        return finding.fingerprint
    return "{}|{}|{}".format(finding.domain, finding.title.strip().lower(), finding.location)


def _scope_mode(checks: Iterable[Check]) -> str:
    kinds = {check.kind for check in checks}
    if "static" in kinds and "browser" in kinds:
        return "both"
    if "static" in kinds:
        return "source"
    if "browser" in kinds:
        return "live"
    return "none"


def score_confidence(
    checks: List[Check],
    findings: List[Finding],
    limitations: List[str] = None,
    halted: bool = False,
    kill_reason: str = "",
) -> Confidence:
    limitations = list(limitations or [])
    if not checks:
        return Confidence(
            score=0.0,
            band="none",
            coverage=0.0,
            rationale="No checks were planned, so no release confidence can be claimed.",
            limitations=limitations
            + ["No checks were planned or executed.", "A confidence score is not a probability of zero defects."],
            breakdown={"formula": FORMULA_VERSION, "planned_checks": 0},
            gate={"status": "fail", "reasons": ["No checks were planned."], "policy": "advisory-v1"},
        )

    total_weight = sum(max(check.weight, 0.0001) for check in checks)
    executed = [check for check in checks if check.status in (CheckStatus.PASSED.value, CheckStatus.FAILED.value)]
    executed_weight = sum(max(check.weight, 0.0001) for check in executed)
    passed = [check for check in executed if check.status == CheckStatus.PASSED.value]
    passed_weight = sum(max(check.weight, 0.0001) for check in passed)
    coverage_ratio = executed_weight / total_weight if total_weight else 0.0
    quality = (passed_weight / executed_weight * 100.0) if executed_weight else 0.0

    scope = _scope_mode(checks)
    scope_factor = SCOPE_FACTOR.get(scope, 0.75)
    coverage_factor = 0.25 + 0.75 * coverage_ratio
    base = quality * coverage_factor * scope_factor

    # Deduplicate findings by behaviour/location before penalising.
    unique: Dict[str, Finding] = {}
    for finding in findings:
        unique.setdefault(_fingerprint(finding), finding)
    unique_findings = list(unique.values())

    findings_by_severity: Dict[str, int] = {}
    penalty = 0.0
    for finding in unique_findings:
        severity = str(finding.severity)
        findings_by_severity[severity] = findings_by_severity.get(severity, 0) + 1
        points = SEVERITY_PENALTY.get(severity, 0.0)
        if finding.strength == "suspected":
            points *= 0.5
        penalty += points
    penalty = min(penalty, 70.0)

    score = base - penalty
    caps: List[Dict[str, Any]] = []
    if findings_by_severity.get(Severity.CRITICAL.value, 0) > 0:
        if score > 30.0:
            caps.append({"reason": "critical finding", "cap": 30.0})
        score = min(score, 30.0)
    if findings_by_severity.get(Severity.HIGH.value, 0) >= 2 and score > 65.0:
        caps.append({"reason": "two or more high findings", "cap": 65.0})
        score = min(score, 65.0)
    if halted and score > 25.0:
        caps.append({"reason": "kill switch halted execution", "cap": 25.0})
        score = min(score, 25.0)
    score = round(max(0.0, min(100.0, score)), 1)

    domains: Dict[str, Dict[str, Any]] = {}
    for check in checks:
        bucket = domains.setdefault(
            check.domain,
            {"weight": 0.0, "executed_weight": 0.0, "passed_weight": 0.0, "checks": 0, "passed": 0, "failed": 0, "blocked": 0},
        )
        bucket["weight"] += check.weight
        bucket["checks"] += 1
        if check.status == CheckStatus.PASSED.value:
            bucket["executed_weight"] += check.weight
            bucket["passed_weight"] += check.weight
            bucket["passed"] += 1
        elif check.status == CheckStatus.FAILED.value:
            bucket["executed_weight"] += check.weight
            bucket["failed"] += 1
        else:
            bucket["blocked"] += 1
    for domain, bucket in domains.items():
        if bucket["executed_weight"]:
            bucket["score"] = round(bucket["passed_weight"] / bucket["executed_weight"] * 100.0, 1)
        else:
            bucket["score"] = 0.0
        bucket["coverage"] = round(
            bucket["executed_weight"] / bucket["weight"] * 100.0 if bucket["weight"] else 0.0, 1
        )

    blocked = [
        check
        for check in checks
        if check.status in (CheckStatus.BLOCKED.value, CheckStatus.SKIPPED.value, CheckStatus.ERROR.value)
    ]
    for check in checks:
        if check.status == CheckStatus.FAILED.value and not check.finding_ids:
            limitations.append(
                "Check '{}' failed without a recorded finding; treat its result as unexplained.".format(check.id)
            )
    if blocked:
        limitations.append(
            "{} planned check(s) were blocked, skipped, or errored and count as coverage gaps, not passes.".format(
                len(blocked)
            )
        )
    if scope == "source":
        limitations.append("Static-only evidence: rendered behavior was not exercised.")
    elif scope == "live":
        limitations.append("Live-only evidence: source and deployment configuration were not inspected.")
    if halted:
        limitations.append("Execution was halted by the kill switch: {}.".format(kill_reason or "critical finding"))
    limitations.append("Confidence is scoped engineering judgment, not a probability of zero defects.")

    band = "high" if score >= 85 else "moderate" if score >= 70 else "guarded" if score >= 50 else "low"
    if score <= 0.0:
        band = "none"

    gate_reasons: List[str] = []
    if findings_by_severity.get(Severity.CRITICAL.value, 0):
        gate_reasons.append("One or more critical findings are present.")
    if findings_by_severity.get(Severity.HIGH.value, 0):
        gate_reasons.append("{} high-severity finding(s) remain open.".format(findings_by_severity[Severity.HIGH.value]))
    if coverage_ratio < 0.7:
        gate_reasons.append("Executed coverage is below 70% of planned weight.")
    if score < 60.0:
        gate_reasons.append("Confidence is below the advisory release floor of 60.")
    if findings_by_severity.get(Severity.CRITICAL.value, 0) or score < 60.0:
        gate_status = "fail"
    elif score < 80.0 or findings_by_severity.get(Severity.HIGH.value, 0) or coverage_ratio < 0.8:
        gate_status = "warn"
    else:
        gate_status = "pass"
    if not gate_reasons:
        gate_reasons.append("No advisory release blockers were detected in the executed scope.")

    rationale = (
        "Executed {executed_weight:.1f} of {total_weight:.1f} planned weight ({coverage:.0f}% coverage); "
        "quality among executed checks was {quality:.0f}%; scope factor {scope_factor:.2f}. "
        "Severity penalty was {penalty:.1f} points. Final confidence {score:.1f}/100 ({band})."
    ).format(
        executed_weight=executed_weight,
        total_weight=total_weight,
        coverage=coverage_ratio * 100.0,
        quality=quality,
        scope_factor=scope_factor,
        penalty=penalty,
        score=score,
        band=band,
    )

    return Confidence(
        score=score,
        band=band,
        coverage=round(coverage_ratio * 100.0, 1),
        rationale=rationale,
        limitations=limitations,
        penalties={
            "severity_total": round(penalty, 1),
            "by_severity": findings_by_severity,
            "caps": caps,
        },
        domain_scores=domains,
        breakdown={
            "formula": FORMULA_VERSION,
            "planned_checks": len(checks),
            "executed_checks": len(executed),
            "passed_checks": len(passed),
            "failed_checks": len(executed) - len(passed),
            "blocked_checks": len(blocked),
            "total_weight": round(total_weight, 3),
            "executed_weight": round(executed_weight, 3),
            "passed_weight": round(passed_weight, 3),
            "coverage_ratio": round(coverage_ratio, 4),
            "quality": round(quality, 1),
            "coverage_factor": round(coverage_factor, 4),
            "scope_mode": scope,
            "scope_factor": scope_factor,
            "base_score": round(base, 1),
            "severity_penalty": round(penalty, 1),
            "score": score,
            "band": band,
            "halted": halted,
        },
        gate={"status": gate_status, "reasons": gate_reasons, "policy": "advisory-v1"},
    )
