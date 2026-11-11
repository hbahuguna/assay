"""Check ownership is derived from persona contracts, not check definitions."""

import shutil
import tempfile
import unittest
from pathlib import Path

from harness.checks import CHECK_CATALOG, plan_checks
from harness.errors import HarnessError, PersonaError
from harness.models import Target
from harness.orchestrator import Orchestrator, RunOptions
from harness.personas import load_personas
from harness.plugins import build_registry
from harness.static_analysis import Inventory

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"


def copy_personas(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(PERSONAS.glob("*.md")):
        shutil.copyfile(source, destination / source.name)
    return destination


def remove_check_from_all(personas_dir, check_id):
    needle = "  - {}\n".format(check_id)
    for path in sorted(personas_dir.glob("*.md")):
        if path.name.lower().startswith("readme"):
            continue
        text = path.read_text()
        path.write_text(text.replace(needle, ""))


def add_check_to_persona(path, check_id):
    text = path.read_text()
    marker = "checks:\n"
    if marker not in text:
        raise AssertionError("persona has no checks frontmatter")
    text = text.replace(marker, marker + "  - {}\n".format(check_id), 1)
    path.write_text(text)


def sample_target():
    return Target(
        raw=str(SAMPLE),
        kind="directory",
        resolved=str(SAMPLE),
        source=str(SAMPLE),
    )


def sample_inventory():
    return Inventory(
        available=True,
        root=str(SAMPLE),
        source_files=["app.py"],
        markup_files=["index.html"],
        manifests=["pyproject.toml"],
    ).to_dict()


class OwnershipTests(unittest.TestCase):
    def test_builtin_catalog_has_no_persona_assignment(self):
        self.assertTrue(all(definition.persona == "" for definition in CHECK_CATALOG))

    def test_unowned_builtin_check_fails_planning_with_the_check_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas_dir = copy_personas(root / "personas")
            remove_check_from_all(personas_dir, "static.inventory")
            personas = load_personas(personas_dir)
            registry = build_registry(root / "project", entry_points=[])

            with self.assertRaises(HarnessError) as context:
                plan_checks(
                    sample_target(),
                    sample_inventory(),
                    personas,
                    max_checks=20,
                    no_browser=True,
                    registry=registry,
                )

            message = str(context.exception)
            self.assertIn("static.inventory", message)
            self.assertIn("exactly one persona contract", message)

    def test_unowned_builtin_check_fails_before_execution_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas_dir = copy_personas(root / "personas")
            remove_check_from_all(personas_dir, "static.inventory")
            project = root / "project"
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(personas_dir),
                no_browser=True,
            )

            with self.assertRaises(PersonaError) as context:
                Orchestrator(project, options).run()

            self.assertIn("static.inventory", str(context.exception))
            self.assertFalse((project / ".harness" / "progress.json").exists())

    def test_duplicate_ownership_fails_validation_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas_dir = copy_personas(root / "personas")
            add_check_to_persona(personas_dir / "security.md", "static.inventory")
            personas = load_personas(personas_dir)
            registry = build_registry(root / "project", entry_points=[])

            with self.assertRaises(PersonaError) as context:
                registry.validate_ownership(personas)

            message = str(context.exception)
            self.assertIn("static.inventory", message)
            self.assertIn("functional", message)
            self.assertIn("security", message)

            project = root / "project"
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(personas_dir),
                no_browser=True,
            )
            with self.assertRaises(PersonaError):
                Orchestrator(project, options).run()
            self.assertFalse((project / ".harness" / "progress.json").exists())

    def test_planning_uses_persona_frontmatter_as_the_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = build_registry(root / "project", entry_points=[])
            personas = load_personas(PERSONAS)
            planned = plan_checks(
                sample_target(),
                sample_inventory(),
                personas,
                max_checks=20,
                no_browser=True,
                registry=registry,
            )

            owners = {}
            for persona in personas:
                for check_id in persona.checks:
                    owners[check_id] = persona.id

            self.assertTrue(planned)
            for check in planned:
                self.assertEqual(check.persona, owners[check.id])

    def test_selected_persona_plan_uses_all_contracts_for_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = build_registry(root / "project", entry_points=[])
            all_personas = load_personas(PERSONAS)
            selected = [persona for persona in all_personas if persona.id == "accessibility"]
            planned = plan_checks(
                sample_target(),
                sample_inventory(),
                selected,
                max_checks=20,
                no_browser=True,
                registry=registry,
                all_personas=all_personas,
            )

            self.assertTrue(planned)
            self.assertTrue(all(check.persona == "accessibility" for check in planned))


if __name__ == "__main__":
    unittest.main()
