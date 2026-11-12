"""Weighted, gap-aware release confidence.

The score is deliberately explainable:

    base = quality * coverage_factor * scope_factor
    final = clamp(base - severity_penalty, caps)

It is scoped engineering judgment, not the probability that no defect exists.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .errors import PolicyError
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

DEFAULT_POLICY_ID = "advisory-v1"
DEFAULT_POLICY_VERSION = "1.0"


@dataclass
class Policy:
    id: str = DEFAULT_POLICY_ID
    version: str = DEFAULT_POLICY_VERSION
    domain_weights: Dict[str, float] = field(default_factory=lambda: dict(DOMAIN_WEIGHTS))
    severity_penalties: Dict[str, float] = field(default_factory=lambda: dict(SEVERITY_PENALTY))
    scope_factors: Dict[str, float] = field(default_factory=lambda: dict(SCOPE_FACTOR))
    penalty_cap: float = 70.0
    caps: Dict[str, float] = field(default_factory=lambda: {"critical": 30.0, "two_high": 65.0, "halted": 25.0})
    gate: Dict[str, Any] = field(
        default_factory=lambda: {
            "pass_score": 80.0,
            "warn_score": 60.0,
            "fail_on_critical": True,
            "fail_on_high": False,
            "coverage_warn": 0.8,
            "coverage_fail": 0.7,
        }
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": "quality-harness.policy/v1",
            "id": self.id,
            "version": self.version,
            "domain_weights": dict(self.domain_weights),
            "severity_penalties": dict(self.severity_penalties),
            "scope_factors": dict(self.scope_factors),
            "penalty_cap": self.penalty_cap,
            "caps": dict(self.caps),
            "gate": dict(self.gate),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Policy":
        if not isinstance(data, dict):
            raise PolicyError("Policy must be a JSON object.")
        allowed = {
            "schema_version",
            "id",
            "version",
            "domain_weights",
            "severity_penalties",
            "scope_factors",
            "penalty_cap",
            "caps",
            "gate",
        }
        unknown = set(data) - allowed
        if unknown:
            raise PolicyError("Unknown policy field(s): {}.".format(", ".join(sorted(unknown))))
        if data.get("schema_version", "quality-harness.policy/v1") != "quality-harness.policy/v1":
            raise PolicyError("Policy schema_version must be 'quality-harness.policy/v1'.")
        policy_id = data.get("id", DEFAULT_POLICY_ID)
        version = data.get("version", DEFAULT_POLICY_VERSION)
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise PolicyError("Policy id must be a non-empty string.")
        if not isinstance(version, str) or not version.strip():
            raise PolicyError("Policy version must be a non-empty string.")

        domain_weights = data.get("domain_weights", dict(DOMAIN_WEIGHTS))
        severity_penalties = data.get("severity_penalties", dict(SEVERITY_PENALTY))
        scope_factors = data.get("scope_factors", dict(SCOPE_FACTOR))
        penalty_cap = data.get("penalty_cap", 70.0)
        caps = data.get("caps", {"critical": 30.0, "two_high": 65.0, "halted": 25.0})
        gate = data.get("gate", {
            "pass_score": 80.0,
            "warn_score": 60.0,
            "fail_on_critical": True,
            "fail_on_high": False,
            "coverage_warn": 0.8,
            "coverage_fail": 0.7,
        })

        if not isinstance(domain_weights, dict) or not domain_weights:
            raise PolicyError("domain_weights must be a non-empty object.")
        for name, value in domain_weights.items():
            if not isinstance(name, str) or not name:
                raise PolicyError("domain_weights keys must be non-empty strings.")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 or value > 10:
                raise PolicyError("domain_weights values must be numbers greater than 0 and at most 10.")
        if not isinstance(severity_penalties, dict) or not severity_penalties:
            raise PolicyError("severity_penalties must be a non-empty object.")
        for name, value in severity_penalties.items():
            if name not in SEVERITY_PENALTY:
                raise PolicyError("Unknown severity penalty key: {}.".format(name))
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 100:
                raise PolicyError("severity_penalties values must be numbers from 0 to 100.")
        if not isinstance(scope_factors, dict):
            raise PolicyError("scope_factors must be an object.")
        for key in ("both", "source", "live", "none"):
            if key not in scope_factors:
                raise PolicyError("scope_factors must include '{}'.".format(key))
        for key, value in scope_factors.items():
            if key not in ("both", "source", "live", "none"):
                raise PolicyError("Unknown scope factor key: {}.".format(key))
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 1:
                raise PolicyError("scope_factors values must be numbers from 0 to 1.")
        if not isinstance(penalty_cap, (int, float)) or isinstance(penalty_cap, bool) or penalty_cap < 0 or penalty_cap > 100:
            raise PolicyError("penalty_cap must be a number from 0 to 100.")
        if not isinstance(caps, dict):
            raise PolicyError("caps must be an object.")
        for key in ("critical", "two_high", "halted"):
            if key not in caps:
                raise PolicyError("caps must include '{}'.".format(key))
        for key, value in caps.items():
            if key not in ("critical", "two_high", "halted"):
                raise PolicyError("Unknown cap key: {}.".format(key))
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 100:
                raise PolicyError("cap values must be numbers from 0 to 100.")
        if not (caps["critical"] <= caps["two_high"]):
            raise PolicyError("critical cap must not exceed the two-high cap.")
        if not isinstance(gate, dict):
            raise PolicyError("gate must be an object.")
        required_gate = {"pass_score", "warn_score", "fail_on_critical", "fail_on_high", "coverage_warn", "coverage_fail"}
        if not required_gate.issubset(gate):
            raise PolicyError("gate is missing required keys.")
        if set(gate) - required_gate:
            raise PolicyError("gate contains unknown keys.")
        pass_score = gate["pass_score"]
        warn_score = gate["warn_score"]
        if not isinstance(pass_score, (int, float)) or isinstance(pass_score, bool) or not 0 <= pass_score <= 100:
            raise PolicyError("gate.pass_score must be a number from 0 to 100.")
        if not isinstance(warn_score, (int, float)) or isinstance(warn_score, bool) or not 0 <= warn_score <= 100:
            raise PolicyError("gate.warn_score must be a number from 0 to 100.")
        if pass_score < warn_score:
            raise PolicyError("gate.pass_score must be at least gate.warn_score.")
        if not isinstance(gate["fail_on_critical"], bool):
            raise PolicyError("gate.fail_on_critical must be a boolean.")
        if not isinstance(gate["fail_on_high"], bool):
            raise PolicyError("gate.fail_on_high must be a boolean.")
        for key in ("coverage_warn", "coverage_fail"):
            value = gate[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                raise PolicyError("gate.{} must be a number from 0 to 1.".format(key))
        if gate["coverage_fail"] > gate["coverage_warn"]:
            raise PolicyError("gate.coverage_fail must not exceed gate.coverage_warn.")

        return cls(
            id=policy_id,
            version=version,
            domain_weights={str(k): float(v) for k, v in domain_weights.items()},
            severity_penalties={str(k): float(v) for k, v in severity_penalties.items()},
            scope_factors={str(k): float(v) for k, v in scope_factors.items()},
            penalty_cap=float(penalty_cap),
            caps={str(k): float(v) for k, v in caps.items()},
            gate={
                "pass_score": float(pass_score),
                "warn_score": float(warn_score),
                "fail_on_critical": bool(gate["fail_on_critical"]),
                "fail_on_high": bool(gate["fail_on_high"]),
                "coverage_warn": float(gate["coverage_warn"]),
                "coverage_fail": float(gate["coverage_fail"]),
            },
        )

    def weight_for(self, domain: str) -> float:
        return float(self.domain_weights.get(domain, 1.0))

    def penalty_for(self, severity: str) -> float:
        return float(self.severity_penalties.get(severity, 0.0))


DEFAULT_POLICY = Policy()


def load_policy(path: Path) -> Policy:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError("Could not load policy {}: {}".format(path, exc))
    return Policy.from_dict(data)


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
    policy: Any = None,
) -> Confidence:
    limitations = list(limitations or [])
    if policy is None:
        policy = DEFAULT_POLICY
    elif isinstance(policy, dict):
        policy = Policy.from_dict(policy)
    if not isinstance(policy, Policy):
        raise PolicyError("policy must be a Policy or policy dictionary.")

    if not checks:
        return Confidence(
            score=0.0,
            band="none",
            coverage=0.0,
            rationale="No checks were planned, so no release confidence can be claimed.",
            limitations=limitations
            + ["No checks were planned or executed.", "A confidence score is not a probability of zero defects."],
            breakdown={"formula": FORMULA_VERSION, "planned_checks": 0},
            gate={"status": "fail", "reasons": ["No checks were planned."], "policy": policy.id},
        )

    total_weight = sum(max(check.weight, 0.0001) for check in checks)
    executed = [check for check in checks if check.status in (CheckStatus.PASSED.value, CheckStatus.FAILED.value)]
    executed_weight = sum(max(check.weight, 0.0001) for check in executed)
    passed = [check for check in executed if check.status == CheckStatus.PASSED.value]
    passed_weight = sum(max(check.weight, 0.0001) for check in passed)
    coverage_ratio = executed_weight / total_weight if total_weight else 0.0
    quality = (passed_weight / executed_weight * 100.0) if executed_weight else 0.0

    scope = _scope_mode(checks)
    scope_factor = policy.scope_factors.get(scope, 0.75)
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
        if getattr(finding, "suppressed", False):
            continue
        severity = str(finding.severity)
        findings_by_severity[severity] = findings_by_severity.get(severity, 0) + 1
        points = policy.penalty_for(severity)
        if finding.strength == "suspected":
            points *= 0.5
        penalty += points
    penalty = min(penalty, policy.penalty_cap)

    score = base - penalty
    caps: List[Dict[str, Any]] = []
    critical_cap = policy.caps.get("critical", 30.0)
    two_high_cap = policy.caps.get("two_high", 65.0)
    halted_cap = policy.caps.get("halted", 25.0)
    if findings_by_severity.get(Severity.CRITICAL.value, 0) > 0:
        if score > critical_cap:
            caps.append({"reason": "critical finding", "cap": critical_cap})
        score = min(score, critical_cap)
    if findings_by_severity.get(Severity.HIGH.value, 0) >= 2 and score > two_high_cap:
        caps.append({"reason": "two or more high findings", "cap": two_high_cap})
        score = min(score, two_high_cap)
    if halted and score > halted_cap:
        caps.append({"reason": "kill switch halted execution", "cap": halted_cap})
        score = min(score, halted_cap)
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
    if policy.gate["fail_on_critical"] and findings_by_severity.get(Severity.CRITICAL.value, 0):
        gate_reasons.append("One or more critical findings are present.")
    if policy.gate["fail_on_high"] and findings_by_severity.get(Severity.HIGH.value, 0):
        gate_reasons.append("{} high-severity finding(s) remain open.".format(findings_by_severity[Severity.HIGH.value]))
    if not policy.gate["fail_on_high"] and findings_by_severity.get(Severity.HIGH.value, 0):
        gate_reasons.append("{} high-severity finding(s) remain open.".format(findings_by_severity[Severity.HIGH.value]))
    if coverage_ratio < policy.gate["coverage_fail"]:
        gate_reasons.append("Executed coverage is below {:.0f}% of planned weight.".format(policy.gate["coverage_fail"] * 100))
    if score < policy.gate["warn_score"]:
        gate_reasons.append("Confidence is below the advisory release floor of {}.".format(policy.gate["warn_score"]))
    if findings_by_severity.get(Severity.CRITICAL.value, 0) or score < policy.gate["warn_score"] or coverage_ratio < policy.gate["coverage_fail"] or (policy.gate["fail_on_high"] and findings_by_severity.get(Severity.HIGH.value, 0)):
        gate_status = "fail"
    elif score < policy.gate["pass_score"] or findings_by_severity.get(Severity.HIGH.value, 0) or coverage_ratio < policy.gate["coverage_warn"]:
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
            "policy_id": policy.id,
            "policy_version": policy.version,
        },
        gate={"status": gate_status, "reasons": gate_reasons, "policy": policy.id},
    )
