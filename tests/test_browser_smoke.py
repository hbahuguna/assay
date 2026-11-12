"""Browser smoke test for the local demo-web fixture.

This test is skipped when Playwright or Chromium is unavailable.  It never
reaches the public internet: it serves ``examples/demo-web`` on localhost and
runs the harness against that local URL.
"""

import http.server
import json
import socketserver
import subprocess
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo-web"
PERSONAS = ROOT / "personas"

try:
    import playwright  # noqa: F401
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright_runtime:
        browser = playwright_runtime.chromium.launch(headless=True)
        browser.close()
    PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    PLAYWRIGHT_AVAILABLE = False


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DEMO), **kwargs)

    def log_message(self, format, *args):
        return


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright and Chromium are not installed")
class BrowserSmokeTests(unittest.TestCase):
    def test_browser_checks_execute_against_local_demo(self):
        httpd = socketserver.TCPServer(("127.0.0.1", 0), _QuietHandler)
        httpd.allow_reuse_address = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        port = httpd.server_address[1]
        root = None
        try:
            import tempfile

            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                command = [
                    sys.executable,
                    "-m",
                    "harness",
                    "run",
                    "--target",
                    "http://127.0.0.1:{}/".format(port),
                    "--source",
                    str(DEMO),
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root),
                ]
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env={**__import__("os").environ, "QH_SSRF_ALLOWLIST": "127.0.0.0/8,localhost"},
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    "harness exited {}: {}".format(completed.returncode, completed.stderr),
                )
                report = json.loads(completed.stdout)
                self.assertEqual(report["status"], "completed")
                checks = {check["id"]: check for check in report["checks"]}
                self.assertIn("browser.load", checks)
                self.assertNotEqual(
                    checks["browser.load"]["status"],
                    "blocked",
                    "browser.load was blocked: {}".format(checks["browser.load"].get("actual")),
                )

                evidence_dir = root / ".harness" / "evidence"
                self.assertTrue(evidence_dir.is_dir())
                run_dirs = [path for path in evidence_dir.iterdir() if path.is_dir()]
                self.assertTrue(run_dirs, "no evidence run directory was created")
                evidence_files = [
                    path.name for path in run_dirs[0].rglob("*") if path.is_file()
                ]
                self.assertTrue(
                    any(name.endswith(".png") for name in evidence_files),
                    "no screenshot artifact was captured",
                )
                self.assertTrue(
                    any("console-log" in name for name in evidence_files),
                    "no console log artifact was captured",
                )
                self.assertTrue(
                    any("network-log" in name for name in evidence_files),
                    "no network log artifact was captured",
                )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
