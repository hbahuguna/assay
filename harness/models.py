"""Core data model for the quality-harness MVP.

The model is deliberately boring: dataclasses plus string enums, all of which
serialise to plain JSON.  A coding agent should be able to read a progress file
or a report without importing the harness.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import uuid
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with a trailing Z."""

    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def new_id(prefix: str) -> str:
    return "{}-{}".format(prefix, uuid.uuid4().hex[:10])


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    HALTED = "halted"
    FAILED = "failed"


class PhaseName(str, enum.Enum):
    RISK_ANALYSIS = "risk_analysis"
    TEST_PLANNING = "test_planning"
    EXECUTION = "execution"
    EVIDENCE_COLLECTION = "evidence_collection"
    CONFIDENCE_SCORING = "confidence_scoring"


class PhaseStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    HALTED = "halted"
    FAILED = "failed"


class CheckStatus(str, enum.Enum):
    PLANNED = "planned"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    ERROR = "error"


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Strength(str, enum.Enum):
    OBSERVED = "observed"
    SOURCE_PROVEN = "source_proven"
    SUSPECTED = "suspected"


SEVERITY_RANK = {
    Severity.CRITICAL.value: 5,
    Severity.HIGH.value: 4,
    Severity.MEDIUM.value: 3,
    Severity.LOW.value: 2,
    Severity.INFO.value: 1,
}


def severity_rank(value: str) -> int:
    return SEVERITY_RANK.get(getattr(value, "value", value), 0)


@dataclasses.dataclass
class Target:
    raw: str
    kind: str  # "url" | "directory" | "file"
    resolved: str
    source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Target":
        return cls(
            raw=data.get("raw", ""),
            kind=data.get("kind", "directory"),
            resolved=data.get("resolved", ""),
            source=data.get("source"),
        )


@dataclasses.dataclass
class Risk:
    id: str
    title: str
    priority: str  # critical | high | medium | low
    domain: str
    consequence: str
    evidence: List[str] = dataclasses.field(default_factory=list)
    weight_multiplier: float = 1.0
    source: str = "analysis"
    status: str = "open"

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Risk":
        return cls(
            id=data["id"],
            title=data["title"],
            priority=data.get("priority", "medium"),
            domain=data.get("domain", "functional"),
            consequence=data.get("consequence", ""),
            evidence=list(data.get("evidence", [])),
            weight_multiplier=float(data.get("weight_multiplier", 1.0)),
            source=data.get("source", "analysis"),
            status=data.get("status", "open"),
        )


@dataclasses.dataclass
class Finding:
    id: str
    title: str
    severity: str
    domain: str
    persona: str
    strength: str
    consequence: str
    location: str = ""
    reproduction: str = ""
    evidence: List[str] = dataclasses.field(default_factory=list)
    remediation: str = ""
    verification: str = ""
    check_id: str = ""
    created_at: str = dataclasses.field(default_factory=utc_now)
    tags: List[str] = dataclasses.field(default_factory=list)
    fingerprint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Finding":
        return cls(
            id=data["id"],
            title=data["title"],
            severity=data.get("severity", "medium"),
            domain=data.get("domain", "functional"),
            persona=data.get("persona", "functional"),
            strength=data.get("strength", "suspected"),
            consequence=data.get("consequence", ""),
            location=data.get("location", ""),
            reproduction=data.get("reproduction", ""),
            evidence=list(data.get("evidence", [])),
            remediation=data.get("remediation", ""),
            verification=data.get("verification", ""),
            check_id=data.get("check_id", ""),
            created_at=data.get("created_at", utc_now()),
            tags=list(data.get("tags", [])),
            fingerprint=data.get("fingerprint", ""),
        )


