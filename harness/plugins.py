"""Signed check-plugin registry and execution context.

Built-in checks and installed plugins are registered through the same
``CheckRegistry``.  Installed plugins are discovered from the
``quality_harness.checks`` entry-point group and must carry an HMAC-SHA256
signature over their declarative metadata, evaluator entry point, and the
canonical definition.  The trust store lives outside the OSS source tree at
``.harness/plugins/trusted_keys.json``.

A plugin that cannot be imported, validated, owned by a persona, or verified is
not allowed to fail the run.  It becomes a blocked check with an actionable
limitation instead, so the coverage gap is visible in the report.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .checks import CHECK_CATALOG, CheckDefinition
from .errors import PersonaError, PluginError
from .evidence import EvidenceStore
from .models import Check, CheckStatus, Target
from .outcomes import BlockerDraft, CheckOutcome, FindingDraft
from .personas import Persona, check_ownership, ownership_error_message
from .static_analysis import Inventory

ENTRY_POINT_GROUP = "quality_harness.checks"
SIGNATURE_SCHEME = "hmac-sha256"
TRUST_FILE = Path(".harness") / "plugins" / "trusted_keys.json"
PLUGIN_KINDS = {"static", "plugin"}
KNOWN_INPUT_REQUIREMENTS = {"source", "url", "markup", "manifest", "any"}
CHECK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass
class PluginContext:
    """The read-only execution surface exposed to a plugin evaluator."""

    check: Check
    target: Target
    root: Optional[Path]
    inventory: Inventory
    evidence: EvidenceStore
    log: Callable[[str], None]

    def save_json(
        self,
        name: str,
        payload: Any,
        description: str = "",
        kind: str = "plugin",
    ) -> str:
        return self.evidence.save_json(
            name,
            payload,
            kind=kind,
            description=description,
            check_id=self.check.id,
        )


@dataclass
class CheckPlugin:
    """The object an installed entry point must return."""

    definition: CheckDefinition
    evaluate: Callable[[PluginContext], CheckOutcome]
    signature: str = ""


class PluginCheckRunner:
    """Execute a plugin evaluator with a bounded, evidence-backed context."""

    def __init__(
        self,
        target: Target,
        root: Optional[Path],
        inventory: Inventory,
        evidence: EvidenceStore,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.target = target
        self.root = root
        self.inventory = inventory
        self.evidence = evidence
        self.log = log or (lambda message: None)

    def run(
        self, check: Check, evaluator: Callable[[PluginContext], CheckOutcome]
    ) -> CheckOutcome:
        context = PluginContext(
            check=check,
            target=self.target,
            root=self.root,
            inventory=self.inventory,
            evidence=self.evidence,
            log=self.log,
        )
        try:
            outcome = evaluator(context)
        except Exception as exc:  # A broken plugin must not kill the run.
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Plugin check crashed: {}: {}".format(type(exc).__name__, exc),
                metadata={"error": str(exc)},
            )
        if not isinstance(outcome, CheckOutcome):
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Plugin did not return a CheckOutcome (got {}).".format(
                    type(outcome).__name__
                ),
            )
        if outcome.status not in {status.value for status in CheckStatus}:
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Plugin returned an unknown check status: {!r}.".format(outcome.status),
            )
        if not isinstance(outcome.findings, list) or any(
            not isinstance(draft, FindingDraft) for draft in outcome.findings
        ):
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Plugin findings must be FindingDraft instances.",
            )
        if outcome.blocker is not None and not isinstance(outcome.blocker, BlockerDraft):
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Plugin blocker must be a BlockerDraft instance.",
            )
        invalid = self._invalid_evidence(outcome.evidence)
        if invalid:
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Plugin returned evidence outside this run's evidence directory.",
                metadata={"limitation": "Invalid plugin evidence paths: {}".format(", ".join(invalid[:5]))},
            )
        if outcome.findings:
            allowed = set(outcome.evidence)
            for draft in outcome.findings:
                draft_evidence = list(draft.evidence or [])
                if any(path not in allowed for path in draft_evidence):
                    return CheckOutcome(
                        status=CheckStatus.ERROR.value,
                        actual="Plugin finding evidence must be included in the check evidence.",
                    )
            if not outcome.evidence and any(not draft.evidence for draft in outcome.findings):
                return CheckOutcome(
                    status=CheckStatus.ERROR.value,
                    actual="Plugin findings require evidence before they can be recorded.",
                )
        return outcome

    def _invalid_evidence(self, paths: Optional[Sequence[str]]) -> List[str]:
        invalid: List[str] = []
        run_dir = self.evidence.run_dir.resolve()
        for raw in paths or []:
            if not isinstance(raw, str) or not raw:
                invalid.append(str(raw))
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = self.evidence.root / candidate
            try:
                candidate = candidate.resolve()
                candidate.relative_to(run_dir)
            except (OSError, ValueError):
                invalid.append(str(raw))
                continue
            if not candidate.is_file():
                invalid.append(str(raw))
        return invalid


def _slug(value: str, fallback: str = "plugin") -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or fallback


def load_trust_keys(
    path: Path, log: Optional[Callable[[str], None]] = None
) -> Dict[str, str]:
    """Load key id -> shared secret pairs.

    The file is deliberately outside the installed package.  A missing file is
    not an error; it simply means no plugin can be trusted yet.
    """

    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if log:
            log("WARNING: could not read plugin trust store {}: {}".format(path, exc))
        return {}
    keys = payload.get("keys", payload) if isinstance(payload, dict) else {}
    if not isinstance(keys, dict):
        if log:
            log("WARNING: plugin trust store {} needs a 'keys' object.".format(path))
        return {}
    return {
        str(key_id): str(secret)
        for key_id, secret in keys.items()
        if isinstance(secret, str) and secret
    }


def signature_payload(definition: CheckDefinition, entry_point: str) -> bytes:
    payload = {
        "schema_version": "quality-harness.check-plugin/v1",
        "entry_point": entry_point,
        "id": definition.id,
        "title": definition.title,
        "domain": definition.domain,
        # Legacy metadata retained in the v1 signature so existing signed
        # plugins keep verifying; ownership itself comes from personas.
        "persona": definition.persona,
        "kind": definition.kind,
        "intent": definition.intent,
        "expected": definition.expected,
        "base_weight": definition.base_weight,
        "requires": definition.requires,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sign_plugin(
    definition: CheckDefinition, entry_point: str, key_id: str, secret: str
) -> str:
    """Return the detached signature string a plugin should expose."""

    digest = hmac.new(
        secret.encode("utf-8"),
        signature_payload(definition, entry_point),
        hashlib.sha256,
    ).hexdigest()
    return "{}:{}:{}".format(SIGNATURE_SCHEME, key_id, digest)


def _parse_signature(signature: str) -> Tuple[str, str]:
    parts = signature.split(":", 2)
    if len(parts) != 3 or parts[0] != SIGNATURE_SCHEME or not parts[1] or not parts[2]:
        raise PluginError(
            "Plugin signature must look like '{}:<key_id>:<hex_digest>'.".format(
                SIGNATURE_SCHEME
            )
        )
    return parts[1], parts[2]


def load_entry_points(group: str = ENTRY_POINT_GROUP) -> List[Any]:
    """Load installed entry points across Python 3.8+ metadata APIs."""

    discovered = importlib_metadata.entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=group))
    return list(discovered.get(group, []))  # type: ignore[union-attr]


def _plugin_from_loaded(loaded: Any) -> CheckPlugin:
    if isinstance(loaded, CheckPlugin):
        return loaded
    if isinstance(loaded, dict):
        definition = loaded.get("definition")
        if isinstance(definition, dict):
            try:
                definition = CheckDefinition(**definition)
            except TypeError as exc:
                raise PluginError("Plugin definition has unknown fields: {}".format(exc))
        evaluate = loaded.get("evaluate")
        signature = str(loaded.get("signature", ""))
        if not isinstance(definition, CheckDefinition):
            raise PluginError("Plugin dictionary needs a CheckDefinition under 'definition'.")
        return CheckPlugin(definition=definition, evaluate=evaluate, signature=signature)
    definition = getattr(loaded, "definition", None)
    evaluate = getattr(loaded, "evaluate", None)
    signature = str(getattr(loaded, "signature", ""))
    if isinstance(definition, CheckDefinition) and callable(evaluate):
        return CheckPlugin(definition=definition, evaluate=evaluate, signature=signature)
    raise PluginError(
        "Entry point must load a CheckPlugin (or an object with definition, evaluate, and signature)."
    )


class CheckRegistry:
    """A deterministic registry shared by built-in checks and plugins."""

    def __init__(
        self,
        trust_keys: Optional[Dict[str, str]] = None,
        trust_file: Optional[Path] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.trust_keys = dict(trust_keys or {})
        self.trust_file = Path(trust_file) if trust_file else TRUST_FILE
        self.log_fn = log or (lambda message: None)
        self._definitions: Dict[str, CheckDefinition] = {}
        self._evaluators: Dict[str, Callable[[PluginContext], CheckOutcome]] = {}
        self._sources: Dict[str, str] = {}
        self._blocked: Dict[str, str] = {}
        self._owner_hints: Dict[str, str] = {}

    @property
    def definitions(self) -> List[CheckDefinition]:
        return list(self._definitions.values())

    def ids(self) -> List[str]:
        return list(self._definitions.keys())

    def get(self, check_id: str) -> Optional[CheckDefinition]:
        return self._definitions.get(check_id)

    def evaluator(self, check_id: str) -> Optional[Callable[[PluginContext], CheckOutcome]]:
        return self._evaluators.get(check_id)

    def source(self, check_id: str) -> str:
        return self._sources.get(check_id, "")

    def blocked_reason(self, check_id: str) -> str:
        return self._blocked.get(check_id, "")

    def owner_hint(self, check_id: str) -> str:
        """Return the persona hint used to place a blocked check in a plan."""

        return self._owner_hints.get(check_id, "")

    def block(self, check_id: str, reason: str) -> None:
        """Mark a registered check blocked without changing its identity."""

        if check_id not in self._definitions or check_id in self._blocked:
            return
        self._blocked[check_id] = reason
        self.log_fn("WARNING: check '{}' blocked: {}".format(check_id, reason))

    def builtin_count(self) -> int:
        return sum(
            1
            for check_id, source in self._sources.items()
            if source == "builtin" and check_id not in self._blocked
        )

    def plugin_count(self) -> int:
        return sum(
            1
            for check_id, source in self._sources.items()
            if source.startswith("plugin:") and check_id not in self._blocked
        )

    def blocked_count(self) -> int:
        return len(self._blocked)

    def has_plugin_failures(self) -> bool:
        return any(source == "plugin-blocked" for source in self._sources.values())

    def register_builtins(self) -> None:
        for definition in CHECK_CATALOG:
            self.register(definition, source="builtin", trusted=True)

    def register(
        self,
        definition: CheckDefinition,
        evaluator: Optional[Callable[[PluginContext], CheckOutcome]] = None,
        source: str = "builtin",
        signature: str = "",
        entry_point: str = "",
        trusted: bool = False,
    ) -> None:
        self._validate_definition(definition)
        if definition.id in self._definitions:
            raise PluginError(
                "Duplicate check id '{}' from {}; already registered by {}.".format(
                    definition.id, source, self._sources.get(definition.id, "unknown")
                )
            )
        if not trusted:
            self._verify_signature(definition, entry_point, signature)
        if evaluator is not None and not callable(evaluator):
            raise PluginError("Plugin evaluator for '{}' is not callable.".format(definition.id))
        self._definitions[definition.id] = definition
        self._sources[definition.id] = source
        if evaluator is not None:
            self._evaluators[definition.id] = evaluator

    def load_entry_point(self, entry_point: Any) -> None:
        name = str(getattr(entry_point, "name", "unnamed"))
        value = str(getattr(entry_point, "value", ""))
        plugin: Optional[CheckPlugin] = None
        try:
            loaded = entry_point.load()
        except Exception as exc:
            self.block_failure(
                name,
                "Plugin '{}' could not be imported: {}: {}".format(name, type(exc).__name__, exc),
            )
            return
        try:
            plugin = _plugin_from_loaded(loaded)
            if not callable(plugin.evaluate):
                raise PluginError("Plugin evaluator is not callable.")
            if plugin.definition.kind not in PLUGIN_KINDS:
                raise PluginError(
                    "Browser plugin kind is not supported yet; use 'static' or 'plugin'."
                )
            self.register(
                plugin.definition,
                evaluator=plugin.evaluate,
                source="plugin:{}".format(value or name),
                signature=plugin.signature,
                entry_point=value,
                trusted=False,
            )
        except PluginError as exc:
            self.block_plugin(name, plugin.definition if plugin is not None else None, str(exc))
        except Exception as exc:
            self.block_failure(
                name,
                "Plugin '{}' could not be loaded: {}: {}".format(name, type(exc).__name__, exc),
            )

    def block_plugin(
        self, label: str, definition: Optional[CheckDefinition], reason: str
    ) -> None:
        """Register a blocked check for a rejected plugin.

        If the plugin definition itself is structurally valid and its id is free,
        keep the intended id so operators can see which plugin is blocked.
        Otherwise use a synthetic id that cannot shadow a real check.
        """

        if definition is not None:
            try:
                self._validate_definition(definition)
            except PluginError:
                definition = None
            if definition is not None and definition.id not in self._definitions:
                self._definitions[definition.id] = definition
                self._sources[definition.id] = "plugin-blocked"
                self._blocked[definition.id] = reason
                self._owner_hints[definition.id] = definition.persona or "functional"
                self.log_fn("WARNING: plugin check '{}' blocked: {}".format(definition.id, reason))
                return
        self.block_failure(
            label,
            reason,
            owner_hint=(
                definition.persona
                if definition and definition.persona
                else "functional"
            ),
        )

    def block_failure(
        self, label: str, reason: str, owner_hint: str = "functional"
    ) -> str:
        base = "plugin.load.{}".format(_slug(label))
        check_id = base
        suffix = 2
        while check_id in self._definitions:
            check_id = "{}-{}".format(base, suffix)
            suffix += 1
        definition = CheckDefinition(
            id=check_id,
            title="Plugin '{}' could not be loaded".format(label),
            domain="functional",
            kind="plugin",
            intent="Keep a plugin configuration failure visible as a coverage gap.",
            expected="The plugin loads, validates, and is owned by exactly one persona.",
            base_weight=1.0,
            requires="any",
        )
        self._definitions[check_id] = definition
        self._sources[check_id] = "plugin-blocked"
        self._blocked[check_id] = reason
        self._owner_hints[check_id] = owner_hint or "functional"
        self.log_fn("WARNING: plugin '{}' blocked: {}".format(label, reason))
        return check_id

    def block_missing_plugin_check(
        self, check_id: str, persona_id: str, domain: str, reason: str
    ) -> None:
        """Block a persona-owned check whose plugin could not be loaded.

        Import failures do not expose the plugin's declared id. The persona
        contract is the only place that id is known, so the orchestrator adds a
        blocked placeholder for it after loading personas.
        """

        if check_id in self._definitions:
            return
        definition = CheckDefinition(
            id=check_id,
            title="Plugin check '{}' could not be loaded".format(check_id),
            domain=domain or "functional",
            kind="plugin",
            intent="Keep a plugin load failure visible as a coverage gap.",
            expected="The plugin loads, validates, signs, and is owned by exactly one persona.",
            base_weight=1.0,
            requires="any",
        )
        self._definitions[check_id] = definition
        self._sources[check_id] = "plugin-blocked"
        self._blocked[check_id] = reason
        self._owner_hints[check_id] = persona_id
        self.log_fn("WARNING: plugin check '{}' blocked: {}".format(check_id, reason))

    def validate_ownership(self, personas: Iterable[Persona]) -> None:
        """Resolve every check to exactly one persona owner.

        Built-in checks are a configuration contract: an unowned or multiply
        owned check fails validation before execution. Plugin ownership
        failures are coverage gaps and become blocked checks instead.
        """

        persona_list = list(personas)
        ownership = check_ownership(persona_list)
        for check_id in list(self._definitions):
            owners = ownership.get(check_id, [])
            source = self._sources.get(check_id, "")
            is_plugin = source.startswith("plugin")
            if len(owners) == 1:
                if check_id in self._blocked and not self._owner_hints.get(check_id):
                    self._owner_hints[check_id] = owners[0]
                continue
            reason = ownership_error_message(check_id, owners)
            if not is_plugin:
                raise PersonaError(reason)
            if check_id in self._blocked:
                continue
            self.block(check_id, reason)

    def _validate_definition(self, definition: CheckDefinition) -> None:
        if not isinstance(definition, CheckDefinition):
            raise PluginError("Plugin definition must be a CheckDefinition.")
        if not definition.id or not CHECK_ID_PATTERN.match(definition.id):
            raise PluginError(
                "Check id {!r} must start with a lower-case letter and use only lower-case "
                "letters, digits, '.', '_', or '-'.".format(definition.id)
            )
        if not definition.title.strip():
            raise PluginError("Check '{}' needs a non-empty title.".format(definition.id))
        if not definition.domain.strip():
            raise PluginError("Check '{}' needs a non-empty domain.".format(definition.id))
        if definition.kind not in {"static", "browser", "plugin"}:
            raise PluginError(
                "Check '{}' has unknown kind '{}'; use static, browser, or plugin.".format(
                    definition.id, definition.kind
                )
            )
        if definition.requires not in KNOWN_INPUT_REQUIREMENTS:
            raise PluginError(
                "Check '{}' has unknown input requirement '{}'; use {}.".format(
                    definition.id,
                    definition.requires,
                    ", ".join(sorted(KNOWN_INPUT_REQUIREMENTS)),
                )
            )
        if isinstance(definition.base_weight, bool) or not isinstance(
            definition.base_weight, (int, float)
        ):
            raise PluginError("Check '{}' base_weight must be a number.".format(definition.id))
        weight = float(definition.base_weight)
        if not math.isfinite(weight) or weight <= 0:
            raise PluginError(
                "Check '{}' base_weight must be greater than zero.".format(definition.id)
            )

    def _verify_signature(
        self, definition: CheckDefinition, entry_point: str, signature: str
    ) -> None:
        if not signature:
            raise PluginError(
                "Plugin '{}' is unsigned. Sign its entry point with a key from {}.".format(
                    definition.id, self.trust_file
                )
            )
        key_id, provided = _parse_signature(signature)
        secret = self.trust_keys.get(key_id)
        if not secret:
            raise PluginError(
                "Plugin '{}' references unknown signing key '{}'. Add it to {}.".format(
                    definition.id, key_id, self.trust_file
                )
            )
        expected = hmac.new(
            secret.encode("utf-8"),
            signature_payload(definition, entry_point),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(provided, expected):
            raise PluginError(
                "Plugin '{}' signature did not verify against key '{}'.".format(
                    definition.id, key_id
                )
            )


def build_registry(
    root: Path,
    entry_points: Optional[Iterable[Any]] = None,
    log: Optional[Callable[[str], None]] = None,
) -> CheckRegistry:
    """Create the built-in registry and discover installed signed plugins."""

    root = Path(root).resolve()
    trust_file = root / TRUST_FILE
    registry = CheckRegistry(
        trust_keys=load_trust_keys(trust_file, log=log),
        trust_file=trust_file,
        log=log,
    )
    registry.register_builtins()
    try:
        discovered = list(entry_points) if entry_points is not None else load_entry_points()
    except Exception as exc:
        registry.block_failure(
            "entry-point discovery",
            "Installed plugin discovery failed: {}: {}".format(type(exc).__name__, exc),
        )
        return registry
    for entry_point in sorted(discovered, key=lambda item: str(getattr(item, "name", ""))):
        registry.load_entry_point(entry_point)
    return registry
