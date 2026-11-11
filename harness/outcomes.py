"""Shared execution result types for static and browser checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import CheckStatus, Severity, Strength


@dataclass
class FindingDraft:
    title: str
    severity: str
    domain: str
    strength: str
    consequence: str
    location: str = ""
    reproduction: str = ""
    evidence: List[str] = field(default_factory=list)
    remediation: str = ""
    verification: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class BlockerDraft:
    attempted_action: str
    boundary: str
    unblock_evidence: List[str] = field(default_factory=list)
    covered_elsewhere: bool = False
    covered_by: List[str] = field(default_factory=list)


@dataclass
class CheckOutcome:
    status: str = CheckStatus.PASSED.value
    actual: str = ""
    evidence: List[str] = field(default_factory=list)
    findings: List[FindingDraft] = field(default_factory=list)
    blocker: Optional[BlockerDraft] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_blocker(
        self,
        attempted_action: str,
        boundary: str,
        unblock_evidence: List[str] = None,
        covered_elsewhere: bool = False,
        covered_by: List[str] = None,
    ) -> None:
        self.blocker = BlockerDraft(
            attempted_action=attempted_action,
            boundary=boundary,
            unblock_evidence=list(unblock_evidence or []),
            covered_elsewhere=covered_elsewhere,
            covered_by=list(covered_by or []),
        )
        self.findings = []
        self.status = CheckStatus.BLOCKED.value

    def add_finding(
        self,
        title: str,
        severity: str,
        domain: str,
        consequence: str,
        location: str = "",
        reproduction: str = "",
        strength: str = Strength.SOURCE_PROVEN.value,
        remediation: str = "",
        verification: str = "",
        tags: List[str] = None,
    ) -> None:
        self.findings.append(
            FindingDraft(
                title=title,
                severity=severity,
                domain=domain,
                strength=strength,
                consequence=consequence,
                location=location,
                reproduction=reproduction,
                evidence=list(self.evidence),
                remediation=remediation,
                verification=verification,
                tags=list(tags or []),
            )
        )
        self.status = CheckStatus.FAILED.value


def clamp_severity(value: str) -> str:
    allowed = {item.value for item in Severity}
    value = (value or "").lower()
    return value if value in allowed else Severity.MEDIUM.value
