"""Redaction regression corpus tests."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import cli
from harness.orchestrator import Orchestrator, RunOptions
from harness.static_analysis import redact as original_redact

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "tests" / "fixtures" / "redaction_corpus.json"


class RedactionCorpusTests(unittest.TestCase):
    def _corpus_values(self):
        corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(corpus["entries"]), 20)
        return [entry["value"] for entry in corpus["entries"]]

    def _fixture_target(self, root: Path, values):
        target = root / "target"
        target.mkdir()
        (target / "pyproject.toml").write_text(
            "[project]\nname='redaction-fixture'\n", encoding="utf-8"
        )
        (target / "secrets.py").write_text("\n".join(values) + "\n", encoding="utf-8")
        return target

    def _serialized_artifacts(self, root: Path, stdout: str, stderr: str) -> str:
        serialized = stdout + stderr
        harness_dir = root / ".harness"
        if harness_dir.exists():
            for path in harness_dir.rglob("*"):
                if path.is_file():
                    serialized += path.read_text(encoding="utf-8", errors="replace")
        return serialized

    def _run_fixture(self, root: Path, values, redactor=None):
        target = self._fixture_target(root, values)
        if redactor is not None:
            original = __import__("harness.static_analysis", fromlist=["redact"]).redact
            __import__("harness.static_analysis", fromlist=["redact"]).redact = redactor
        try:
            options = RunOptions(
                target=str(target),
                personas_dir=str(ROOT / "personas"),
                no_browser=True,
                kill_on="never",
            )
            state = Orchestrator(root, options).run()
        finally:
            if redactor is not None:
                __import__("harness.static_analysis", fromlist=["redact"]).redact = original_redact
        return state

    def test_corpus_values_do_not_reach_artifacts(self):
        values = self._corpus_values()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout_buffer = __import__("io").StringIO()
            stderr_buffer = __import__("io").StringIO()
            import contextlib

            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                state = self._run_fixture(root, values)
            self.assertIsNotNone(state)
            serialized = self._serialized_artifacts(
                root, stdout_buffer.getvalue(), stderr_buffer.getvalue()
            )
            for value in values:
                self.assertNotIn(
                    value,
                    serialized,
                    "redaction corpus value leaked into artifacts",
                )
            self.assertIn("[REDACTED", serialized)

    def test_broken_redactor_leaks_a_corpus_value(self):
        values = self._corpus_values()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout_buffer = __import__("io").StringIO()
            stderr_buffer = __import__("io").StringIO()
            import contextlib

            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                state = self._run_fixture(root, values, redactor=lambda value: value)
            self.assertIsNotNone(state)
            serialized = self._serialized_artifacts(
                root, stdout_buffer.getvalue(), stderr_buffer.getvalue()
            )
            leaked = [value for value in values if value in serialized]
            self.assertTrue(
                leaked,
                "broken redactor did not leak any corpus value; test is not exercising redaction",
            )
            # The failure message above intentionally omits the leaked value.

    def test_corpus_is_safe_and_large_enough(self):
        corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(corpus["entries"]), 20)
        for entry in corpus["entries"]:
            self.assertIn("id", entry)
            self.assertIn("value", entry)
            self.assertTrue(str(entry["value"]).strip())


if __name__ == "__main__":
    unittest.main()
