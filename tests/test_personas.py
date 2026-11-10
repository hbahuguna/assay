"""Persona contract parsing and schema validation tests."""

import tempfile
import unittest
from pathlib import Path

from harness.checks import catalog_ids
from harness.errors import PersonaError
from harness.personas import (
    load_persona,
    load_personas,
    validate_payload,
    validate_persona_checks,
)

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"


def valid_finding():
    return {
        "title": "A concrete barrier",
        "severity": "medium",
        "domain": "functional",
        "strength": "observed",
        "consequence": "A customer cannot complete the task.",
        "location": "https://example.test/",
        "reproduction": "Open the target and follow the cited flow.",
        "evidence": [".harness/evidence/run/screenshot.png"],
        "remediation": "Fix the control and add a regression check.",
        "verification": "Re-run the check and confirm the flow succeeds.",
        "tags": ["example"],
    }


class PersonaTests(unittest.TestCase):
    def test_loads_the_three_specialist_contracts(self):
        personas = load_personas(PERSONAS, require_minimum=3)
        self.assertEqual(
            {persona.id for persona in personas},
            {"functional", "security", "accessibility"},
        )
        for persona in personas:
            self.assertTrue(persona.domains)
            self.assertTrue(persona.tools)
            self.assertTrue(persona.checks)
            self.assertEqual(persona.output_schema["type"], "object")
            self.assertTrue(persona.output_schema["required"])
            self.assertTrue(persona.output_schema["properties"])

    def test_every_referenced_check_exists_in_the_catalog(self):
        personas = load_personas(PERSONAS, require_minimum=3)
        validate_persona_checks(personas, catalog_ids())

    def test_one_check_is_not_claimed_by_two_personas(self):
        personas = load_personas(PERSONAS, require_minimum=3)
        owners = {}
        for persona in personas:
            for check_id in persona.checks:
                self.assertNotIn(check_id, owners, "{} is claimed twice".format(check_id))
                owners[check_id] = persona.id
        self.assertEqual(len(owners), len(catalog_ids()))

    def test_valid_payload_passes_each_specialist_schema(self):
        for persona in load_personas(PERSONAS, require_minimum=3):
            payload = valid_finding()
            payload["domain"] = persona.domains[0]
            errors = validate_payload(payload, persona.output_schema)
            self.assertEqual(errors, [], "{} rejected a valid payload: {}".format(persona.id, errors))

    def test_payload_missing_required_fields_is_rejected(self):
        persona = load_persona(PERSONAS / "security.md")
        payload = valid_finding()
        payload["domain"] = "security"
        del payload["remediation"]
        errors = validate_payload(payload, persona.output_schema)
        self.assertTrue(any("remediation" in error for error in errors))

    def test_out_of_contract_severity_is_rejected(self):
        persona = load_persona(PERSONAS / "accessibility.md")
        payload = valid_finding()
        payload["domain"] = "accessibility"
        payload["severity"] = "blocker"
        errors = validate_payload(payload, persona.output_schema)
        self.assertTrue(any("not one of" in error for error in errors))

    def test_missing_required_section_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.md"
            path.write_text(
                "---\n"
                "id: broken\n"
                "name: Broken\n"
                "domains: [functional]\n"
                "tools: [nothing]\n"
                "checks: [static.inventory]\n"
                "---\n\n"
                "## Role\n\nText.\n\n"
                "## Available tools\n\nNone.\n\n"
                "## Output schema\n\n```json\n"
                '{"type": "object", "required": ["title"], "properties": {"title": {"type": "string"}}}\n'
                "```\n",
                encoding="utf-8",
            )
            with self.assertRaises(PersonaError):
                load_persona(path)


if __name__ == "__main__":
    unittest.main()
