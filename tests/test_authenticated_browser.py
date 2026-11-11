"""Authenticated browser storage-state tests."""

import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SECRET = "not-a-real-session-secret"

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright_runtime:
        browser = playwright_runtime.chromium.launch(headless=True)
        browser.close()
    PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - environment dependent
    PLAYWRIGHT_AVAILABLE = False


class _AuthHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _cookie_value(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" in part:
                key, value = part.strip().split("=", 1)
                if key == name:
                    return value
        return None

    def do_GET(self):
        if self.path == "/login":
            body = b"logged in"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Set-Cookie",
                "session={}; HttpOnly; Path=/; SameSite=Lax".format(SECRET),
            )
            self.end_headers()
            self.wfile.write(body)
            return

        if self._cookie_value("session") == SECRET:
            body = (
                "<!doctype html><html lang='en'><head><title>Protected page</title></head>"
                "<body><main><h1>Protected page</h1><p>Authenticated content.</p></main></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = b"authentication required"
        self.send_response(401)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _serve():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _AuthHandler)
    httpd.allow_reuse_address = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _run_harness(root, url, extra_args=None, source=None):
    command = [
        sys.executable,
        "-m",
        "harness",
        "run",
        "--target",
        url,
        "--personas-dir",
        str(PERSONAS),
        "--root",
        str(root),
    ]
    if source is not None:
        command.extend(["--source", str(source)])
    if extra_args:
        command.extend(extra_args)
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright and Chromium are not installed")
class AuthenticatedBrowserTests(unittest.TestCase):
    def _login(self, url, state_path):
        with sync_playwright() as playwright_runtime:
            browser = playwright_runtime.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(url + "login")
            context.storage_state(path=str(state_path))
            browser.close()

    def test_storage_state_enables_authenticated_page_and_redacts_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            httpd, thread = _serve()
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            state_path = root / "storage-state.json"
            try:
                self._login(url, state_path)
                completed = _run_harness(
                    root,
                    url,
                    ["--storage-state", str(state_path)],
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(
                completed.returncode,
                0,
                "harness exited {}: {}".format(completed.returncode, completed.stderr),
            )
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "completed")
            browser_load = next(
                check for check in report["checks"] if check["id"] == "browser.load"
            )
            self.assertEqual(browser_load["status"], "passed")

            serialized = b""
            for path in (root / ".harness").rglob("*"):
                if path.is_file():
                    serialized += path.read_bytes()
            self.assertNotIn(SECRET.encode("utf-8"), serialized)
            self.assertIn(b"[REDACTED", serialized)

    def test_missing_storage_state_blocks_browser_checks_but_static_checks_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "site"
            source.mkdir()
            (source / "index.html").write_text(
                "<!doctype html><html lang='en'><head><title>Auth demo</title></head>"
                "<body><main><h1>Auth demo</h1><p>Enough visible content.</p></main></body></html>",
                encoding="utf-8",
            )
            httpd, thread = _serve()
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(
                    root,
                    url,
                    ["--storage-state", str(root / "missing-state.json")],
                    source=source,
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "completed")
            browser_checks = [
                check for check in report["checks"] if check["kind"] == "browser"
            ]
            self.assertTrue(browser_checks)
            self.assertEqual(
                {check["status"] for check in browser_checks},
                {"blocked"},
            )
            self.assertTrue(
                any(
                    "Storage state file was not found" in blocker["boundary"]
                    for blocker in report["blockers"]
                )
            )
            static_checks = [
                check for check in report["checks"] if check["kind"] == "static"
            ]
            self.assertTrue(
                any(check["status"] == "passed" for check in static_checks),
                "anonymous static checks did not run",
            )

    def test_expired_storage_state_blocks_browser_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "expired-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "name": "session",
                                "value": SECRET,
                                "domain": "127.0.0.1",
                                "path": "/",
                                "expires": 1,
                                "httpOnly": True,
                                "secure": False,
                                "sameSite": "Lax",
                            }
                        ],
                        "origins": [],
                    }
                ),
                encoding="utf-8",
            )
            httpd, thread = _serve()
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(
                    root,
                    url,
                    ["--storage-state", str(state_path)],
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            browser_checks = [
                check for check in report["checks"] if check["kind"] == "browser"
            ]
            self.assertTrue(browser_checks)
            self.assertEqual(
                {check["status"] for check in browser_checks},
                {"blocked"},
            )
            self.assertTrue(
                any("expired" in blocker["boundary"].lower() for blocker in report["blockers"])
            )

    def test_storage_state_can_be_loaded_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {
                "cookies": [
                    {
                        "name": "session",
                        "value": SECRET,
                        "domain": "127.0.0.1",
                        "path": "/",
                        "expires": -1,
                        "httpOnly": True,
                        "secure": False,
                        "sameSite": "Lax",
                    }
                ],
                "origins": [],
            }
            httpd, thread = _serve()
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            env = dict(os.environ)
            env["AUTH_STATE"] = json.dumps(state)
            try:
                command = [
                    sys.executable,
                    "-m",
                    "harness",
                    "run",
                    "--target",
                    url,
                    "--personas-dir",
                    str(PERSONAS),
                    "--root",
                    str(root),
                    "--storage-state",
                    "env:AUTH_STATE",
                ]
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    env=env,
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            browser_load = next(
                check for check in report["checks"] if check["id"] == "browser.load"
            )
            self.assertEqual(browser_load["status"], "passed")
            serialized = b""
            for path in (root / ".harness").rglob("*"):
                if path.is_file():
                    serialized += path.read_bytes()
            self.assertNotIn(SECRET.encode("utf-8"), serialized)


if __name__ == "__main__":
    unittest.main()
