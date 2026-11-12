"""Deterministic five-phase orchestrator.

Risk Analysis -> Test Planning -> Execution -> Evidence Collection -> Confidence Scoring

The orchestrator owns state transitions, persistence, persona loading, the kill
switch, and the final report.  Check implementation lives in static_analysis.py
and browser.py so this module stays readable.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .browser import BrowserCheckRunner, BrowserOptions, redact_text
from .checks import apply_risk_weights, plan_checks
from .determinism import (
    Determinism,
    format_timestamp,
    make_determinism,
    reset_determinism,
    set_determinism,
)
from .errors import HarnessError, PersonaError, StateError, TargetError
from .evidence import EvidenceStore
from .models import (
    Blocker,
    Check,
    CheckStatus,
    Finding,
    InvalidFinding,
    KillSwitch,
    Phase,
    PhaseName,
    PhaseStatus,
    RunState,
    RunStatus,
    Severity,
    Target,
    severity_rank,
    new_id,
    utc_now,
)
from .outcomes import BlockerDraft, CheckOutcome
from .personas import (
    Persona,
    load_personas,
    select_personas,
    unknown_persona_check_ids,
    validate_blocker,
    validate_payload,
    validate_persona_checks,
)
from .plugins import CheckRegistry, PluginCheckRunner, build_registry
from .report import build_report
from .risk import RiskAnalyzer
from .scoring import Policy, load_policy, score_confidence
from .static_analysis import Inventory, SourceInspector, StaticCheckRunner
from .workflows import Workflow, get_workflow


_SENSITIVE_KEY = re.compile(
    r"(?i)(authorization|cookie|credential|password|passwd|private[_-]?key|"
    r"secret|session|token|api[_-]?key)"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


def _redact_string(value: str) -> str:
    text = _PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", str(value))
    return redact_text(text)


def _redact_payload(value: Any, key: str = "") -> Any:
    """Recursively redact a payload before it can reach state or the report."""

    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_payload(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return _redact_string(repr(value))


def _draft_payload(draft: Any) -> Dict[str, Any]:
    """Return the raw finding payload without deriving missing fields."""

    if isinstance(draft, Mapping):
        return dict(draft)
    if is_dataclass(draft) and not isinstance(draft, type):
        payload = {}
        for definition in fields(draft):
            try:
                payload[definition.name] = getattr(draft, definition.name)
            except AttributeError:
                # A malformed object is itself a contract violation; preserve
                # the absence instead of crashing the run.
                continue
        return payload
    to_dict = getattr(draft, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    if hasattr(draft, "__dict__"):
        return dict(vars(draft))
    return {"value": draft}


@dataclass
class RunOptions:
    target: str
    source: Optional[str] = None
    intent: str = "Assess release readiness of the target."
    workflow_id: str = "release-confidence"
    personas_dir: str = "personas"
    selected_personas: List[str] = field(default_factory=list)
    max_checks: int = 20
    no_browser: bool = False
    headless: bool = True
    timeout_ms: int = 30000
    viewport: str = "1440x900"
    kill_on: str = "critical"
    mobile: bool = False
    mobile_viewport: str = "390x844"
    browser_har: bool = False
    browser_trace: bool = False
    browser_video: bool = False
    storage_state: Optional[str] = None
    policy: Optional[str] = None
    require_personas: int = 3
    verbose: bool = False
    seed: Optional[int] = None
    clock_mode: str = "real"
    frozen_clock: object = None
    generated_at: object = None


class Orchestrator:
    def __init__(self, root: Path, options: RunOptions, log=None) -> None:
        self.root = Path(root).resolve()
        self.options = options
        from .state import StateStore, SuppressionStore

        self.state_store = StateStore(self.root)
        self.suppression_store = SuppressionStore(self.root)
        self.log_fn = log
        self.state: Optional[RunState] = None
        self.evidence: Optional[EvidenceStore] = None
        self.personas: List[Persona] = []
        self.all_personas: List[Persona] = []
        self.persona_map: Dict[str, Persona] = {}
        self.registry: Optional[CheckRegistry] = None
        self.workflow: Optional[Workflow] = None
        self.policy: Optional[Policy] = None
        self.determinism: Optional[Determinism] = None
        self._validate_options()

    # ------------------------------------------------------------------ public
    def run(self, resume: bool = False) -> RunState:
        self.determinism = make_determinism(
            seed=self.options.seed,
            clock_mode=self.options.clock_mode,
            frozen_clock=self.options.frozen_clock,
        )
        token = set_determinism(self.determinism)
        try:
            return self._run(resume)
        finally:
            reset_determinism(token)

    def _run(self, resume: bool = False) -> RunState:
        self._load_registry()
        self._load_personas()
        self._load_policy()
        target = self._resolve_target(self.options.target, self.options.source)
        if resume:
            state = self.state_store.load()
            self.workflow = self._resolve_workflow(state.workflow_id)
            if state.workflow_version and state.workflow_version != self.workflow.version:
                raise StateError(
                    "Workflow '{}' changed since this run started (saved version {}, current version {}). "
                    "Start a new run instead of resuming.".format(
                        state.workflow_id, state.workflow_version, self.workflow.version
                    )
                )
            self._prepare_resume(state, target)
        else:
            self.workflow = self._resolve_workflow(self.options.workflow_id)
            state = self._new_state(target)
            if self.state_store.kill_request():
                self.log("Ignoring a stale manual kill request from a previous run.")
                self.state_store.clear_kill()
            self.state_store.ensure()
            self.state_store.save(state)
        self.state = state
        self.evidence = EvidenceStore(self.root, state.run_id)
        evidence_phase = state.phase(PhaseName.EVIDENCE_COLLECTION.value)
        if resume and evidence_phase.status != PhaseStatus.COMPLETED.value:
            self.evidence.reconcile(state.checks)
        state.evidence_dir = self.evidence.summary()["directory"]
        self.state_store.save(state)

        self.log(
            "Run {} | workflow={}@{} | target={} | intent={!r}".format(
                state.run_id,
                state.workflow_id,
                state.workflow_version,
                state.target.resolved,
                state.intent,
            )
        )
        try:
            for phase in state.phases:
                if phase.name in (PhaseName.EVIDENCE_COLLECTION.value, PhaseName.CONFIDENCE_SCORING.value):
                    continue
                if state.kill_switch.triggered:
                    if phase.status not in (PhaseStatus.COMPLETED.value,):
                        self._skip_phase(phase, "Kill switch halts the remaining workflow.")
                    continue
                if phase.status == PhaseStatus.COMPLETED.value:
                    continue
                manual = self.state_store.kill_request()
                if manual:
                    self._trigger_kill("Manual kill requested: {}".format(manual), manual=True)
                    self._skip_phase(phase, "Kill switch halts the remaining workflow.")
                    continue
                self._run_phase(phase)

            self._run_phase(state.phase(PhaseName.EVIDENCE_COLLECTION.value))
            self._run_phase(state.phase(PhaseName.CONFIDENCE_SCORING.value))
            self._finalize_run()
        except Exception as exc:
            if self.state is not None:
                self.state.status = RunStatus.FAILED.value
                self.state.error = "{}: {}".format(type(exc).__name__, exc)
                self.state.touch()
                for phase in self.state.phases:
                    if phase.status == PhaseStatus.RUNNING.value:
                        phase.status = PhaseStatus.FAILED.value
                        phase.completed_at = utc_now()
                        phase.summary = self.state.error
                self.state_store.save(self.state)
                self.state_store.append_event(
                    {"run_id": self.state.run_id, "event": "run_failed", "error": self.state.error}
                )
                self.state_store.record_run(self.state)
            raise
        return state

    def abort(self, reason: str) -> None:
        self.state_store.request_kill(reason)
        self.state_store.append_event({"event": "manual_kill_requested", "reason": reason})

    # ------------------------------------------------------------------ setup
    def _validate_options(self) -> None:
        if self.options.kill_on not in ("critical", "high", "never"):
            raise HarnessError("kill_on must be one of: critical, high, never")
        if self.options.max_checks < 1:
            raise HarnessError("max_checks must be at least 1")
        try:
            width, height = self.options.viewport.lower().split("x", 1)
            if int(width) < 320 or int(height) < 240:
                raise ValueError
        except ValueError:
            raise HarnessError("viewport must look like 1440x900")
        if self.options.generated_at:
            try:
                self.options.generated_at = format_timestamp(self.options.generated_at)
            except ValueError as exc:
                raise HarnessError("generated_at must be an ISO-8601 timestamp: {}".format(exc))

    def _load_registry(self) -> None:
        self.registry = build_registry(self.root, log=self.log)
        self.log(
            "Loaded check registry: {} built-in, {} plugin, {} blocked.".format(
                self.registry.builtin_count(),
                self.registry.plugin_count(),
                self.registry.blocked_count(),
            )
        )

    def _load_personas(self) -> None:
        if self.registry is None:
            raise HarnessError("Check registry was not loaded before personas.")
        personas_dir = Path(self.options.personas_dir).expanduser()
        if not personas_dir.is_absolute():
            cwd_candidate = Path.cwd() / personas_dir
            root_candidate = self.root / personas_dir
            personas_dir = cwd_candidate if cwd_candidate.is_dir() else root_candidate
        all_personas = load_personas(
            personas_dir,
            require_minimum=self.options.require_personas,
        )
        unknown = unknown_persona_check_ids(all_personas, self.registry.ids())
        if unknown and self.registry.has_plugin_failures():
            all_persona_map = {persona.id: persona for persona in all_personas}
            owners: Dict[str, List[str]] = {}
            for persona_id, check_id in unknown:
                owners.setdefault(check_id, []).append(persona_id)
            for check_id, persona_ids in sorted(owners.items()):
                persona = all_persona_map.get(persona_ids[0])
                domain = persona.domains[0] if persona and persona.domains else "functional"
                self.registry.block_missing_plugin_check(
                    check_id,
                    persona_ids[0],
                    domain,
                    "Plugin check '{}' is declared by persona(s) {} but the plugin "
                    "failed to load or verify.".format(
                        check_id, ", ".join(persona_ids)
                    ),
                )
        validate_persona_checks(all_personas, self.registry.ids())
        self.registry.validate_ownership(all_personas)
        self.all_personas = all_personas
        self.personas = select_personas(
            all_personas, self.options.selected_personas or None
        )
        self.persona_map = {persona.id: persona for persona in self.personas}
        self.log(
            "Loaded personas: {}".format(", ".join(persona.id for persona in self.personas))
        )

    def _load_policy(self) -> None:
        if self.options.policy:
            path = Path(self.options.policy).expanduser()
            if not path.is_absolute():
                cwd_candidate = Path.cwd() / path
                root_candidate = self.root / path
                path = cwd_candidate if cwd_candidate.is_file() else root_candidate
            self.policy = load_policy(path)
        else:
            self.policy = Policy()

    def _resolve_target(self, raw: str, source: Optional[str]) -> Target:
        raw = (raw or "").strip()
        if not raw:
            raise TargetError("A target URL or directory is required.")
        if raw.startswith(("http://", "https://")):
            parsed = urlsplit(raw)
            if not parsed.netloc:
                raise TargetError("Target URL is missing a host: {}".format(raw))
            kind = "url"
            resolved = raw
            source_root = self._resolve_source(source) if source else None
        else:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            path = path.resolve()
            if not path.exists():
                raise TargetError("Target path does not exist: {}".format(path))
            kind = "directory" if path.is_dir() else "file"
            resolved = str(path)
            source_root = str(path if path.is_dir() else path.parent)
            if source:
                source_root = self._resolve_source(source)
        if source_root is not None:
            source_path = Path(source_root)
            if not source_path.is_dir():
                source_root = str(source_path.parent)
        return Target(raw=raw, kind=kind, resolved=resolved, source=source_root)

    @staticmethod
    def _resolve_source(source: Optional[str]) -> Optional[str]:
        if not source:
            return None
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.exists():
            raise TargetError("Source path does not exist: {}".format(path))
        return str(path)

    def _resolve_workflow(self, workflow_id: str) -> Workflow:
        return get_workflow(workflow_id, workflows_dir=self.root / ".harness" / "workflows")

    def _new_state(self, target: Target) -> RunState:
        if self.workflow is None:
            self.workflow = self._resolve_workflow(self.options.workflow_id)
        assert self.determinism is not None
        run_id = self.determinism.run_id()
        phases = [
            Phase(name=definition.name, title=definition.title)
            for definition in self.workflow.phases
        ]
        state = RunState(
            run_id=run_id,
            workflow_id=self.workflow.id,
            workflow_version=self.workflow.version,
            target=target,
            intent=self.options.intent,
            phases=phases,
            options={
                "max_checks": self.options.max_checks,
                "no_browser": self.options.no_browser,
                "headless": self.options.headless,
                "timeout_ms": self.options.timeout_ms,
                "viewport": self.options.viewport,
                "kill_on": self.options.kill_on,
                "selected_personas": list(self.options.selected_personas),
                "source": target.source,
                "seed": self.options.seed,
                "clock_mode": self.options.clock_mode,
                "generated_at": self.options.generated_at,
            },
            kill_switch=KillSwitch(armed=True, threshold=self.options.kill_on),
            seed=self.options.seed,
            clock_mode=self.options.clock_mode,
        )
        state.personas = [self._public_persona(persona) for persona in self.personas]
        state.evidence_dir = str(Path(".harness") / "evidence" / run_id)
        return state

    def _prepare_resume(self, state: RunState, target: Target) -> None:
        if state.status == RunStatus.HALTED.value:
            raise StateError(
                "This run was halted by the kill switch. Start a new run after investigating the finding, "
                "or remove .harness/KILL and use a new run ID."
            )
        if state.target.kind != target.kind or state.target.resolved != target.resolved:
            raise StateError(
                "Resume target does not match the saved run. Saved: {}; requested: {}.".format(
                    state.target.resolved, target.resolved
                )
            )
        execution = state.phase(PhaseName.EXECUTION.value)
        for phase in state.phases:
            if phase.status in (PhaseStatus.RUNNING.value, PhaseStatus.FAILED.value):
                phase.status = PhaseStatus.PENDING.value
                phase.completed_at = None
                phase.summary = ""
            if phase.name in (PhaseName.RISK_ANALYSIS.value, PhaseName.TEST_PLANNING.value):
                if phase.status != PhaseStatus.COMPLETED.value:
                    phase.status = PhaseStatus.PENDING.value
        if execution.status != PhaseStatus.COMPLETED.value:
            rerun_ids = {
                check.id
                for check in state.checks
                if check.status in (CheckStatus.RUNNING.value, CheckStatus.PLANNED.value, CheckStatus.ERROR.value)
            }
            state.findings = [finding for finding in state.findings if finding.check_id not in rerun_ids]
            state.blockers = [
                blocker for blocker in state.blockers if blocker.check_id not in rerun_ids
            ]
            remaining_invalid = [
                finding for finding in state.invalid_findings if finding.check_id not in rerun_ids
            ]
            if len(remaining_invalid) != len(state.invalid_findings):
                state.invalid_findings = remaining_invalid
                self._rebuild_contract_state(state)
            for check in state.checks:
                if check.id in rerun_ids:
                    check.status = CheckStatus.PLANNED.value
                    check.actual = ""
                    check.evidence = []
                    check.finding_ids = []
                    check.skip_reason = ""
                    check.started_at = None
                    check.completed_at = None
                    check.metadata = {}
        state.status = RunStatus.RUNNING.value
        state.error = ""
        state.completed_at = None
        state.halted_reason = ""
        state.personas = [self._public_persona(persona) for persona in self.personas]
        self.state_store.save(state)
        self.log("Resuming run {} from the first incomplete phase.".format(state.run_id))

    @staticmethod
    def _rebuild_contract_state(state: RunState) -> None:
        """Recompute contract-failure counters after a rerun removes records."""

        failures: Dict[str, int] = {}
        streaks: Dict[str, int] = {}
        degraded: List[str] = []
        for record in state.invalid_findings:
            persona = record.persona
            failures[persona] = failures.get(persona, 0) + 1
            streak = streaks.get(persona, 0) + 1
            streaks[persona] = streak
            if streak >= 3 and persona not in degraded:
                degraded.append(persona)
        state.contract_failures = failures
        state.contract_failure_streaks = streaks
        state.degraded_personas = degraded

    @staticmethod
    def _public_persona(persona: Persona) -> Dict[str, Any]:
        data = persona.public()
        data["schema_valid"] = True
        data["required_fields"] = list(persona.output_schema.get("required", []))
        return data

    # ------------------------------------------------------------------ phases
    def _run_phase(self, phase: Phase) -> None:
        assert self.state is not None
        if phase.status == PhaseStatus.COMPLETED.value:
            return
        if phase.name == PhaseName.RISK_ANALYSIS.value:
            self._phase_risk_analysis(phase)
        elif phase.name == PhaseName.TEST_PLANNING.value:
            self._phase_test_planning(phase)
        elif phase.name == PhaseName.EXECUTION.value:
            self._phase_execution(phase)
        elif phase.name == PhaseName.EVIDENCE_COLLECTION.value:
            self._phase_evidence(phase)
        elif phase.name == PhaseName.CONFIDENCE_SCORING.value:
            self._phase_confidence(phase)
        else:
            raise HarnessError("Unknown phase: {}".format(phase.name))

    def _start_phase(self, phase: Phase) -> None:
        assert self.state is not None
        phase.status = PhaseStatus.RUNNING.value
        phase.started_at = utc_now()
        self.state.current_phase = phase.name
        self.state.touch()
        self.state_store.save(self.state)
        self.log("{}: {}".format(phase.title, "started"))

    def _finish_phase(
        self, phase: Phase, status: str, summary: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        assert self.state is not None
        phase.status = status
        phase.summary = summary
        phase.completed_at = utc_now()
        if details:
            phase.details = details
        self.state.touch()
        self.state_store.save(self.state)
        self.log("{}: {} - {}".format(phase.title, status, summary))

    def _skip_phase(self, phase: Phase, reason: str) -> None:
        if phase.status == PhaseStatus.COMPLETED.value:
            return
        if phase.started_at is None:
            phase.started_at = utc_now()
        self._finish_phase(phase, PhaseStatus.SKIPPED.value, reason)

    def _phase_risk_analysis(self, phase: Phase) -> None:
        assert self.state is not None
        self._start_phase(phase)
        if self.state.inventory:
            inventory = Inventory.from_dict(self.state.inventory)
        elif self.state.target.source:
            self.log("Inspecting source inventory at {}".format(self.state.target.source))
            inventory = SourceInspector(Path(self.state.target.source)).discover()
        else:
            inventory = Inventory(available=False, root="")
        self.state.inventory = inventory.to_dict()
        browser_planned = self.state.target.kind == "url" and not self.options.no_browser
        risks = RiskAnalyzer(self.state.target, self.state.intent, inventory, browser_planned).analyze()
        self.state.risks = risks
        top = [{"id": risk.id, "title": risk.title, "priority": risk.priority} for risk in risks[:5]]
        summary = "{} risk(s) ranked from intent, target shape, and inventory signals.".format(len(risks))
        self._finish_phase(
            phase,
            PhaseStatus.COMPLETED.value,
            summary,
            {
                "inventory_available": inventory.available,
                "source_files": len(inventory.source_files),
                "markup_files": len(inventory.markup_files),
                "test_files": len(inventory.test_files),
                "manifests": inventory.manifests[:20],
                "browser_planned": browser_planned,
                "top_risks": top,
            },
        )

    def _phase_test_planning(self, phase: Phase) -> None:
        assert self.state is not None
        self._start_phase(phase)
        inventory = Inventory.from_dict(self.state.inventory) if self.state.inventory else Inventory(False, "")
        checks = plan_checks(
            self.state.target,
            inventory.to_dict(),
            self.personas,
            self.options.max_checks,
            self.options.no_browser,
            registry=self.registry,
            all_personas=self.all_personas,
            mobile=self.options.mobile,
        )
        apply_risk_weights(checks, self.state.risks)
        self.state.checks = checks
        self.state.blockers = []
        for check in checks:
            if check.status == CheckStatus.BLOCKED.value:
                self._record_blocker(
                    check,
                    boundary=check.actual or check.skip_reason,
                )
        by_kind: Dict[str, int] = {}
        by_persona: Dict[str, int] = {}
        for check in checks:
            by_kind[check.kind] = by_kind.get(check.kind, 0) + 1
            by_persona[check.persona] = by_persona.get(check.persona, 0) + 1
        summary = "{} check(s) planned: {} static, {} browser, {} plugin.".format(
            len(checks),
            by_kind.get("static", 0),
            by_kind.get("browser", 0),
            by_kind.get("plugin", 0),
        )
        self._finish_phase(
            phase,
            PhaseStatus.COMPLETED.value,
            summary,
            {"by_kind": by_kind, "by_persona": by_persona, "check_ids": [check.id for check in checks]},
        )

    def _phase_execution(self, phase: Phase) -> None:
        assert self.state is not None
        self._start_phase(phase)
        if self.state.kill_switch.triggered:
            self._mark_remaining_skipped("Kill switch halted execution before checks started.")
            self._finish_phase(
                phase,
                PhaseStatus.HALTED.value,
                "Execution halted: {}".format(self.state.halted_reason),
            )
            return

        terminal = (
            CheckStatus.PASSED.value,
            CheckStatus.FAILED.value,
            CheckStatus.BLOCKED.value,
            CheckStatus.SKIPPED.value,
        )
        serial_checks = [
            check
            for check in self.state.checks
            if check.kind != "browser" and check.status not in terminal
        ]
        if serial_checks:
            inventory = (
                Inventory.from_dict(self.state.inventory)
                if self.state.inventory
                else Inventory(False, "")
            )
            source_root = (
                Path(self.state.target.source) if self.state.target.source else None
            )
            static_runner = (
                StaticCheckRunner(
                    source_root,
                    inventory,
                    self.evidence,  # type: ignore[arg-type]
                )
                if source_root is not None
                else None
            )
            plugin_runner = PluginCheckRunner(
                self.state.target,
                source_root,
                inventory,
                self.evidence,  # type: ignore[arg-type]
                self.log,
            )
            for check in serial_checks:
                if self.state.kill_switch.triggered:
                    break
                manual = self.state_store.kill_request()
                if manual:
                    self._trigger_kill("Manual kill requested: {}".format(manual), manual=True)
                    break
                check.status = CheckStatus.RUNNING.value
                check.started_at = utc_now()
                self.state_store.save(self.state)
                evaluator = (
                    self.registry.evaluator(check.id) if self.registry is not None else None
                )
                if evaluator is not None:
                    outcome = plugin_runner.run(check, evaluator)
                elif check.kind == "static" and static_runner is not None:
                    outcome = static_runner.run(check)
                elif check.kind == "static":
                    outcome = CheckOutcome(
                        status=CheckStatus.BLOCKED.value,
                        actual="No source directory was available for static inspection.",
                        metadata={
                            "limitation": "Static inspection was not available for this run."
                        },
                    )
                else:
                    outcome = CheckOutcome(
                        status=CheckStatus.ERROR.value,
                        actual="No runner is registered for check '{}'.".format(check.id),
                    )
                halt = self._record_outcome(check, outcome)
                if halt:
                    break

        if not self.state.kill_switch.triggered:
            manual = self.state_store.kill_request()
            if manual:
                self._trigger_kill("Manual kill requested: {}".format(manual), manual=True)
        if not self.state.kill_switch.triggered:
            browser_checks = [
                check
                for check in self.state.checks
                if check.kind == "browser"
                and check.status
                not in (
                    CheckStatus.PASSED.value,
                    CheckStatus.FAILED.value,
                    CheckStatus.BLOCKED.value,
                    CheckStatus.SKIPPED.value,
                )
            ]
            desktop_browser_checks = [
                check
                for check in browser_checks
                if check.id not in {"browser.overflow", "browser.focus_order"}
            ]
            mobile_browser_checks = [
                check
                for check in browser_checks
                if check.id in {"browser.overflow", "browser.focus_order"}
            ]
            if desktop_browser_checks:
                if self.state.target.kind != "url":
                    for check in desktop_browser_checks:
                        self._record_outcome(
                            check,
                            CheckOutcome(
                                status=CheckStatus.BLOCKED.value,
                                actual="No live URL target was supplied; Operate coverage is unavailable.",
                                metadata={
                                    "limitation": "Operate coverage was not available for this run.",
                                    "viewport": self.options.viewport,
                                },
                            ),
                        )
                else:
                    viewport = self.options.viewport.lower().split("x", 1)

                    def desktop_callback(check: Check, outcome: CheckOutcome) -> bool:
                        outcome.metadata.setdefault("viewport", self.options.viewport)
                        return self._browser_outcome_callback(check, outcome)

                    runner = BrowserCheckRunner(
                        self.evidence,  # type: ignore[arg-type]
                        BrowserOptions(
                            headless=self.options.headless,
                            timeout_ms=self.options.timeout_ms,
                            viewport_width=int(viewport[0]),
                            viewport_height=int(viewport[1]),
                            capture_har=self.options.browser_har,
                            capture_trace=self.options.browser_trace,
                            capture_video=self.options.browser_video,
                            storage_state=self.options.storage_state,
                        ),
                        desktop_callback,
                    )
                    runner.run(self.state.target.resolved, desktop_browser_checks)
            if mobile_browser_checks:
                mobile_viewport = self.options.mobile_viewport.lower().split("x", 1)
                if self.state.target.kind != "url":
                    for check in mobile_browser_checks:
                        self._record_outcome(
                            check,
                            CheckOutcome(
                                status=CheckStatus.BLOCKED.value,
                                actual="No live URL target was supplied; Operate coverage is unavailable.",
                                metadata={
                                    "limitation": "Operate coverage was not available for this run.",
                                    "viewport": self.options.mobile_viewport,
                                },
                            ),
                        )
                else:
                    def mobile_callback(check: Check, outcome: CheckOutcome) -> bool:
                        outcome.metadata.setdefault("viewport", self.options.mobile_viewport)
                        return self._browser_outcome_callback(check, outcome)

                    runner = BrowserCheckRunner(
                        self.evidence,  # type: ignore[arg-type]
                        BrowserOptions(
                            headless=self.options.headless,
                            timeout_ms=self.options.timeout_ms,
                            viewport_width=int(mobile_viewport[0]),
                            viewport_height=int(mobile_viewport[1]),
                            capture_har=self.options.browser_har,
                            capture_trace=self.options.browser_trace,
                            capture_video=self.options.browser_video,
                            storage_state=self.options.storage_state,
                        ),
                        mobile_callback,
                    )
                    runner.run(self.state.target.resolved, mobile_browser_checks)

        if self.state.kill_switch.triggered:
            self._mark_remaining_skipped("Kill switch halted execution.")
            self._finish_phase(
                phase,
                PhaseStatus.HALTED.value,
                "Execution halted: {}".format(self.state.halted_reason),
                {"halted_reason": self.state.halted_reason},
            )
        else:
            executed = [
                check
                for check in self.state.checks
                if check.status in (CheckStatus.PASSED.value, CheckStatus.FAILED.value)
            ]
            blocked = [
                check
                for check in self.state.checks
                if check.status in (CheckStatus.BLOCKED.value, CheckStatus.ERROR.value)
            ]
            summary = "{} check(s) executed, {} blocked/errored, {} finding(s) recorded.".format(
                len(executed), len(blocked), len(self.state.findings)
            )
            self._finish_phase(
                phase,
                PhaseStatus.COMPLETED.value,
                summary,
                {
                    "executed": len(executed),
                    "blocked": len(blocked),
                    "findings": len(self.state.findings),
                },
            )

    def _browser_outcome_callback(self, check: Check, outcome: CheckOutcome) -> bool:
        halt = self._record_outcome(check, outcome)
        manual = self.state_store.kill_request() if self.state_store else None
        if manual and self.state is not None and not self.state.kill_switch.triggered:
            self._trigger_kill("Manual kill requested: {}".format(manual), manual=True)
            return True
        return halt

    def _blocker_for_check(self, check_id: str) -> Optional[Blocker]:
        assert self.state is not None
        return next(
            (blocker for blocker in self.state.blockers if blocker.check_id == check_id),
            None,
        )

    def _unblock_hint(self, check: Check, boundary: str) -> List[str]:
        text = boundary.lower()
        if "persona" in text and ("owner" in text or "ownership" in text):
            return [
                "Declare exactly one persona owner for check '{}' and re-run.".format(
                    check.id
                )
            ]
        if any(token in text for token in ("plugin", "signature", "trusted", "verify")):
            return [
                "Install a signed plugin with trusted metadata for check '{}' and re-run.".format(
                    check.id
                )
            ]
        if any(token in text for token in ("browser", "playwright", "chromium", "operate")):
            return [
                "Provide a runnable browser target and a Playwright/Chromium installation, "
                "then re-run check '{}'.".format(check.id)
            ]
        if any(token in text for token in ("source", "static", "inventory")):
            return [
                "Provide a readable source directory for check '{}' and re-run.".format(
                    check.id
                )
            ]
        return [
            "Resolve the reported boundary for check '{}' and re-run it.".format(check.id)
        ]

    def _record_blocker(
        self,
        check: Check,
        blocker: Optional[BlockerDraft] = None,
        boundary: str = "",
    ) -> Blocker:
        """Create exactly one structured blocker for a blocked check."""

        assert self.state is not None
        existing = self._blocker_for_check(check.id)
        if existing is not None:
            return existing
        if not isinstance(blocker, BlockerDraft):
            blocker = None
        boundary_text = _redact_string(
            str(
                (blocker.boundary if blocker else "")
                or boundary
                or check.actual
                or check.skip_reason
                or "Check was blocked before execution."
            )
        )
        attempted = _redact_string(
            str(
                (blocker.attempted_action if blocker else "")
                or check.title
                or check.id
            )
        )
        unblock_evidence = (
            [_redact_string(str(item)) for item in blocker.unblock_evidence]
            if blocker
            and isinstance(blocker.unblock_evidence, (list, tuple))
            and blocker.unblock_evidence
            else self._unblock_hint(check, boundary_text)
        )
        covered_by = (
            [_redact_string(str(item)) for item in blocker.covered_by]
            if blocker and isinstance(blocker.covered_by, (list, tuple))
            else []
        )
        record = Blocker(
            id=new_id("blocker"),
            persona=check.persona,
            check_id=check.id,
            attempted_action=attempted,
            boundary=boundary_text,
            unblock_evidence=unblock_evidence,
            covered_elsewhere=(
                blocker.covered_elsewhere
                if blocker is not None and isinstance(blocker.covered_elsewhere, bool)
                else False
            ),
            covered_by=covered_by,
            recorded_at=utc_now(),
        )
        persona = self.persona_map.get(check.persona)
        if persona is not None:
            errors = validate_blocker(record.to_dict(), persona)
            if errors:
                self.log(
                    "WARNING: blocker for check {} violates the {} blocker contract: {}".format(
                        check.id, persona.id, "; ".join(errors[:5])
                    )
                )
        self.state.blockers.append(record)
        return record

    def _compute_trend(self) -> Dict[str, Any]:
        assert self.state is not None
        history = self.state_store.load_history()
        previous = None
        for entry in history.get("runs", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("run_id") == self.state.run_id:
                continue
            target = entry.get("target", {})
            if not isinstance(target, dict):
                continue
            if target.get("raw") != self.state.target.raw:
                continue
            previous = entry
            break

        current_fingerprints = sorted(
            {finding.fingerprint for finding in self.state.findings if finding.fingerprint}
        )
        current_set = set(current_fingerprints)
        if previous is None:
            return {
                "baseline": True,
                "previous_run_id": None,
                "new": [],
                "resolved": [],
                "persistent": [],
                "score_delta": 0.0,
                "coverage_delta": 0.0,
            }

        previous_fingerprints = [
            str(item) for item in previous.get("finding_fingerprints", []) if item
        ]
        previous_set = set(previous_fingerprints)
        try:
            previous_score = float(previous.get("score", 0.0))
        except (TypeError, ValueError):
            previous_score = 0.0
        try:
            previous_coverage = float(previous.get("finding_counts", {}).get("total", 0)) if False else 0.0
        except (TypeError, ValueError):
            previous_coverage = 0.0
        # Coverage is not stored in history; keep the delta explicit as 0.0
        # rather than fabricating one from unrelated finding counts.
        return {
            "baseline": False,
            "previous_run_id": previous.get("run_id"),
            "new": sorted(current_set - previous_set),
            "resolved": sorted(previous_set - current_set),
            "persistent": sorted(current_set & previous_set),
            "score_delta": round(self.state.confidence.score - previous_score, 1),
            "coverage_delta": 0.0,
        }

    def _record_outcome(self, check: Check, outcome: CheckOutcome) -> bool:
        assert self.state is not None
        blocker_draft = (
            outcome.blocker if isinstance(outcome.blocker, BlockerDraft) else None
        )
        existing_blocker = self._blocker_for_check(check.id)
        blocked = (
            outcome.status == CheckStatus.BLOCKED.value
            or blocker_draft is not None
            or existing_blocker is not None
        )
        check.status = CheckStatus.BLOCKED.value if blocked else outcome.status
        check.evidence = list(outcome.evidence)
        if blocked:
            boundary = _redact_string(
                str(
                    (blocker_draft.boundary if blocker_draft else "")
                    or outcome.metadata.get("boundary")
                    or outcome.metadata.get("limitation")
                    or outcome.actual
                    or "Check was blocked."
                )
            )
            check.actual = _redact_string(str(outcome.actual)) or boundary
            check.metadata = _redact_payload(dict(outcome.metadata))
            check.finding_ids = []
            check.skip_reason = str(
                check.metadata.get("limitation") or boundary
            )
            if existing_blocker is None:
                self._record_blocker(check, blocker=blocker_draft, boundary=boundary)
            if outcome.findings:
                self.log(
                    "WARNING: {} was blocked; {} finding(s) were discarded because a "
                    "blocker cannot carry findings.".format(
                        check.id, len(outcome.findings)
                    )
                )
        else:
            check.actual = outcome.actual
            check.metadata = dict(outcome.metadata)
        if check.status in (
            CheckStatus.PASSED.value,
            CheckStatus.FAILED.value,
            CheckStatus.BLOCKED.value,
            CheckStatus.SKIPPED.value,
            CheckStatus.ERROR.value,
        ):
            check.completed_at = utc_now()
        if check.status == CheckStatus.SKIPPED.value:
            check.skip_reason = str(outcome.metadata.get("limitation") or outcome.actual)
        if outcome.status == CheckStatus.ERROR.value and not blocked:
            self.log("WARNING: {} errored: {}".format(check.id, outcome.actual))
        recorded_findings: List[Finding] = []
        invalid_count = 0
        if not blocked:
            for draft in outcome.findings:
                payload = _draft_payload(draft)
                persona = self.persona_map.get(check.persona)
                errors = (
                    validate_payload(payload, persona.output_schema)
                    if persona is not None
                    else []
                )
                if errors:
                    self._quarantine_finding(check, payload, errors)
                    invalid_count += 1
                    continue
                finding = self._to_finding(payload, check)
                existing = next(
                    (
                        item
                        for item in self.state.findings
                        if item.fingerprint == finding.fingerprint
                    ),
                    None,
                )
                if existing is not None:
                    for path in finding.evidence:
                        if path and path not in existing.evidence:
                            existing.evidence.append(path)
                    if existing.id not in check.finding_ids:
                        check.finding_ids.append(existing.id)
                    recorded_findings.append(existing)
                    self._record_valid_contract(check.persona)
                    continue
                self.state.findings.append(finding)
                check.finding_ids.append(finding.id)
                recorded_findings.append(finding)
                self._record_valid_contract(check.persona)
        if invalid_count:
            check.metadata["invalid_findings"] = invalid_count
        self.state.touch()
        if self.evidence is not None:
            self.evidence.checkpoint()
        self.state_store.save(self.state)
        self.state_store.append_event(
            {
                "run_id": self.state.run_id,
                "event": "check_completed",
                "check_id": check.id,
                "status": check.status,
                "findings": len(recorded_findings),
                "invalid_findings": invalid_count,
            }
        )
        threshold = self.options.kill_on
        if threshold == "never":
            return False
        trigger_rank = severity_rank(threshold)
        for finding in recorded_findings:
            if severity_rank(finding.severity) >= trigger_rank:
                self._trigger_kill(
                    "Automatic kill switch: {} finding from check {}".format(
                        finding.severity, check.id
                    ),
                    finding_id=finding.id,
                )
                return True
        return False

    def _quarantine_finding(
        self, check: Check, payload: Dict[str, Any], errors: List[str]
    ) -> InvalidFinding:
        """Persist a contract-violating finding outside scoring and kill policy."""

        assert self.state is not None
        persona = check.persona
        record = InvalidFinding(
            id=new_id("invalid-finding"),
            persona=persona,
            check_id=check.id,
            errors=[_redact_string(error) for error in errors],
            payload=_redact_payload(payload),
            recorded_at=utc_now(),
        )
        self.state.invalid_findings.append(record)
        self.state.contract_failures[persona] = (
            self.state.contract_failures.get(persona, 0) + 1
        )
        streak = self.state.contract_failure_streaks.get(persona, 0) + 1
        self.state.contract_failure_streaks[persona] = streak
        if streak >= 3 and persona not in self.state.degraded_personas:
            self.state.degraded_personas.append(persona)
        self.log(
            "WARNING: quarantined invalid finding from {} check {} ({} validation error(s)); "
            "first: {}".format(
                persona, check.id, len(record.errors), record.errors[0] if record.errors else "unknown"
            )
        )
        return record

    def _record_valid_contract(self, persona: str) -> None:
        assert self.state is not None
        if persona and self.state.contract_failure_streaks.get(persona):
            self.state.contract_failure_streaks[persona] = 0

    def _to_finding(self, payload: Dict[str, Any], check: Check) -> Finding:
        title = payload.get("title", "")
        domain = payload.get("domain", "")
        severity = payload.get("severity", "")
        location = payload.get("location", "")
        fingerprint = hashlib.sha256(
            "{}|{}|{}|{}".format(
                domain, str(title).strip().lower(), location, severity
            ).encode("utf-8")
        ).hexdigest()[:16]
        return Finding(
            id=new_id("finding"),
            title=title,
            severity=severity,
            domain=domain,
            persona=check.persona,
            strength=payload.get("strength", ""),
            consequence=payload.get("consequence", ""),
            location=location,
            reproduction=payload.get("reproduction", ""),
            evidence=list(payload.get("evidence") or []) or list(check.evidence),
            remediation=payload.get("remediation", ""),
            verification=payload.get("verification", ""),
            check_id=check.id,
            tags=list(payload.get("tags") or []),
            fingerprint=fingerprint,
        )

    def _mark_remaining_skipped(self, reason: str) -> None:
        assert self.state is not None
        for check in self.state.checks:
            if check.status in (CheckStatus.PLANNED.value, CheckStatus.RUNNING.value):
                self._record_outcome(
                    check,
                    CheckOutcome(
                        status=CheckStatus.SKIPPED.value,
                        actual=reason,
                        metadata={"halted": self.state.kill_switch.triggered, "limitation": reason},
                    ),
                )

    def _trigger_kill(self, reason: str, finding_id: str = "", manual: bool = False) -> None:
        assert self.state is not None
        if self.state.kill_switch.triggered:
            return
        self.state.kill_switch.triggered = True
        self.state.kill_switch.reason = reason
        self.state.kill_switch.triggered_at = utc_now()
        self.state.kill_switch.finding_id = finding_id
        self.state.kill_switch.manual = manual
        self.state.status = RunStatus.HALTED.value
        self.state.halted_reason = reason
        execution = next((phase for phase in self.state.phases if phase.name == PhaseName.EXECUTION.value), None)
        if execution is not None and execution.status in (PhaseStatus.RUNNING.value, PhaseStatus.PENDING.value):
            execution.status = PhaseStatus.HALTED.value
            execution.summary = reason
            execution.completed_at = utc_now()
        self.state.touch()
        self.state_store.save(self.state)
        self.state_store.append_event(
            {"run_id": self.state.run_id, "event": "kill_switch", "reason": reason, "manual": manual}
        )
        self.log("KILL SWITCH: {}".format(reason))

    def _phase_evidence(self, phase: Phase) -> None:
        assert self.state is not None
        if phase.status == PhaseStatus.COMPLETED.value:
            return
        self._start_phase(phase)
        assert self.evidence is not None
        metadata = {
            "run_id": self.state.run_id,
            "target": self.state.target.to_dict(),
            "intent": self.state.intent,
            "status": self.state.status,
            "checks": {
                "planned": len(self.state.checks),
                "passed": len([c for c in self.state.checks if c.status == CheckStatus.PASSED.value]),
                "failed": len([c for c in self.state.checks if c.status == CheckStatus.FAILED.value]),
                "blocked": len([c for c in self.state.checks if c.status == CheckStatus.BLOCKED.value]),
                "skipped": len([c for c in self.state.checks if c.status == CheckStatus.SKIPPED.value]),
                "error": len([c for c in self.state.checks if c.status == CheckStatus.ERROR.value]),
            },
            "findings": len(self.state.findings),
            "invalid_findings": len(self.state.invalid_findings),
            "contract_failures": dict(self.state.contract_failures),
            "degraded_personas": list(self.state.degraded_personas),
            "kill_switch": self.state.kill_switch.to_dict(),
            "determinism": {
                "seed": self.state.seed,
                "clock_mode": self.state.clock_mode,
            },
        }
        manifest = self.evidence.finalize(metadata)
        summary = manifest.get("summary", {})
        self.state.evidence_dir = summary.get("directory", self.state.evidence_dir)
        verification = self.evidence.verify()
        self._finish_phase(
            phase,
            PhaseStatus.COMPLETED.value,
            "{} evidence artifact(s), {} bytes; manifest {}.".format(
                summary.get("count", 0), summary.get("total_bytes", 0), manifest.get("manifest_path", "")
            ),
            {
                "manifest": manifest.get("manifest_path", ""),
                "manifest_sha256": manifest.get("manifest_sha256", ""),
                "summary": summary,
                "verification": verification,
            },
        )

    def _phase_confidence(self, phase: Phase) -> None:
        assert self.state is not None
        if phase.status == PhaseStatus.COMPLETED.value:
            return
        self._start_phase(phase)
        limitations: List[str] = []
        for check in self.state.checks:
            note = check.metadata.get("limitation")
            if note and str(note) not in limitations:
                limitations.append(str(note))
        if any(check.id == "browser.accessibility" for check in self.state.checks):
            limitations.append(
                "Automated accessibility checks do not establish WCAG conformance; human review is still required."
            )
        if any(check.id == "browser.security" for check in self.state.checks):
            limitations.append(
                "Security checks are bounded and non-destructive; they are not a penetration test or a certification."
            )
        if self.state.invalid_findings:
            limitations.append(
                "{} finding(s) violated a persona output contract and were quarantined; "
                "they are excluded from scoring and kill-switch evaluation.".format(
                    len(self.state.invalid_findings)
                )
            )
        for persona in self.state.degraded_personas:
            limitations.append(
                "Persona '{}' produced three consecutive schema-invalid findings and is "
                "marked degraded.".format(persona)
            )
        if self.policy is not None:
            self.state.options["policy"] = {"id": self.policy.id, "version": self.policy.version}
        active_suppressions, expired_suppressions = self.suppression_store.active_and_expired()
        for finding in self.state.findings:
            record = active_suppressions.get(finding.fingerprint)
            if record is None:
                finding.suppressed = False
                finding.suppression_owner = ""
                finding.suppression_reason = ""
                finding.suppression_ticket = ""
                finding.suppression_expires_at = ""
            else:
                finding.suppressed = True
                finding.suppression_owner = str(record.get("owner", ""))
                finding.suppression_reason = str(record.get("reason", ""))
                finding.suppression_ticket = str(record.get("ticket", ""))
                finding.suppression_expires_at = str(record.get("expires_at", ""))
        for fingerprint in expired_suppressions:
            expired_fingerprint = "suppression-expired:{}".format(fingerprint)
            if any(item.fingerprint == expired_fingerprint for item in self.state.findings):
                continue
            expired_finding = Finding(
                id=new_id("finding"),
                title="Expired suppression: {}".format(fingerprint),
                severity=Severity.LOW.value,
                domain="functional",
                persona="functional",
                strength="source_proven",
                consequence="A previously suppressed finding has re-entered scoring because its suppression expired.",
                location="suppression:{}".format(fingerprint),
                fingerprint=expired_fingerprint,
                tags=["suppression", "expired"],
            )
            self.state.findings.append(expired_finding)
        confidence = score_confidence(
            self.state.checks,
            self.state.findings,
            limitations=limitations,
            halted=self.state.kill_switch.triggered,
            kill_reason=self.state.halted_reason,
            policy=self.policy,
        )
        self.state.confidence = confidence
        self.state.options["trend"] = self._compute_trend()
        self._finish_phase(
            phase,
            PhaseStatus.COMPLETED.value,
            "Confidence {}/100 ({}); gate {}; coverage {}%.".format(
                confidence.score, confidence.band, confidence.gate.get("status"), confidence.coverage
            ),
            {
                "score": confidence.score,
                "band": confidence.band,
                "gate": confidence.gate,
                "breakdown": confidence.breakdown,
            },
        )

    def _finalize_run(self) -> None:
        assert self.state is not None
        if self.state.kill_switch.triggered:
            self.state.status = RunStatus.HALTED.value
        elif self.state.error:
            self.state.status = RunStatus.FAILED.value
        else:
            self.state.status = RunStatus.COMPLETED.value
        self.state.completed_at = utc_now()
        self.state.current_phase = PhaseName.CONFIDENCE_SCORING.value
        self.state.touch()
        self.state_store.save(self.state)
        assert self.evidence is not None and self.workflow is not None
        generated_at = self.options.generated_at or self.state.options.get("generated_at")
        report = build_report(
            self.state,
            self.workflow,
            self.evidence.summary(),
            generated_at=str(generated_at) if generated_at else None,
        )
        self.state_store.save_report(report)
        self.state_store.record_run(self.state, report)
        self.state_store.append_event(
            {
                "run_id": self.state.run_id,
                "event": "run_finished",
                "status": self.state.status,
                "confidence": self.state.confidence.score,
                "findings": len(self.state.findings),
            }
        )
        self.log(
            "Run {} {} | confidence {}/100 | {} finding(s) | report {}".format(
                self.state.run_id,
                self.state.status,
                self.state.confidence.score,
                len(self.state.findings),
                self.state_store.report_path,
            )
        )

    # ------------------------------------------------------------------ util
    def log(self, message: str) -> None:
        if self.log_fn is not None:
            self.log_fn(message)
        else:
            print("[quality-harness] {}".format(message), file=sys.stderr)
