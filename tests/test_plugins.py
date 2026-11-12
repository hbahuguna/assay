"""Plugin registry, signature, ownership, and blocked-failure tests."""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from harness.checks import CHECK_CATALOG, catalog_ids, plan_checks
from harness.errors import PluginError
from harness.models import CheckStatus, Target
from harness.orchestrator import Orchestrator, RunOptions
from harness.personas import load_personas
from harness.plugins import (
    ENTRY_POINT_GROUP,
    CheckRegistry,
    build_registry,
    load_entry_points,
    sign_plugin,
)
from harness.static_analysis import Inventory

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SAMPLE = ROOT / "examples" / "sample-repo"
SAMPLE_PLUGIN = ROOT / "examples" / "plugins" / "sample_check.py"
SECRET = "synthetic-plugin-signing-secret"


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


def write_trust_store(root: Path, key_id: str = "test-key", secret: str = SECRET) -> None:
    path = Path(root) / ".harness" / "plugins" / "trusted_keys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"keys": {key_id: secret}}), encoding="utf-8")


def personas_with_plugin(root: Path, check_id: str = "example.package_metadata") -> Path:
    destination = Path(root) / "personas"
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(PERSONAS.glob("*.md")):
        text = source.read_text(encoding="utf-8")
        if source.name == "functional.md":
            marker = "  - browser.links\n"
            if marker not in text:
                raise AssertionError("functional persona check marker not found")
            text = text.replace(marker, marker + "  - {}\n".format(check_id))
        (destination / source.name).write_text(text, encoding="utf-8")
    return destination


class RegistryTests(unittest.TestCase):
    def test_builtins_are_discovered_through_the_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = build_registry(Path(directory), entry_points=[])
        self.assertEqual(set(registry.ids()), set(catalog_ids()))
        self.assertEqual(registry.source("static.secrets"), "builtin")
        self.assertEqual(registry.builtin_count(), len(CHECK_CATALOG))
        self.assertEqual(registry.plugin_count(), 0)
        planned = plan_checks(
            target=Target(
                raw=".", kind="directory", resolved=str(SAMPLE), source=str(SAMPLE)
            ),
            inventory=Inventory(
                available=True,
                root=str(SAMPLE),
                source_files=["app.py"],
                markup_files=["page.html"],
                manifests=["pyproject.toml"],
            ).to_dict(),
            personas=load_personas(PERSONAS),
            max_checks=20,
            no_browser=True,
            registry=registry,
        )
        self.assertIn("static.secrets", [check.id for check in planned])
        self.assertEqual(
            {check.id for check in planned},
            set(registry.ids())
            - {
                "static.full_assessment",
                "static.test_category",
                "browser.load",
                "browser.console",
                "browser.network",
                "browser.security",
                "browser.accessibility",
                "browser.content",
                "browser.links",
                "browser.overflow",
                "browser.focus_order",
            },
        )

    def test_entry_point_group_is_used_for_both_metadata_apis(self):
        entry = EntryPointStub("sample_check", "sample_check:PLUGIN", lambda: None)
        with patch("harness.plugins.importlib_metadata.entry_points") as discovered:
            discovered.return_value = {ENTRY_POINT_GROUP: [entry]}
            self.assertEqual(load_entry_points(), [entry])
            discovered.return_value = {"other.group": []}
            self.assertEqual(load_entry_points(), [])

        class Selectable:
            def __init__(self, entries):
                self._entries = entries

            def select(self, group):
                return [item for item in self._entries if group == ENTRY_POINT_GROUP]

        with patch("harness.plugins.importlib_metadata.entry_points") as discovered:
            discovered.return_value = Selectable([entry])
            self.assertEqual(load_entry_points(), [entry])

    def test_duplicate_unknown_input_and_zero_weight_are_rejected(self):
        registry = CheckRegistry()
        registry.register_builtins()
        baseline = CHECK_CATALOG[0]
        with self.assertRaises(PluginError):
            registry.register(baseline, trusted=True)
        with self.assertRaises(PluginError):
            registry.register(
                replace(baseline, id="plugin.unknown_input", requires="database"),
                trusted=True,
            )
        with self.assertRaises(PluginError):
            registry.register(
                replace(baseline, id="plugin.zero_weight", base_weight=0),
                trusted=True,
            )

    def test_unsigned_plugin_becomes_a_blocked_definition(self):
        plugin = load_sample_plugin().PLUGIN
        entry = EntryPointStub("sample_check", "sample_check:PLUGIN", lambda: plugin)
        with tempfile.TemporaryDirectory() as directory:
            registry = build_registry(Path(directory), entry_points=[entry])
        self.assertEqual(registry.plugin_count(), 0)
        self.assertIn("example.package_metadata", registry.ids())
        self.assertIn("unsigned", registry.blocked_reason("example.package_metadata").lower())

    def test_invalid_signature_is_rejected(self):
        plugin = load_sample_plugin().PLUGIN
        plugin.signature = sign_plugin(
            plugin.definition, "sample_check:PLUGIN", "test-key", "wrong-secret"
        )
        entry = EntryPointStub("sample_check", "sample_check:PLUGIN", lambda: plugin)
        with tempfile.TemporaryDirectory() as directory:
            write_trust_store(Path(directory))
            registry = build_registry(Path(directory), entry_points=[entry])
        self.assertIn("did not verify", registry.blocked_reason("example.package_metadata"))


