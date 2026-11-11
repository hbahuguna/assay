"""Optional browser capture and redaction tests."""

import http.server
import json
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONAS = ROOT / "personas"
SENSITIVE_VALUE = "not-a-real-token-value"

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
        super().__init__(*args, directory=str(kwargs.pop("directory")), **kwargs)

    def log_message(self, format, *args):
        return


def _serve(directory):
    handler = lambda *args, **kwargs: _QuietHandler(
        *args, directory=str(directory), **kwargs
    )
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    httpd.allow_reuse_address = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _run_harness(root, url, extra_args=None):
    command = [
        sys.executable,
        "-m",
        "harness",
        "run",
        "--target",
        url,
        "--source",
        str(root / "site"),
        "--personas-dir",
        str(PERSONAS),
        "--root",
        str(root),
    ]
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
class BrowserCaptureTests(unittest.TestCase):
    def _site(self, root, html):
        site = root / "site"
        site.mkdir()
        (site / "index.html").write_text(html, encoding="utf-8")
        return site

    def test_capture_flags_record_redacted_har_trace_and_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = (
                "<!doctype html><html lang='en'><head><title>Capture fixture</title></head>"
                "<body><main><h1>Capture fixture</h1><p>Enough visible content.</p>"
                "<img src='/asset.png?token={}' alt='tokenized asset'></main></body></html>"
            ).format(SENSITIVE_VALUE)
            self._site(root, html)
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(
                    root,
                    url,
                    [
                        "--browser-har",
                        "--browser-trace",
                        "--browser-video",
                    ],
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
            evidence_dir = root / ".harness" / "evidence"
            run_dirs = [path for path in evidence_dir.iterdir() if path.is_dir()]
            self.assertTrue(run_dirs)
            manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
            kinds = {entry["kind"] for entry in manifest["evidence"]}
            self.assertIn("har", kinds)
            self.assertIn("trace", kinds)
            self.assertIn("video", kinds)
            for entry in manifest["evidence"]:
                self.assertGreater(entry["bytes"], 0)
                self.assertTrue(entry["sha256"])

            serialized = ""
            serialized_bytes = b""
            for path in (root / ".harness").rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    serialized_bytes += data
                    serialized += data.decode("utf-8", errors="ignore")
            self.assertNotIn(SENSITIVE_VALUE.encode("utf-8"), serialized_bytes)
            self.assertIn("[REDACTED", serialized)

    def test_capture_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._site(
                root,
                "<!doctype html><html lang='en'><head><title>Capture fixture</title></head>"
                "<body><main><h1>Capture fixture</h1><p>Enough visible content.</p></main></body></html>",
            )
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(root, url)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "completed")
            evidence_dir = root / ".harness" / "evidence"
            run_dirs = [path for path in evidence_dir.iterdir() if path.is_dir()]
            self.assertTrue(run_dirs)
            manifest = json.loads((run_dirs[0] / "manifest.json").read_text(encoding="utf-8"))
            kinds = {entry["kind"] for entry in manifest["evidence"]}
            self.assertNotIn("har", kinds)
            self.assertNotIn("trace", kinds)
            self.assertNotIn("video", kinds)


if __name__ == "__main__":
    unittest.main()
