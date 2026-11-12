"""Metering event contract and local file-sink tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness import cli
from harness.evidence import EvidenceStore
from harness.metering import (
    EVENT_BROWSER_MINUTE_CONSUMED,
    EVENT_CHECK_EXECUTED,
    EVENT_EVIDENCE_BYTE_STORED,
    EVENT_EXPORT_GENERATED,
    EVENT_RUN_STARTED,
    EVENT_TYPES,
    REQUIRED_ENVELOPE_FIELDS,
    SCHEMA_FILES,
    SCHEMA_VERSION,
    MeteringEmitter,
    build_event,
    event_schema_paths,
    validate_event,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample-repo"
PERSONAS = ROOT / "personas"


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def read_events(root: Path):
    path = root / ".harness" / "metering" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class MeteringContractTests(unittest.TestCase):
    def test_v1_event_names_are_frozen(self):
        self.assertEqual(
            EVENT_TYPES,
            (
                "run_started",
                "check_executed",
                "browser_minute_consumed",
                "evidence_byte_stored",
                "export_generated",
            ),
        )

    def test_schema_exists_for_envelope_and_every_event_type(self):
        self.assertEqual(set(SCHEMA_FILES), {"envelope", *EVENT_TYPES})
        for name, path in event_schema_paths().items():
            self.assertTrue(path.is_file(), name)
            schema = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(schema, dict)

    def test_envelope_schema_freezes_required_contract_fields(self):
        envelope = json.loads(
            (ROOT / "schemas" / "metering" / "envelope.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(envelope["properties"]["schema_version"]["const"], SCHEMA_VERSION)
        for field in ("workspace", "run", "quantity", "unit", "timestamp", "source"):
            self.assertIn(field, envelope["required"])
            self.assertIn(field, envelope["properties"])
        for event_type in EVENT_TYPES:
            schema = json.loads(
                (ROOT / "schemas" / "metering" / SCHEMA_FILES[event_type]).read_text(
                    encoding="utf-8"
                )
            )
            for field in REQUIRED_ENVELOPE_FIELDS:
                self.assertIn(field, schema["required"])
                self.assertIn(field, schema["properties"])
            self.assertEqual(
                schema["properties"]["event_type"]["const"], event_type
            )

    def test_every_event_type_validates_with_its_required_metadata(self):
        metadata = {
            EVENT_RUN_STARTED: {"workflow_id": "release-confidence"},
            EVENT_CHECK_EXECUTED: {"check_id": "static.tests", "status": "passed"},
            EVENT_BROWSER_MINUTE_CONSUMED: {"mode": "desktop"},
            EVENT_EVIDENCE_BYTE_STORED: {"kind": "text", "path": ".harness/evidence/x/a.txt"},
            EVENT_EXPORT_GENERATED: {"format": "sarif"},
        }
        for event_type in EVENT_TYPES:
            quantity = 0.5 if event_type == EVENT_BROWSER_MINUTE_CONSUMED else 1
            event = build_event(
                event_type,
                run="run-1",
                workspace="ws-1",
                source="oss",
                quantity=quantity,
                metadata=metadata[event_type],
            )
            self.assertEqual(validate_event(event), [])
            for field in REQUIRED_ENVELOPE_FIELDS:
                self.assertIn(field, event)

    def test_validation_rejects_unknown_type_and_missing_fields(self):
        event = build_event(
            EVENT_RUN_STARTED,
            run="run-1",
            metadata={"workflow_id": "release-confidence"},
        )
        broken = dict(event)
        broken.pop("workspace")
        self.assertIn("missing required field: workspace", validate_event(broken))
        broken = dict(event)
        broken["event_type"] = "unknown"
        self.assertTrue(validate_event(broken))
        with self.assertRaises(Exception):
            build_event("unknown", run="run-1")


class MeteringSinkTests(unittest.TestCase):
    def test_duplicate_idempotency_key_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            emitter = MeteringEmitter(root, "run-1")
            first = emitter.emit(
                EVENT_CHECK_EXECUTED,
                1,
                "check",
                idempotency_key="run-1:check:static.tests",
                metadata={"check_id": "static.tests", "status": "passed"},
            )
            duplicate = emitter.emit(
                EVENT_CHECK_EXECUTED,
                1,
                "check",
                idempotency_key="run-1:check:static.tests",
                metadata={"check_id": "static.tests", "status": "passed"},
            )
            self.assertIsNotNone(first)
            self.assertIsNone(duplicate)
            self.assertEqual(len(read_events(root)), 1)

            # A new process must also honor the persisted key.
            reloaded = MeteringEmitter(root, "run-1")
            self.assertIsNone(
                reloaded.emit(
                    EVENT_CHECK_EXECUTED,
                    1,
                    "check",
                    idempotency_key="run-1:check:static.tests",
                    metadata={"check_id": "static.tests", "status": "passed"},
                )
            )
            self.assertEqual(len(read_events(root)), 1)

    def test_event_has_all_envelope_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            emitter = MeteringEmitter(root, "run-1", workspace="workspace-1")
            event = emitter.emit(
                EVENT_RUN_STARTED,
                1,
                "run",
                idempotency_key="run-1:run_started",
                metadata={"workflow_id": "release-confidence"},
            )
            self.assertIsNotNone(event)
            self.assertEqual(event["workspace"], "workspace-1")
            self.assertEqual(event["run"], "run-1")
            self.assertEqual(event["quantity"], 1)
            self.assertEqual(event["unit"], "run")
            self.assertEqual(event["source"], "oss")
            self.assertEqual(event["schema_version"], SCHEMA_VERSION)
            self.assertEqual(validate_event(event), [])

    def test_identical_artifact_content_is_metered_per_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            emitter = MeteringEmitter(root, "run-1")
            store = EvidenceStore(root, "run-1", metering=emitter)
            store.save_text("first", "same content", check_id="check-a")
            store.save_text("second", "same content", check_id="check-b")
            events = [
                event
                for event in emitter.events()
                if event["event_type"] == EVENT_EVIDENCE_BYTE_STORED
            ]
            self.assertEqual(len(events), 2)
            self.assertEqual(sum(event["quantity"] for event in events), 24)


class LocalRunMeteringTests(unittest.TestCase):
    def test_oss_run_emits_reconcilable_events_to_file_sink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            events = read_events(root)
            self.assertTrue(events)
            types = {event["event_type"] for event in events}
            self.assertIn(EVENT_RUN_STARTED, types)
            self.assertIn(EVENT_CHECK_EXECUTED, types)
            self.assertIn(EVENT_EVIDENCE_BYTE_STORED, types)
            for event in events:
                self.assertEqual(event["run"], report["run_id"])
                self.assertEqual(event["workspace"], "local")
                self.assertEqual(event["source"], "oss")
                self.assertEqual(validate_event(event), [])
            self.assertEqual(report["metering"]["event_count"], len(events))
            self.assertEqual(report["metering"]["sink"], ".harness/metering/events.jsonl")

    def test_custom_workspace_and_sink_are_honored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sink = root / "custom" / "usage.jsonl"
            code, stdout, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root),
                    "--workspace",
                    "acme",
                    "--metering-sink",
                    str(sink),
                ]
            )
            self.assertEqual(code, 0)
            report = json.loads(stdout)
            events = [
                json.loads(line)
                for line in sink.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(events)
            for event in events:
                self.assertEqual(event["workspace"], "acme")
            self.assertEqual(report["metering"]["workspace"], "acme")


class ExportMeteringTests(unittest.TestCase):
    def test_export_generation_emits_one_idempotent_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, _, _ = run_cli(
                [
                    "run",
                    "--target",
                    str(SAMPLE),
                    "--no-browser",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 0)
            output = root / "report.sarif"
            code, _, _ = run_cli(
                ["sarif", "--root", str(root), "--output", str(output)]
            )
            self.assertEqual(code, 0)
            code, _, _ = run_cli(
                ["sarif", "--root", str(root), "--output", str(output)]
            )
            self.assertEqual(code, 0)
            code, _, _ = run_cli(["junit", "--root", str(root), "--output", str(root / "junit.xml")])
            self.assertEqual(code, 0)

            events = read_events(root)
            exports = [
                event for event in events if event["event_type"] == EVENT_EXPORT_GENERATED
            ]
            self.assertEqual(len(exports), 2)
            self.assertEqual({event["metadata"]["format"] for event in exports}, {"sarif", "junit"})
            for event in exports:
                self.assertEqual(event["run"], json.loads(
                    (root / ".harness" / "report.json").read_text(encoding="utf-8")
                )["run_id"])
                self.assertEqual(event["quantity"], 1)
                self.assertEqual(event["unit"], "export")


if __name__ == "__main__":
    unittest.main()
