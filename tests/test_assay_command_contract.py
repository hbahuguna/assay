"""Contract tests for the `/assay-browser` command (QH-097).

The OpenCode plugin must never turn a browser request into a static-only run.
These tests cover the pure command builder (through Node) and the harness
planning rule that a URL target with browser execution enabled plans the real
``browser.*`` checks.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer

from harness.checks import plan_checks
from harness.models import Target
from harness.personas import load_personas

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
DEMO = ROOT / "examples" / "demo-web"
PLUGIN_TEST = ROOT / "tests" / "plugin" / "command-args.test.mjs"
PLUGIN = ROOT / ".opencode" / "plugins" / "assay" / "index.ts"
BUILDER = ROOT / ".opencode" / "plugins" / "assay" / "command-args.ts"

try:
    import playwright  # noqa: F401
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright_runtime:
        browser = playwright_runtime.chromium.launch(headless=True)
        browser.close()
    PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    PLAYWRIGHT_AVAILABLE = False


class _QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DEMO), **kwargs)

    def log_message(self, format, *args):
        return


class BrowserCommandArgsTests(unittest.TestCase):
    def test_plugin_command_builder_matches_browser_contract(self):
        node = shutil.which("node")
        if node:
            completed = subprocess.run(
                [node, "--test", str(PLUGIN_TEST)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(
                completed.returncode,
                0,
                "plugin command builder tests failed:\n{}\n{}".format(
                    completed.stdout, completed.stderr
                ),
            )
        else:  # pragma: no cover - Node is present in the verified environment
            builder = BUILDER.read_text(encoding="utf-8")
            source = PLUGIN.read_text(encoding="utf-8")
            self.assertIn("browserInvocation", builder)
            self.assertIn("needs a reachable URL", builder)
            self.assertIn("browserInvocation", source)
            self.assertIn("isBrowser", source)

    def test_plugin_does_not_force_no_browser_for_browser_command(self):
        wrapper = (ROOT / ".opencode" / "commands" / "assay-browser.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("--no-browser", wrapper)
        self.assertIn("requires a URL", wrapper)
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn("browserInvocation", source)
        self.assertIn("isBrowser", source)
        self.assertIn("browserEnv", source)


class BrowserCommandPlanningTests(unittest.TestCase):
    def _target(self):
        return Target(
            raw="http://localhost:3000",
            kind="url",
            resolved="http://localhost:3000",
        )

    def test_url_target_with_browser_enabled_plans_real_browser_checks(self):
        personas = load_personas(PERSONAS, require_minimum=3)
        checks = plan_checks(
            self._target(),
            {"available": False},
            personas,
            max_checks=0,
            no_browser=False,
            intent="Assay browser assessment",
        )
        ids = {check.id for check in checks}
        self.assertIn("browser.load", ids)
        self.assertIn("browser.console", ids)
        self.assertIn("browser.network", ids)
        self.assertIn("browser.security", ids)
        self.assertIn("browser.accessibility", ids)
        self.assertIn("browser.content", ids)
        self.assertIn("browser.links", ids)
        # Live browser checks replace the static scope marker.
        self.assertNotIn("static.browser_category", ids)
        self.assertNotIn("static.inventory", ids)

    def test_no_browser_keeps_browser_checks_out_of_scope(self):
        personas = load_personas(PERSONAS, require_minimum=3)
        checks = plan_checks(
            self._target(),
            {"available": False},
            personas,
            max_checks=0,
            no_browser=True,
            intent="Assay browser assessment",
        )
        self.assertFalse(any(check.kind == "browser" for check in checks))
        self.assertIn("static.browser_category", {check.id for check in checks})


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright and Chromium are not installed")
class BrowserCommandEndToEndTests(unittest.TestCase):
    def test_browser_command_runs_live_checks_and_never_reports_static_only(self):
        httpd = TCPServer(("127.0.0.1", 0), _QuietHandler)
        httpd.allow_reuse_address = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = [
                    sys.executable,
                    "-m",
                    "harness",
                    "run",
                    "--target",
                    "http://127.0.0.1:{}/".format(port),
                    "--personas-dir",
                    str(PERSONAS),
                    "--intent",
                    "Assay browser assessment",
                    "--root",
                    str(root),
                ]
                completed = subprocess.run(
                    command,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env={**os.environ, "QH_SSRF_ALLOWLIST": "127.0.0.0/8,localhost"},
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    "harness exited {}: {}".format(completed.returncode, completed.stderr),
                )
                report = json.loads(completed.stdout)
                self.assertEqual(report["status"], "completed")
                checks = {check["id"]: check for check in report["checks"]}
                browser_checks = [
                    check for check in report["checks"] if check["kind"] == "browser"
                ]
                self.assertTrue(browser_checks, "no live browser checks were planned")
                for check in browser_checks:
                    self.assertNotEqual(
                        check["status"],
                        "blocked",
                        "{} was blocked: {}".format(check["id"], check.get("actual")),
                    )
                self.assertIn("browser.load", checks)
                self.assertNotIn("static.browser_category", checks)
                self.assertGreater(
                    report["confidence"]["breakdown"]["total_weight"], 0
                )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