class PluginExecutionTests(unittest.TestCase):
    def _signed_entry(self):
        module = load_sample_plugin()
        plugin = module.PLUGIN
        plugin.signature = sign_plugin(
            plugin.definition, "sample_check:PLUGIN", "test-key", SECRET
        )
        return plugin

    def test_sample_plugin_adds_and_executes_a_check(self):
        plugin = self._signed_entry()
        entry = EntryPointStub("sample_check", "sample_check:PLUGIN", lambda: plugin)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trust_store(root)
            personas = personas_with_plugin(root)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(personas),
                no_browser=True,
            )
            with patch("harness.plugins.load_entry_points", return_value=[entry]):
                state = Orchestrator(root, options).run()

            self.assertEqual(state.status, "completed")
            plugin_check = state.check("example.package_metadata")
            self.assertIsNotNone(plugin_check)
            self.assertEqual(plugin_check.status, CheckStatus.PASSED.value)
            self.assertTrue(plugin_check.evidence)
            manifest = json.loads(
                (root / ".harness" / "evidence" / state.run_id / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(
                any(
                    entry.get("check_id") == "example.package_metadata"
                    for entry in manifest.get("evidence", [])
                )
            )

    def test_plugin_not_owned_by_persona_becomes_blocked(self):
        plugin = self._signed_entry()
        entry = EntryPointStub("sample_check", "sample_check:PLUGIN", lambda: plugin)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_trust_store(root)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
            )
            with patch("harness.plugins.load_entry_points", return_value=[entry]):
                state = Orchestrator(root, options).run()

            self.assertEqual(state.status, "completed")
            plugin_check = state.check("example.package_metadata")
            self.assertEqual(plugin_check.status, CheckStatus.BLOCKED.value)
            self.assertIn("declared persona", plugin_check.actual)

    def test_import_failure_becomes_a_blocked_check_not_a_failed_run(self):
        def broken_loader():
            raise ImportError("synthetic missing dependency")

        entry = EntryPointStub("broken_plugin", "broken_plugin:PLUGIN", broken_loader)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(PERSONAS),
                no_browser=True,
            )
            with patch("harness.plugins.load_entry_points", return_value=[entry]):
                state = Orchestrator(root, options).run()

            self.assertEqual(state.status, "completed")
            blocked = [
                check
                for check in state.checks
                if check.id.startswith("plugin.load.broken")
            ]
            self.assertEqual(len(blocked), 1)
            self.assertEqual(blocked[0].status, CheckStatus.BLOCKED.value)
            self.assertIn("could not be imported", blocked[0].actual)

    def test_import_failure_for_persona_owned_check_is_blocked_not_fatal(self):
        def broken_loader():
            raise ImportError("synthetic missing dependency")

        entry = EntryPointStub("sample_check", "sample_check:PLUGIN", broken_loader)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personas = personas_with_plugin(root)
            options = RunOptions(
                target=str(SAMPLE),
                personas_dir=str(personas),
                no_browser=True,
            )
            with patch("harness.plugins.load_entry_points", return_value=[entry]):
                state = Orchestrator(root, options).run()

            self.assertEqual(state.status, "completed")
            plugin_check = state.check("example.package_metadata")
            self.assertIsNotNone(plugin_check)
            self.assertEqual(plugin_check.status, CheckStatus.BLOCKED.value)
            self.assertIn("failed to load or verify", plugin_check.actual)


if __name__ == "__main__":
    unittest.main()