@dataclasses.dataclass
class Check:
    id: str
    title: str
    domain: str
    persona: str
    kind: str  # "static" | "browser"
    intent: str
    expected: str
    base_weight: float
    status: str = CheckStatus.PLANNED.value
    weight: float = 1.0
    actual: str = ""
    evidence: List[str] = dataclasses.field(default_factory=list)
    finding_ids: List[str] = dataclasses.field(default_factory=list)
    skip_reason: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Check":
        return cls(
            id=data["id"],
            title=data["title"],
            domain=data.get("domain", "functional"),
            persona=data.get("persona", "functional"),
            kind=data.get("kind", "static"),
            intent=data.get("intent", ""),
            expected=data.get("expected", ""),
            base_weight=float(data.get("base_weight", 1.0)),
            status=data.get("status", CheckStatus.PLANNED.value),
            weight=float(data.get("weight", data.get("base_weight", 1.0))),
            actual=data.get("actual", ""),
            evidence=list(data.get("evidence", [])),
            finding_ids=list(data.get("finding_ids", [])),
            skip_reason=data.get("skip_reason", ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclasses.dataclass
class Phase:
    name: str
    title: str
    status: str = PhaseStatus.PENDING.value
    summary: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Phase":
        return cls(
            name=data["name"],
            title=data.get("title", data["name"]),
            status=data.get("status", PhaseStatus.PENDING.value),
            summary=data.get("summary", ""),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            details=dict(data.get("details", {})),
        )


@dataclasses.dataclass
class Confidence:
    score: float = 0.0
    band: str = "unscored"
    coverage: float = 0.0
    rationale: str = ""
    limitations: List[str] = dataclasses.field(default_factory=list)
    penalties: Dict[str, float] = dataclasses.field(default_factory=dict)
    domain_scores: Dict[str, Any] = dataclasses.field(default_factory=dict)
    breakdown: Dict[str, Any] = dataclasses.field(default_factory=dict)
    gate: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Confidence":
        data = data or {}
        return cls(
            score=float(data.get("score", 0.0)),
            band=data.get("band", "unscored"),
            coverage=float(data.get("coverage", 0.0)),
            rationale=data.get("rationale", ""),
            limitations=list(data.get("limitations", [])),
            penalties=dict(data.get("penalties", {})),
            domain_scores=dict(data.get("domain_scores", {})),
            breakdown=dict(data.get("breakdown", {})),
            gate=dict(data.get("gate", {})),
        )


@dataclasses.dataclass
class KillSwitch:
    armed: bool = True
    threshold: str = "critical"
    triggered: bool = False
    reason: str = ""
    triggered_at: Optional[str] = None
    finding_id: str = ""
    manual: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "KillSwitch":
        data = data or {}
        return cls(
            armed=bool(data.get("armed", True)),
            threshold=data.get("threshold", "critical"),
            triggered=bool(data.get("triggered", False)),
            reason=data.get("reason", ""),
            triggered_at=data.get("triggered_at"),
            finding_id=data.get("finding_id", ""),
            manual=bool(data.get("manual", False)),
        )


@dataclasses.dataclass
class RunState:
    run_id: str
    workflow_id: str
    target: Target
    intent: str
    workflow_version: str = ""
    status: str = RunStatus.RUNNING.value
    current_phase: str = PhaseName.RISK_ANALYSIS.value
    phases: List[Phase] = dataclasses.field(default_factory=list)
    risks: List[Risk] = dataclasses.field(default_factory=list)
    checks: List[Check] = dataclasses.field(default_factory=list)
    findings: List[Finding] = dataclasses.field(default_factory=list)
    confidence: Confidence = dataclasses.field(default_factory=Confidence)
    kill_switch: KillSwitch = dataclasses.field(default_factory=KillSwitch)
    inventory: Dict[str, Any] = dataclasses.field(default_factory=dict)
    personas: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    options: Dict[str, Any] = dataclasses.field(default_factory=dict)
    evidence_dir: str = ""
    started_at: str = dataclasses.field(default_factory=utc_now)
    updated_at: str = dataclasses.field(default_factory=utc_now)
    completed_at: Optional[str] = None
    halted_reason: str = ""
    error: str = ""
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "target": self.target.to_dict(),
            "intent": self.intent,
            "status": self.status,
            "current_phase": self.current_phase,
            "phases": [p.to_dict() for p in self.phases],
            "risks": [r.to_dict() for r in self.risks],
            "checks": [c.to_dict() for c in self.checks],
            "findings": [f.to_dict() for f in self.findings],
            "confidence": self.confidence.to_dict(),
            "kill_switch": self.kill_switch.to_dict(),
            "inventory": self.inventory,
            "personas": self.personas,
            "options": self.options,
            "evidence_dir": self.evidence_dir,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "halted_reason": self.halted_reason,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunState":
        return cls(
            run_id=data["run_id"],
            workflow_id=data.get("workflow_id", "release-confidence"),
            workflow_version=data.get("workflow_version", ""),
            target=Target.from_dict(data.get("target", {})),
            intent=data.get("intent", ""),
            status=data.get("status", RunStatus.RUNNING.value),
            current_phase=data.get("current_phase", PhaseName.RISK_ANALYSIS.value),
            phases=[Phase.from_dict(p) for p in data.get("phases", [])],
            risks=[Risk.from_dict(r) for r in data.get("risks", [])],
            checks=[Check.from_dict(c) for c in data.get("checks", [])],
            findings=[Finding.from_dict(f) for f in data.get("findings", [])],
            confidence=Confidence.from_dict(data.get("confidence")),
            kill_switch=KillSwitch.from_dict(data.get("kill_switch")),
            inventory=dict(data.get("inventory", {})),
            personas=list(data.get("personas", [])),
            options=dict(data.get("options", {})),
            evidence_dir=data.get("evidence_dir", ""),
            started_at=data.get("started_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            completed_at=data.get("completed_at"),
            halted_reason=data.get("halted_reason", ""),
            error=data.get("error", ""),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    def phase(self, name: str) -> Phase:
        for phase in self.phases:
            if phase.name == name:
                return phase
        raise KeyError("Unknown phase: {}".format(name))

    def check(self, check_id: str) -> Check:
        for check in self.checks:
            if check.id == check_id:
                return check
        raise KeyError("Unknown check: {}".format(check_id))

    def finding(self, finding_id: str) -> Finding:
        for finding in self.findings:
            if finding.id == finding_id:
                return finding
        raise KeyError("Unknown finding: {}".format(finding_id))

    def touch(self) -> None:
        self.updated_at = utc_now()
