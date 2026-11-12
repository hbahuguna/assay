"""Structured refusal/blocker contract tests."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harness.cli import main
from harness.errors import BrowserUnavailable
from harness.models import CheckStatus
from harness.orchestrator import Orchestrator, RunOptions
from harness.outcomes import BlockerDraft, CheckOutcome, FindingDraft
from harness.personas import load_personas, validate_blocker
from harness.plugins import sign_plugin
from harness.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"
SAMPLE_PLUGIN = ROOT / "examples" / "plugins" / "sample_check.py"
SECRET = "synthetic-plugin-signing-secret"
SENSITIVE_VALUE = "not-a-real-token-value"
CHECK_ID = "example.package_metadata"


class EntryPointStub:
    def __init__(self, name, value, loader):
        self.name = name
        self.value = value
        self._loader = loader

    def load(self):
        return self._loader()


def load_sample_plugin():
    spec = importlib.util.spec_from_file_location("sample_check", SAMPLE_PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_trust_store(root):
    path = Path(root) / ".harness" / "plugins" / "trusted_keys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"keys": {"test-key": SECRET}}),
        encoding="utf-8",
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
            text = text.replace(marker, marker + "  - {}\n".format(CHECK_ID))
        (destination / source.name).write_text(text, encoding="utf-8")
    return destination


def run_plugin(root, plugin, personas_dir):
    entry = EntryPointStub("sample_check", "sample_check:PLUGIN", lambda: plugin)
    options = RunOptions(
        target=str(SAMPLE),
        personas_dir=str(personas_dir),
        no_browser=True,
    )
    with patch("harness.plugins.load_entry_points", return_value=[entry]):
        return Orchestrator(root, options).run()


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def signed_plugin(evaluate):
    plugin = load_sample_plugin().PLUGIN
    plugin.evaluate = evaluate
    plugin.signature = sign_plugin(
        plugin.definition,
        "sample_check:PLUGIN",
        "test-key",
        SECRET,
    )
    return plugin


class BlockerContractTests(unittest.TestCase):
    def test_blocked_outcome_produces_one_blocker_and_no_findings(self):
        def evaluate(context):
            evidence = context.save_json(
                "blocked-outcome",
                {"reason": "read-only run"},
                "Evidence that the plugin stopped at its boundary.",
            )
            return CheckOutcome(
                status=CheckStatus.BLOCKED.value,
                actual="Refused to submit a synthetic transaction.",
                evidence=[evidence],
                findings=[
                    FindingDraft(
                        title="This finding must be discarded",
                        severity="high",
                        domain="data_integrity",
                        strength="observed",
                        consequence="A blocked check cannot carry a finding.",
                    )
                ],
                blocker=BlockerDraft(
                    attempted_action="Submit a synthetic checkout transaction.",
                    boundary="The run is read-only and has no synthetic tenant.",
                    unblock_evidence=[
                        "Provide a disposable checkout tenant and an approved transaction budget."
                    ],
                    covered_elsewhere=False,
                ),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trust_store(root)
            personas = personas_with_plugin(root)
            state = run_plugin(root, signed_plugin(evaluate), personas)

            self.assertEqual(state.status, "completed")
            check = state.check(CHECK_ID)
            self.assertIsNotNone(check)
            self.assertEqual(check.status, CheckStatus.BLOCKED.value)
            self.assertEqual(check.finding_ids, [])
            self.assertFalse(
                [finding for finding in state.findings if finding.check_id == CHECK_ID]
            )
            blockers = [
                blocker for blocker in state.blockers if blocker.check_id == CHECK_ID
            ]
            self.assertEqual(len(blockers), 1)
            blocker = blockers[0]
            self.assertEqual(blocker.persona, "functional")
            self.assertEqual(
                blocker.attempted_action,
                "Submit a synthetic checkout transaction.",
            )
            self.assertIn("read-only", blocker.boundary)
            self.assertTrue(blocker.unblock_evidence)
            self.assertFalse(blocker.covered_elsewhere)

            report = StateStore(root).load_report()
            self.assertEqual(len(report["blockers"]), 1)
            self.assertEqual(report["blockers"][0]["check_id"], CHECK_ID)
            self.assertEqual(report["blockers"][0]["covered_elsewhere"], False)
            self.assertEqual(report["coverage"]["blockers"], 1)
            self.assertEqual(
                report["coverage"]["by_domain"]["data_integrity"]["blockers"],
                1,
            )

            code, stdout, _ = run_cli(["status", "--root", str(root)])
            self.assertEqual(code, 0)
            status = json.loads(stdout)
            self.assertEqual(len(status["blockers"]), 1)
            self.assertEqual(status["blockers"][0]["check_id"], CHECK_ID)
            self.assertEqual(status["blockers_by_persona"], {"functional": 1})
            self.assertEqual(status["blockers_by_check"], {CHECK_ID: 1})

    def test_a_passed_outcome_with_a_blocker_is_forced_to_blocked(self):
        def evaluate(context):
            return CheckOutcome(
                status=CheckStatus.PASSED.value,
                actual="The plugin incorrectly claimed a pass.",
                blocker=BlockerDraft(
                    attempted_action="Claim the check passed.",
                    boundary="A refusal cannot be converted into a pass.",
                    unblock_evidence=["Resolve the blocker before rerunning."],
                ),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trust_store(root)
            personas = personas_with_plugin(root)
            state = run_plugin(root, signed_plugin(evaluate), personas)

            check = state.check(CHECK_ID)
            self.assertIsNotNone(check)
            self.assertEqual(check.status, CheckStatus.BLOCKED.value)
            self.assertEqual(check.finding_ids, [])
            self.assertEqual(
                len([blocker for blocker in state.blockers if blocker.check_id == CHECK_ID]),
                1,
            )

    def test_planning_blocked_plugin_produces_a_blocker_and_no_findings(self):
        plugin = signed_plugin(lambda context: CheckOutcome())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trust_store(root)
            state = run_plugin(root, plugin, PERSONAS)

            check = state.check(CHECK_ID)
            self.assertIsNotNone(check)
            self.assertEqual(check.status, CheckStatus.BLOCKED.value)
            self.assertEqual(check.finding_ids, [])
            blockers = [
                blocker for blocker in state.blockers if blocker.check_id == CHECK_ID
            ]
            self.assertEqual(len(blockers), 1)
            self.assertIn("declared persona owner", blockers[0].boundary)
            self.assertIn(CHECK_ID, blockers[0].unblock_evidence[0])

    def test_blocker_text_is_redacted_before_persistence(self):
        def evaluate(context):
            return CheckOutcome(
                status=CheckStatus.BLOCKED.value,
                actual="Refused to read a protected resource.",
                blocker=BlockerDraft(
                    attempted_action="Read the protected resource.",
                    boundary="Credentials were required: token={}".format(
                        SENSITIVE_VALUE
                    ),
                    unblock_evidence=["Provide a scoped synthetic credential."],
                ),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trust_store(root)
            personas = personas_with_plugin(root)
            state = run_plugin(root, signed_plugin(evaluate), personas)

            check = state.check(CHECK_ID)
            self.assertIsNotNone(check)
            blocker = next(
                blocker
                for blocker in state.blockers
                if blocker.check_id == CHECK_ID
            )
            self.assertNotIn(SENSITIVE_VALUE, blocker.boundary)
            self.assertIn("token=[REDACTED]", blocker.boundary)

            serialized = ""
            harness_dir = root / ".harness"
            for path in sorted(harness_dir.rglob("*")):
                if path.is_file():
                    serialized += path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn(SENSITIVE_VALUE, serialized)
            self.assertIn("[REDACTED", serialized)

            code, stdout, _ = run_cli(["status", "--root", str(root)])
            self.assertEqual(code, 0)
            self.assertNotIn(SENSITIVE_VALUE, stdout)
            status = json.loads(stdout)
            self.assertEqual(
                status["blockers"][0]["boundary"],
                "Credentials were required: token=[REDACTED]",
            )

    def test_each_persona_owns_a_valid_blocker_contract(self):
        personas = load_personas(PERSONAS, require_minimum=3)
        self.assertEqual(
            {persona.id for persona in personas},
            {"functional", "security", "accessibility"},
        )
        for persona in personas:
            with self.subTest(persona=persona.id):
                self.assertEqual(persona.blocker_schema.get("type"), "object")
                self.assertTrue(persona.blocker_schema.get("required"))
                payload = {
                    "persona": persona.id,
                    "check_id": "browser.accessibility",
                    "attempted_action": "Inspect the rendered accessibility tree.",
                    "boundary": "The browser is unavailable.",
                    "unblock_evidence": ["Install Chromium and rerun the check."],
                    "covered_elsewhere": False,
                    "covered_by": [],
                }
                self.assertEqual(validate_blocker(payload, persona), [])
                invalid = dict(payload)
                del invalid["boundary"]
                self.assertTrue(validate_blocker(invalid, persona))

    def test_unavailable_browser_produces_one_blocker_per_browser_check(self):
        async def unavailable(self):
            raise BrowserUnavailable("synthetic browser runtime is unavailable")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target="http://127.0.0.1:9/",
                source=str(SAMPLE),
                personas_dir=str(PERSONAS),
                ssrf_allowlist=["127.0.0.0/8"],
            )
            with patch(
                "harness.browser.BrowserController.start", unavailable
            ):
                state = Orchestrator(root, options).run()

            browser_checks = [
                check for check in state.checks if check.kind == "browser"
            ]
            self.assertEqual(len(browser_checks), 7)
            self.assertEqual(
                {check.status for check in browser_checks},
                {CheckStatus.BLOCKED.value},
            )
            blocker_ids = {blocker.check_id for blocker in state.blockers}
            self.assertEqual(
                blocker_ids,
                {check.id for check in browser_checks},
            )
            self.assertEqual(
                {blocker.persona for blocker in state.blockers},
                {"functional", "security", "accessibility"},
            )
            for blocker in state.blockers:
                self.assertIn("synthetic browser runtime", blocker.boundary)
                self.assertTrue(blocker.unblock_evidence)
                self.assertFalse(blocker.covered_elsewhere)


if __name__ == "__main__":
    unittest.main()
