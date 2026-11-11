"""Persona lint and scaffold command tests."""

import io
import json
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from harness.cli import main
from harness.personas import load_persona

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"


def run_cli(arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(arguments)
    return code, stdout.getvalue(), stderr.getvalue()


def run_json(arguments):
    code, stdout, stderr = run_cli(arguments)
    try:
        payload = json.loads(stdout)
    except ValueError:
        raise AssertionError(
            "stdout was not JSON ({}): {!r}; stderr={!r}".format(
                code, stdout, stderr
            )
        )
    return code, payload, stderr


class PersonaLintTests(unittest.TestCase):
    def test_clean_contracts_pass_lint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, payload, _ = run_json(
                [
                    "personas",
                    "lint",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root),
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["schema_version"], "quality-harness.personas.lint/v1")
        self.assertEqual(payload["status"], "valid")
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["summary"]["valid_personas"], 3)
        self.assertEqual(
            {persona["id"] for persona in payload["personas"]},
            {"functional", "security", "accessibility"},
        )

    def test_lint_reports_each_required_failure_category(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas = root / "personas"
            personas.mkdir()
            for persona_id, checks in (
                ("one", ["static.inventory"]),
                ("two", ["static.inventory"]),
                ("unknown", ["static.does_not_exist"]),
                ("schema", ["static.tests"]),
            ):
                code, payload, _ = run_json(
                    [
                        "personas",
                        "scaffold",
                        persona_id,
                        "--personas-dir",
                        str(personas),
                        "--root",
                        str(root),
                        "--check",
                        *checks,
                    ]
                )
                self.assertEqual(code, 0, payload)

            schema_path = personas / "schema.md"
            schema_text = schema_path.read_text(encoding="utf-8")
            schema_text = schema_text.replace('  "severity",\n', "", 1)
            schema_path.write_text(schema_text, encoding="utf-8")

            broken_path = personas / "broken.md"
            scaffold_persona = load_persona(personas / "one.md")
            self.assertTrue(scaffold_persona.checks)
            broken_text = (personas / "unknown.md").read_text(encoding="utf-8")
            broken_text = re.sub(
                r"## Blocker schema.*?(?=## Refusal handling)",
                "",
                broken_text,
                flags=re.DOTALL,
            )
            broken_path.write_text(broken_text, encoding="utf-8")

            code, payload, _ = run_json(
                [
                    "personas",
                    "lint",
                    "--personas-dir",
                    str(personas),
                    "--root",
                    str(root),
                ]
            )
            errors = payload["errors"]
            codes = {error["code"] for error in errors}

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "invalid")
        self.assertIn("missing_section", codes)
        self.assertIn("unknown_check", codes)
        self.assertIn("duplicate_ownership", codes)
        self.assertIn("schema_required_field_missing", codes)
        duplicate = next(
            error for error in errors if error["code"] == "duplicate_ownership"
        )
        self.assertEqual(duplicate["check_id"], "static.inventory")
        self.assertEqual(duplicate["owners"], ["one", "two"])
        schema_error = next(
            error
            for error in errors
            if error["code"] == "schema_required_field_missing"
        )
        self.assertEqual(schema_error["schema"], "output")
        self.assertEqual(schema_error["field"], "severity")
        self.assertEqual(payload["summary"]["errors"], len(errors))

    def test_default_personas_command_still_lists_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, payload, _ = run_json(
                [
                    "personas",
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root),
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["schema_version"], "quality-harness.personas/v1")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["personas"][0]["id"], "accessibility")


class PersonaScaffoldTests(unittest.TestCase):
    def test_scaffold_writes_a_contract_that_passes_lint_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas = root / "personas"
            shutil.copytree(PERSONAS, personas)

            code, payload, _ = run_json(
                [
                    "personas",
                    "scaffold",
                    "reliability",
                    "--personas-dir",
                    str(personas),
                    "--root",
                    str(root),
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(payload["schema_version"], "quality-harness.personas.scaffold/v1")
            self.assertEqual(payload["status"], "created")
            self.assertEqual(payload["persona_id"], "reliability")
            self.assertEqual(payload["checks"], ["plugin.reliability.placeholder"])
            scaffolded = Path(payload["path"])
            self.assertTrue(scaffolded.exists())

            persona = load_persona(scaffolded)
            self.assertEqual(persona.id, "reliability")
            self.assertEqual(persona.domains, ["reliability"])
            self.assertEqual(persona.checks, ["plugin.reliability.placeholder"])
            self.assertTrue(persona.output_schema["required"])
            self.assertTrue(persona.blocker_schema["required"])

            lint_code, lint_payload, _ = run_json(
                [
                    "personas",
                    "lint",
                    "--personas-dir",
                    str(personas),
                    "--root",
                    str(root),
                ]
            )

        self.assertEqual(lint_code, 0)
        self.assertEqual(lint_payload["status"], "valid")
        self.assertEqual(lint_payload["errors"], [])
        warning_codes = {warning["code"] for warning in lint_payload["warnings"]}
        self.assertIn("plugin_check_placeholder", warning_codes)
        self.assertEqual(lint_payload["summary"]["valid_personas"], 4)

    def test_scaffold_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas = root / "personas"
            personas.mkdir()
            arguments = [
                "personas",
                "scaffold",
                "reliability",
                "--personas-dir",
                str(personas),
                "--root",
                str(root),
            ]

            code, created, _ = run_json(arguments)
            self.assertEqual(code, 0)
            path = Path(created["path"])
            original = path.read_text(encoding="utf-8")

            code, refused, _ = run_json(arguments)
            self.assertEqual(code, 2)
            self.assertEqual(refused["status"], "refused")
            self.assertIn("--force", refused["reason"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

            code, overwritten, _ = run_json(arguments + ["--force"])
            self.assertEqual(code, 0)
            self.assertEqual(overwritten["status"], "overwritten")
            self.assertTrue(overwritten["force"])

    def test_scaffold_accepts_domains_and_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas = root / "personas"
            personas.mkdir()
            code, payload, _ = run_json(
                [
                    "personas",
                    "scaffold",
                    "performance",
                    "--personas-dir",
                    str(personas),
                    "--root",
                    str(root),
                    "--name",
                    "Performance Specialist",
                    "--domain",
                    "performance",
                    "--domain",
                    "reliability",
                    "--check",
                    "plugin.performance.load",
                ]
            )
            persona = load_persona(Path(payload["path"]))

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["domains"], ["performance", "reliability"])
        self.assertEqual(payload["checks"], ["plugin.performance.load"])
        self.assertEqual(persona.name, "Performance Specialist")
        self.assertEqual(persona.domains, ["performance", "reliability"])
        self.assertEqual(persona.checks, ["plugin.performance.load"])


if __name__ == "__main__":
    unittest.main()
