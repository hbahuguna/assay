"""Risk analysis: turn intent, target shape, and inventory into ranked risks."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import Risk, Severity, Target
from .static_analysis import Inventory


PRIORITY_MULTIPLIER = {
    Severity.CRITICAL.value: 1.7,
    Severity.HIGH.value: 1.4,
    Severity.MEDIUM.value: 1.15,
    Severity.LOW.value: 1.0,
    Severity.INFO.value: 0.9,
}

INTENT_RULES = [
    {
        "keywords": ("login", "sign in", "sign-in", "auth", "password", "session", "sso", "identity"),
        "title": "Identity and session boundaries may fail open",
        "domain": "security",
        "priority": Severity.HIGH.value,
        "consequence": "Account takeover, broken authorization, or a session that outlives the customer's intent.",
    },
    {
        "keywords": ("checkout", "payment", "purchase", "billing", "invoice", "subscription", "plan", "usage", "pricing"),
        "title": "Money, entitlements, and usage accounting may be wrong",
        "domain": "security",
        "priority": Severity.HIGH.value,
        "consequence": "Customers can be overcharged, undercharged, or granted entitlements they did not buy.",
    },
    {
        "keywords": ("signup", "onboarding", "self-serve", "self serve", "trial", "upgrade"),
        "title": "The self-serve activation path may lose or mislead a new customer",
        "domain": "functional",
        "priority": Severity.HIGH.value,
        "consequence": "A customer cannot reach first value, or reaches a paid state without understanding the commitment.",
    },
    {
        "keywords": ("accessibility", "a11y", "wcag", "keyboard", "screen reader", "contrast"),
        "title": "Disabled customers may be unable to complete the primary journey",
        "domain": "accessibility",
        "priority": Severity.HIGH.value,
        "consequence": "Keyboard, low-vision, and assistive-technology users are excluded from the product.",
    },
    {
        "keywords": ("upload", "import", "file", "paste", "webhook", "parser"),
        "title": "Untrusted input may cross a parser or storage boundary",
        "domain": "security",
        "priority": Severity.HIGH.value,
        "consequence": "Malicious input can execute code, exhaust resources, or expose another tenant's data.",
    },
    {
        "keywords": ("search", "catalog", "content", "copy", "localization", "locale"),
        "title": "Customers may not find or understand the intended content",
        "domain": "content",
        "priority": Severity.MEDIUM.value,
        "consequence": "The task looks complete but the result is missing, ambiguous, or wrong for the locale.",
    },
    {
        "keywords": ("performance", "latency", "speed", "load", "scale"),
        "title": "User-visible latency may exceed the customer's patience",
        "domain": "performance",
        "priority": Severity.MEDIUM.value,
        "consequence": "Customers abandon the journey or the release collapses under normal peak load.",
    },
    {
        "keywords": ("api", "webhook", "integration", "sync", "export"),
        "title": "Machine-to-machine contracts may drift or fail silently",
        "domain": "data_integrity",
        "priority": Severity.HIGH.value,
        "consequence": "Downstream systems receive partial, duplicated, or malformed data without a visible failure.",
    },
]


class RiskAnalyzer:
    def __init__(
        self,
        target: Target,
        intent: str,
        inventory: Inventory,
        browser_planned: bool,
    ) -> None:
        self.target = target
        self.intent = intent or ""
        self.inventory = inventory
        self.browser_planned = browser_planned

    def analyze(self) -> List[Risk]:
        risks: List[Risk] = []
        intent_lower = self.intent.lower()
        if self.intent:
            risks.append(
                Risk(
                    id="risk-01",
                    title="The stated customer outcome may not be delivered",
                    priority=Severity.HIGH.value,
                    domain="functional",
                    consequence="The release can pass technical checks while failing the job the customer came to do.",
                    evidence=["Intent: {}".format(self.intent)],
                    weight_multiplier=PRIORITY_MULTIPLIER[Severity.HIGH.value],
                    source="intent",
                )
            )
        risks.append(
            Risk(
                id="risk-02",
                title="Evidence scope may be narrower than the release decision",
                priority=Severity.MEDIUM.value,
                domain="reliability",
                consequence="A confident report can hide untested surfaces, making the ship decision look safer than it is.",
                evidence=[
                    "Target kind: {}".format(self.target.kind),
                    "Browser checks planned: {}".format(self.browser_planned),
                    "Source inspection available: {}".format(self.inventory.available),
                ],
                weight_multiplier=PRIORITY_MULTIPLIER[Severity.MEDIUM.value],
                source="scope",
            )
        )
        if self.target.kind == "url":
            risks.append(
                Risk(
                    id="risk-03",
                    title="The live surface may expose transport, session, or runtime defects",
                    priority=Severity.HIGH.value,
                    domain="security",
                    consequence="A visitor can lose a session, trust a tampered response, or see a broken page.",
                    evidence=["Live URL: {}".format(self.target.resolved)],
                    weight_multiplier=PRIORITY_MULTIPLIER[Severity.HIGH.value],
                    source="target",
                )
            )
        elif not self.browser_planned:
            risks.append(
                Risk(
                    id="risk-03",
                    title="Source-only evidence cannot establish rendered behavior",
                    priority=Severity.MEDIUM.value,
                    domain="functional",
                    consequence="A page can compile and still fail for a real customer because Operate coverage is absent.",
                    evidence=["No URL target was supplied."],
                    weight_multiplier=PRIORITY_MULTIPLIER[Severity.MEDIUM.value],
                    source="target",
                )
            )
        for rule in INTENT_RULES:
            if any(keyword in intent_lower for keyword in rule["keywords"]):
                risks.append(
                    Risk(
                        id="",
                        title=rule["title"],
                        priority=rule["priority"],
                        domain=rule["domain"],
                        consequence=rule["consequence"],
                        evidence=["Intent keyword match: {}".format(", ".join(rule["keywords"]))],
                        weight_multiplier=PRIORITY_MULTIPLIER[rule["priority"]],
                        source="intent",
                    )
                )
        if self.inventory.available:
            if not self.inventory.test_files:
                risks.append(
                    Risk(
                        id="",
                        title="No automated regression signal protects this change",
                        priority=Severity.MEDIUM.value,
                        domain="functional",
                        consequence="A regression can ship without a repeatable failing check.",
                        evidence=["0 test/spec files found under the source root."],
                        weight_multiplier=PRIORITY_MULTIPLIER[Severity.MEDIUM.value],
                        source="inventory",
                    )
                )
            if not self.inventory.ci_files:
                risks.append(
                    Risk(
                        id="",
                        title="Test evidence is not refreshed by continuous integration",
                        priority=Severity.LOW.value,
                        domain="reliability",
                        consequence="Local evidence can become stale before the next release.",
                        evidence=["No CI configuration found."],
                        weight_multiplier=PRIORITY_MULTIPLIER[Severity.LOW.value],
                        source="inventory",
                    )
                )
            if self.inventory.env_files:
                risks.append(
                    Risk(
                        id="",
                        title="Committed environment files may expose live configuration",
                        priority=Severity.HIGH.value,
                        domain="security",
                        consequence="Credentials or internal endpoints may be readable by anyone with repository access.",
                        evidence=["Committed env files: {}".format(", ".join(self.inventory.env_files[:5]))],
                        weight_multiplier=PRIORITY_MULTIPLIER[Severity.HIGH.value],
                        source="inventory",
                    )
                )
            if self.inventory.manifests and not self.inventory.lockfiles:
                risks.append(
                    Risk(
                        id="",
                        title="Dependency resolution is not reproducible",
                        priority=Severity.LOW.value,
                        domain="data_integrity",
                        consequence="Two builds can execute different dependency code under the same release label.",
                        evidence=["Manifests without a lockfile."],
                        weight_multiplier=PRIORITY_MULTIPLIER[Severity.LOW.value],
                        source="inventory",
                    )
                )
            if self.inventory.markup_files:
                risks.append(
                    Risk(
                        id="",
                        title="Rendered interfaces may carry accessibility barriers",
                        priority=Severity.MEDIUM.value,
                        domain="accessibility",
                        consequence="Some customers cannot perceive, navigate, or operate the interface.",
                        evidence=["{} markup/component files found.".format(len(self.inventory.markup_files))],
                        weight_multiplier=PRIORITY_MULTIPLIER[Severity.MEDIUM.value],
                        source="inventory",
                    )
                )
        return self._dedupe_and_number(risks)

    @staticmethod
    def _dedupe_and_number(risks: List[Risk]) -> List[Risk]:
        seen = set()
        unique: List[Risk] = []
        for risk in risks:
            key = re.sub(r"\W+", " ", risk.title.lower()).strip()
            if key in seen:
                continue
            seen.add(key)
            unique.append(risk)
        unique = unique[:12]
        for index, risk in enumerate(unique, start=1):
            risk.id = "risk-{:02d}".format(index)
        return unique
