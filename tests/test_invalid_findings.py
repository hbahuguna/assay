"""Persona output-contract quarantine, contract-failure, and degradation tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.checks import CheckDefinition
from harness.models import CheckStatus
from harness.orchestrator import Orchestrator, RunOptions
from harness.outcomes import CheckOutcome, FindingDraft
from harness.plugins import CheckPlugin, sign_plugin
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"
PLUGIN_ID = "example.contract_probe"
ENTRY_VALUE = "contract_probe:PLUGIN"
SIGNING_KEY = "contract-probe-key"
SIGNING_SECRET = "synthetic-contract-probe-signing-secret"
SENSITIVE_VALUE = "not-a-real-token-value"


class MissingVerificationDraft(FindingDraft):
    """A malformed draft that cannot supply the required verification field."""

    @property
    def verification(self):
        raise AttributeError("verification")

    @verification.setter
    def verification(self, value):
        pass


class EntryPointStub:
    def __init__(self, name, value, loader):
        self.name = name
        self.value = value
        self._loader = loader

    def load(self):
        return self._loader()


def write_trust_store(root):
    path = Path(root) / ".harness" / "plugins" / "trusted_keys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"keys": {SIGNING_KEY: SIGNING_SECRET}}), encoding="utf-8"
    )


def personas_with_plugin(root):
    destination = Path(root) / "personas"
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(PERSONAS.glob("*.md")):
        text = source.read_text(encoding="utf-8")
        if source.name == "functional.md":
            marker = "  - browser.links\n"
            if marker not in text:
                raise AssertionError("functional persona check marker not found")
            text = text.replace(marker, marker + "  - {}\n".format(PLUGIN_ID))
        (destination / source.name).write_text(text, encoding="utf-8")
    return destination


def plugin_definition():
    return CheckDefinition(
        id=PLUGIN_ID,
        title="Persona output contracts are enforced",
        domain="functional",
        kind="plugin",
        intent="Confirm malformed specialist findings cannot affect release confidence.",
        expected="Every finding validates against its owning persona output schema.",
        base_weight=0.5,
        requires="source",
    )


def plugin_entry(evaluate):
    definition = plugin_definition()
    plugin = CheckPlugin(
        definition=definition,
        evaluate=evaluate,
        signature=sign_plugin(
            definition, ENTRY_VALUE, SIGNING_KEY, SIGNING_SECRET
        ),
    )
    return EntryPointStub("contract_probe", ENTRY_VALUE, lambda: plugin)


def run_plugin(root, evaluate, kill_on="critical"):
    write_trust_store(root)
    personas = personas_with_plugin(root)
    options = RunOptions(
        target=str(SAMPLE),
        personas_dir=str(personas),
        no_browser=True,
        kill_on=kill_on,
    )
    with patch("harness.plugins.load_entry_points", return_value=[plugin_entry(evaluate)]):
        return Orchestrator(root, options, log=lambda message: None).run()


def invalid_outcome(context):
    evidence = context.save_json(
        "contract-probe",
        {"probe": "invalid-finding"},
        "Evidence attached to an intentionally malformed finding.",
    )
    wrong_enum = FindingDraft(
        title="Malformed severity",
        severity="blocker",
        domain="functional",
        strength="observed",
        consequence="A report consumer could trust an unranked finding.",
        location="src/app.py:10",
        reproduction="Record this finding.",
        remediation="Use a documented severity.",
        verification="Re-run the contract check.",
        tags=["contract-probe"],
    )
    wrong_nested = FindingDraft(
        title="Malformed nested tags",
        severity="high",
        domain="functional",
        strength="observed",
        consequence="token={} must never persist.".format(SENSITIVE_VALUE),
        location="src/app.py:11",
        reproduction="Record this finding.",
        remediation="Use an array of strings.",
        verification="Re-run the contract check.",
        tags={"not": "an array"},
    )
    missing_field = MissingVerificationDraft(
        title="Missing verification",
        severity="critical",
        domain="functional",
        strength="observed",
        consequence="The report would omit a required field.",
        location="src/app.py:12",
        reproduction="Record this finding.",
        remediation="Provide verification steps.",
        verification="Re-run the contract check.",
        tags=["contract-probe"],
    )
    return CheckOutcome(
        status=CheckStatus.FAILED.value,
        actual="Three malformed findings were produced.",
        evidence=[evidence],
        findings=[wrong_enum, wrong_nested, missing_field],
    )


def control_outcome(context):
    evidence = context.save_json(
        "contract-probe",
        {"probe": "control"},
        "Evidence attached to the control check.",
    )
    return CheckOutcome(
        status=CheckStatus.FAILED.value,
        actual="The control check failed without a finding.",
        evidence=[evidence],
    )


class InvalidFindingQuarantineTests(unittest.TestCase):
    def test_invalid_findings_are_quarantined_without_scoring_or_killing(self):
        with tempfile.TemporaryDirectory() as invalid_dir, tempfile.TemporaryDirectory() as control_dir:
            invalid_root = Path(invalid_dir)
            control_root = Path(control_dir)
            invalid = run_plugin(invalid_root, invalid_outcome)
            control = run_plugin(control_root, control_outcome)

            self.assertEqual(invalid.status, "completed")
            self.assertFalse(invalid.kill_switch.triggered)
            self.assertEqual(
                [finding for finding in invalid.findings if finding.check_id == PLUGIN_ID],
                [],
            )
            self.assertEqual(len(invalid.invalid_findings), 3)
            self.assertEqual(invalid.confidence.score, control.confidence.score)
            self.assertEqual(invalid.contract_failures, {"functional": 3})
            self.assertEqual(invalid.contract_failure_streaks, {"functional": 3})
            self.assertEqual(invalid.degraded_personas, ["functional"])

            report = StateStore(invalid_root).load_report()
            self.assertEqual(len(report["invalid_findings"]), 3)
            for entry in report["invalid_findings"]:
                self.assertEqual(entry["persona"], "functional")
                self.assertEqual(entry["check_id"], PLUGIN_ID)
                self.assertTrue(entry["errors"])
                self.assertIn("payload", entry)
                self.assertEqual(entry["contract"], "persona_output_schema")

            self.assertEqual(report["contract_failures"]["total"], 3)
            self.assertEqual(
                report["contract_failures"]["by_persona"], {"functional": 3}
            )
            self.assertEqual(
                report["contract_failures"]["degraded_personas"], ["functional"]
            )
            functional = next(
                persona
                for persona in report["personas"]
                if persona["id"] == "functional"
            )
            self.assertTrue(functional["degraded"])
            self.assertEqual(functional["contract_failures"], 3)
            self.assertEqual(functional["contract_failure_streak"], 3)
            self.assertTrue(
                any("quarantined" in note for note in report["limitations"])
            )
            self.assertTrue(
                any("marked degraded" in note for note in report["limitations"])
            )

            persisted = (
                (invalid_root / ".harness" / "progress.json").read_text(encoding="utf-8")
                + (invalid_root / ".harness" / "report.json").read_text(encoding="utf-8")
            )
            for path in (invalid_root / ".harness" / "evidence").rglob("*"):
                if path.is_file():
                    persisted += path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn(SENSITIVE_VALUE, persisted)
            self.assertIn("[REDACTED]", persisted)

            report_text = json.dumps(report)
            self.assertNotIn(SENSITIVE_VALUE, report_text)
            self.assertIn("[REDACTED", report_text)

    def test_missing_enum_and_nested_contract_errors_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = run_plugin(root, invalid_outcome)
            report = StateStore(root).load_report()

            errors = " ".join(
                error
                for entry in report["invalid_findings"]
                for error in entry["errors"]
            )
            self.assertIn("verification", errors)
            self.assertIn("not one of", errors)
            self.assertIn("expected type array", errors)
            self.assertEqual(state.check(PLUGIN_ID).finding_ids, [])
            self.assertEqual(state.check(PLUGIN_ID).metadata["invalid_findings"], 3)

    def test_a_valid_finding_resets_the_consecutive_contract_failure_streak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def two_invalid_then_valid(context):
                evidence = context.save_json(
                    "contract-probe",
                    {"probe": "reset"},
                    "Evidence attached to the streak-reset check.",
                )
                invalid = FindingDraft(
                    title="Malformed severity",
                    severity="blocker",
                    domain="functional",
                    strength="observed",
                    consequence="Unranked finding.",
                    location="src/app.py:20",
                    reproduction="Record it.",
                    remediation="Use a documented severity.",
                    verification="Re-run.",
                )
                valid = FindingDraft(
                    title="A valid finding",
                    severity="low",
                    domain="functional",
                    strength="observed",
                    consequence="The release has a minor visible defect.",
                    location="src/app.py:21",
                    reproduction="Open the cited location.",
                    remediation="Fix the defect.",
                    verification="Re-run the check.",
                    tags=["contract-probe"],
                )
                invalid_again = FindingDraft(
                    title="Malformed tags",
                    severity="high",
                    domain="functional",
                    strength="observed",
                    consequence="Wrong nested shape.",
                    location="src/app.py:22",
                    reproduction="Record it.",
                    remediation="Use an array of strings.",
                    verification="Re-run.",
                    tags={"bad": "shape"},
                )
                return CheckOutcome(
                    status=CheckStatus.FAILED.value,
                    actual="Two invalid findings surround one valid finding.",
                    evidence=[evidence],
                    findings=[invalid, valid, invalid_again],
                )

            state = run_plugin(root, two_invalid_then_valid, kill_on="never")
            self.assertEqual(state.contract_failures, {"functional": 2})
            self.assertEqual(state.contract_failure_streaks, {"functional": 1})
            self.assertEqual(state.degraded_personas, [])
            report = StateStore(root).load_report()
            functional = next(
                persona
                for persona in report["personas"]
                if persona["id"] == "functional"
            )
            self.assertFalse(functional["degraded"])


if __name__ == "__main__":
    unittest.main()
