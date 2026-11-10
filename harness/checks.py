"""Stable check catalog and intent-linked planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .errors import HarnessError
from .models import Check, CheckStatus, Risk, Target, utc_now
from .personas import Persona


@dataclass(frozen=True)
class CheckDefinition:
    id: str
    title: str
    domain: str
    persona: str
    kind: str  # static | browser
    intent: str
    expected: str
    base_weight: float
    requires: str  # source | url | markup | manifest | any


CHECK_CATALOG: List[CheckDefinition] = [
    CheckDefinition(
        id="static.inventory",
        title="Source and test inventory is complete and readable",
        domain="functional",
        persona="functional",
        kind="static",
        intent="Know what the release contains before judging it.",
        expected="A readable source tree with an inventory of languages, manifests, tests, and CI.",
        base_weight=1.0,
        requires="source",
    ),
    CheckDefinition(
        id="static.secrets",
        title="No live credentials or private keys are committed",
        domain="security",
        persona="security",
        kind="static",
        intent="Prevent a release from exposing credentials that grant access beyond the product.",
        expected="No private keys, provider tokens, or high-entropy credentials in tracked source.",
        base_weight=2.0,
        requires="source",
    ),
    CheckDefinition(
        id="static.unsafe",
        title="High-risk code patterns are absent or justified",
        domain="security",
        persona="security",
        kind="static",
        intent="Reduce remotely triggerable command execution, injection, and transport failures.",
        expected="No unjustified eval, shell execution, unsafe HTML injection, SQL concatenation, or TLS bypass.",
        base_weight=1.8,
        requires="source",
    ),
    CheckDefinition(
        id="static.tests",
        title="The release has an automated test signal",
        domain="functional",
        persona="functional",
        kind="static",
        intent="Ensure change safety does not depend entirely on manual exploration.",
        expected="At least one automated test file and a discoverable test framework or documented test command.",
        base_weight=1.5,
        requires="source",
    ),
    CheckDefinition(
        id="static.ci",
        title="Continuous integration runs the test signal",
        domain="reliability",
        persona="functional",
        kind="static",
        intent="Keep evidence fresh on every change instead of relying on one local run.",
        expected="A CI configuration that runs tests or the harness on pull requests.",
        base_weight=1.0,
        requires="source",
    ),
    CheckDefinition(
        id="static.dependencies",
        title="Dependencies and environment files are locked and clean",
        domain="data_integrity",
        persona="functional",
        kind="static",
        intent="Prevent unrepeatable builds and accidental environment disclosure.",
        expected="A lockfile for each package ecosystem and no committed local environment file.",
        base_weight=1.1,
        requires="manifest",
    ),
    CheckDefinition(
        id="static.accessibility",
        title="Source markup avoids obvious accessibility barriers",
        domain="accessibility",
        persona="accessibility",
        kind="static",
        intent="Catch barriers before a browser run, especially missing text alternatives and labels.",
        expected="Images have alt text, interactive elements are semantic, and document language is declared.",
        base_weight=1.2,
        requires="markup",
    ),
    CheckDefinition(
        id="browser.load",
        title="The live target loads with a meaningful response",
        domain="functional",
        persona="functional",
        kind="browser",
        intent="Establish that a real customer can reach a rendered product surface.",
        expected="A 2xx/3xx response, a non-empty title, and visible body content.",
        base_weight=1.8,
        requires="url",
    ),
    CheckDefinition(
        id="browser.console",
        title="The rendered page has no unhandled console errors",
        domain="reliability",
        persona="functional",
        kind="browser",
        intent="Detect runtime failures that may be invisible to a happy-path screenshot.",
        expected="No page errors and no error-level console messages during load.",
        base_weight=1.2,
        requires="url",
    ),
    CheckDefinition(
        id="browser.network",
        title="Critical network requests succeed",
        domain="reliability",
        persona="functional",
        kind="browser",
        intent="Find failed APIs and missing assets that break a customer journey.",
        expected="No failed requests and no unexpected 4xx/5xx responses during load.",
        base_weight=1.3,
        requires="url",
    ),
    CheckDefinition(
        id="browser.security",
        title="Transport, headers, cookies, and forms use safe defaults",
        domain="security",
        persona="security",
        kind="browser",
        intent="Reduce session theft, mixed-content injection, and clickjacking exposure.",
        expected="HTTPS, baseline security headers, secure cookies, and no insecure form targets.",
        base_weight=1.8,
        requires="url",
    ),
    CheckDefinition(
        id="browser.accessibility",
        title="Rendered controls expose usable names and structure",
        domain="accessibility",
        persona="accessibility",
        kind="browser",
        intent="Ensure keyboard and assistive-technology users can identify and operate the page.",
        expected="Images have alternatives, form controls have labels, controls have accessible names, and IDs are unique.",
        base_weight=1.4,
        requires="url",
    ),
    CheckDefinition(
        id="browser.content",
        title="The page has a clear title and heading structure",
        domain="content",
        persona="functional",
        kind="browser",
        intent="Give customers and search systems a coherent page identity.",
        expected="One primary heading and a concise, descriptive page title.",
        base_weight=0.8,
        requires="url",
    ),
    CheckDefinition(
        id="browser.links",
        title="Rendered links are safe and actionable",
        domain="functional",
        persona="functional",
        kind="browser",
        intent="Avoid dead ends, insecure destinations, and tabnabbing exposure.",
        expected="No empty or javascript: links, no insecure destinations on HTTPS pages, and noopener on new tabs.",
        base_weight=0.9,
        requires="url",
    ),
]


def catalog_ids(registry: Optional[Any] = None) -> List[str]:
    if registry is not None:
        return list(registry.ids())
    return [definition.id for definition in CHECK_CATALOG]


def _applicable(definition: CheckDefinition, inventory: Dict[str, Any], target: Target, no_browser: bool) -> bool:
    source_available = bool(inventory.get("available"))
    has_markup = bool(inventory.get("has_markup"))
    has_manifest = bool(inventory.get("has_manifest"))
    if definition.requires == "source":
        return source_available
    if definition.requires == "markup":
        return source_available and has_markup
    if definition.requires == "manifest":
        return source_available and has_manifest
    if definition.requires == "url":
        return target.kind == "url" and not no_browser
    return True


def plan_checks(
    target: Target,
    inventory: Dict[str, Any],
    personas: Iterable[Persona],
    max_checks: int,
    no_browser: bool = False,
    registry: Optional[Any] = None,
) -> List[Check]:
    selected = {persona.id for persona in personas}
    definitions = registry.definitions if registry is not None else CHECK_CATALOG
    checks: List[Check] = []
    for definition in definitions:
        if definition.persona not in selected:
            continue
        if not _applicable(definition, inventory, target, no_browser):
            continue
        blocked_reason = (
            registry.blocked_reason(definition.id) if registry is not None else ""
        )
        checks.append(
            Check(
                id=definition.id,
                title=definition.title,
                domain=definition.domain,
                persona=definition.persona,
                kind=definition.kind,
                intent=definition.intent,
                expected=definition.expected,
                base_weight=definition.base_weight,
                weight=definition.base_weight,
                status=(
                    CheckStatus.BLOCKED.value
                    if blocked_reason
                    else CheckStatus.PLANNED.value
                ),
                actual=blocked_reason,
                skip_reason=blocked_reason,
                completed_at=utc_now() if blocked_reason else None,
            )
        )
    if not checks:
        raise HarnessError(
            "No applicable checks. A directory target needs source files; a URL target "
            "needs browser execution enabled. Use --source to combine both."
        )
    if max_checks > 0:
        checks = checks[:max_checks]
    if not checks:
        raise HarnessError("max_checks removed every applicable check")
    return checks


def apply_risk_weights(checks: List[Check], risks: List[Risk]) -> None:
    multipliers: Dict[str, float] = {}
    for risk in risks:
        multipliers[risk.domain] = max(multipliers.get(risk.domain, 1.0), risk.weight_multiplier)
    for check in checks:
        multiplier = multipliers.get(check.domain, 1.0)
        check.weight = round(check.base_weight * multiplier, 4)
