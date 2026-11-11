"""Responsive and mobile browser Operate tests."""

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
        str(root / "site") if (root / "site").exists() else str(root),
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
class MobileBrowserTests(unittest.TestCase):
    def _site(self, root, html):
        site = root / "site"
        site.mkdir()
        (site / "index.html").write_text(html, encoding="utf-8")
        return site

    def _report(self, root):
        return json.loads((root / ".harness" / "report.json").read_text(encoding="utf-8"))

    def test_mobile_pack_records_viewport_for_every_browser_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._site(root, (DEMO / "index.html").read_text(encoding="utf-8"))
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(root, url, ["--mobile"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = self._report(root)
            browser_checks = [check for check in report["checks"] if check["kind"] == "browser"]
            self.assertTrue(browser_checks)
            for check in browser_checks:
                self.assertIn("viewport", check["metadata"], check["id"])
            mobile_checks = [
                check for check in browser_checks if check["id"] in {"browser.overflow", "browser.focus_order"}
            ]
            self.assertEqual(len(mobile_checks), 2)
            self.assertEqual(
                {check["metadata"]["viewport"] for check in mobile_checks},
                {"390x844"},
            )
            desktop_checks = [
                check for check in browser_checks if check["id"] == "browser.load"
            ]
            self.assertEqual(desktop_checks[0]["metadata"]["viewport"], "1440x900")

    def test_overflow_page_fails_with_offending_element_and_screenshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html = (
                "<!doctype html><html lang='en'><head><title>Overflow fixture</title></head>"
                "<body><main><h1>Overflow fixture</h1><div style='width: 1000px;'>wide</div></main></body></html>"
            )
            self._site(root, html)
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(root, url, ["--mobile"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = self._report(root)
            overflow = next(check for check in report["checks"] if check["id"] == "browser.overflow")
            self.assertEqual(overflow["status"], "failed")
            self.assertIn("offending_element", overflow["metadata"])
            self.assertTrue(any(path.endswith(".png") for path in overflow["evidence"]))

    def test_focus_order_matches_dom_order_on_clean_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._site(root, (DEMO / "index.html").read_text(encoding="utf-8"))
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(root, url, ["--mobile"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = self._report(root)
            focus = next(check for check in report["checks"] if check["id"] == "browser.focus_order")
            self.assertEqual(focus["status"], "passed")

    def test_desktop_only_result_is_unchanged_when_mobile_pack_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._site(root, (DEMO / "index.html").read_text(encoding="utf-8"))
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                without_mobile = _run_harness(root, url)
                self.assertEqual(without_mobile.returncode, 0, without_mobile.stderr)
                report_without = self._report(root)

                root_mobile = Path(directory) / "mobile"
                root_mobile.mkdir()
                (root_mobile / "site").mkdir()
                (root_mobile / "site" / "index.html").write_text(
                    (DEMO / "index.html").read_text(encoding="utf-8"), encoding="utf-8"
                )
                httpd_mobile, thread_mobile = _serve(root_mobile / "site")
                port_mobile = httpd_mobile.server_address[1]
                url_mobile = "http://127.0.0.1:{}/".format(port_mobile)
                with_mobile = _run_harness(root_mobile, url_mobile, ["--mobile"])
                self.assertEqual(with_mobile.returncode, 0, with_mobile.stderr)
                report_with = json.loads((root_mobile / ".harness" / "report.json").read_text(encoding="utf-8"))
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                httpd_mobile.shutdown()
                httpd_mobile.server_close()
                thread_mobile.join(timeout=5)

            desktop_without = {
                check["id"]: check["status"]
                for check in report_without["checks"]
                if check["kind"] == "browser" and check["id"] not in {"browser.overflow", "browser.focus_order"}
            }
            desktop_with = {
                check["id"]: check["status"]
                for check in report_with["checks"]
                if check["kind"] == "browser" and check["id"] not in {"browser.overflow", "browser.focus_order"}
            }
            self.assertEqual(desktop_without, desktop_with)


if __name__ == "__main__":
    unittest.main()
