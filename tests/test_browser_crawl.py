"""Comprehensive same-origin browser crawl tests.

These tests prove that ``--crawl`` turns the one-page browser pack into a
multi-route assessment: every discovered route gets its own screenshot,
console/network log, and check evaluation, while destructive, external,
download, and non-HTML links stay out of scope.
"""

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

from harness.browser import (
    BrowserCheckRunner,
    BrowserOptions,
    NavigationResult,
    PageView,
)
from harness.models import CheckStatus

ROOT = Path(__file__).resolve().parents[1]
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
        "--personas-dir",
        str(PERSONAS),
        "--root",
        str(root),
    ]
    if extra_args:
        command.extend(extra_args)
    return subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=240,
        env={**os.environ, "QH_SSRF_ALLOWLIST": "127.0.0.0/8,localhost"},
    )


def _page(title, heading, links=""):
    return (
        "<!doctype html><html lang='en'><head><title>{}</title></head>"
        "<body><main><h1>{}</h1><p>Enough visible content for the crawl fixture.</p>"
        "{}"
        "</main></body></html>"
    ).format(title, heading, links)


class BrowserCrawlNetworkTests(unittest.TestCase):
    def _view(self, entries):
        runner = BrowserCheckRunner(None, BrowserOptions(), lambda check, outcome: False)
        navigation = NavigationResult(
            url="https://example.test/current",
            final_url="https://example.test/current",
        )
        view = PageView(
            options=runner.options,
            navigation=navigation,
            network_entries=entries,
            evidence_refs={},
        )
        return runner, navigation, view

    def test_route_transition_aborts_are_not_network_failures(self):
        runner, navigation, view = self._view(
            [
                {
                    "url": "https://example.test/previous-page.js",
                    "method": "GET",
                    "status": None,
                    "failed": True,
                    "failure": "net::ERR_ABORTED",
                },
                {
                    "url": "https://example.test/app.js",
                    "method": "GET",
                    "status": 200,
                    "failed": False,
                },
            ]
        )
        outcome = runner._eval_network(view, navigation)
        self.assertEqual(outcome.status, CheckStatus.PASSED.value)
        self.assertEqual(outcome.findings, [])

    def test_real_network_failure_still_fails(self):
        runner, navigation, view = self._view(
            [
                {
                    "url": "https://example.test/broken.js",
                    "method": "GET",
                    "status": None,
                    "failed": True,
                    "failure": "net::ERR_CONNECTION_REFUSED",
                }
            ]
        )
        outcome = runner._eval_network(view, navigation)
        self.assertEqual(outcome.status, CheckStatus.FAILED.value)
        self.assertEqual(len(outcome.findings), 1)


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright and Chromium are not installed")
class BrowserCrawlTests(unittest.TestCase):
    def _site(self, root):
        site = root / "site"
        site.mkdir()
        (site / "index.html").write_text(
            _page(
                "Crawl fixture home",
                "Crawl fixture home",
                "<nav>"
                "<a href='/about.html'>About</a>"
                "<a href='/pricing.html'>Pricing</a>"
                "<a href='/missing.html'>Missing</a>"
                "<a href='/logout'>Logout</a>"
                "<a href='https://example.com/external'>External</a>"
                "<a href='/manual.pdf' download>Download</a>"
                "</nav>",
            ),
            encoding="utf-8",
        )
        (site / "about.html").write_text(
            _page("About the crawl fixture", "About the crawl fixture"),
            encoding="utf-8",
        )
        (site / "pricing.html").write_text(
            _page("Pricing for the crawl fixture", "Pricing for the crawl fixture"),
            encoding="utf-8",
        )
        return site

    def _report(self, root):
        return json.loads((root / ".harness" / "report.json").read_text(encoding="utf-8"))

    def test_crawl_runs_every_check_on_each_same_origin_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._site(root)
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(
                    root,
                    url,
                    ["--crawl", "--max-pages", "5", "--mobile"],
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
            report = self._report(root)
            self.assertEqual(report["status"], "completed")
            checks = {check["id"]: check for check in report["checks"]}

            load = checks["browser.load"]
            self.assertTrue(load["metadata"].get("crawled"))
            self.assertEqual(load["metadata"]["page_count"], 4)
            routes = {entry["route"] for entry in load["metadata"]["routes"]}
            self.assertIn(url, routes)
            self.assertIn(url + "about.html", routes)
            self.assertIn(url + "pricing.html", routes)
            self.assertIn(url + "missing.html", routes)
            self.assertFalse(any("logout" in route for route in routes))
            self.assertFalse(any("example.com" in route for route in routes))
            self.assertFalse(any("manual.pdf" in route for route in routes))
            self.assertEqual(load["status"], "failed")
            self.assertTrue(
                any("HTTP 404" in finding["title"] for finding in report["findings"]),
                "the crawled 404 route was not recorded as a finding",
            )

            for check_id in (
                "browser.console",
                "browser.network",
                "browser.security",
                "browser.accessibility",
                "browser.content",
                "browser.links",
            ):
                self.assertTrue(checks[check_id]["metadata"].get("crawled"), check_id)
                self.assertGreaterEqual(
                    checks[check_id]["metadata"].get("page_count", 0), 3, check_id
                )

            mobile = checks["browser.overflow"]
            self.assertTrue(mobile["metadata"].get("crawled"))
            self.assertEqual(mobile["metadata"]["page_count"], 4)

            evidence_dir = root / ".harness" / "evidence"
            run_dir = next(path for path in evidence_dir.iterdir() if path.is_dir())
            manifest = json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )
            names = [entry["path"] for entry in manifest["evidence"]]
            self.assertTrue(
                any("route-2-" in name and name.endswith(".png") for name in names),
                "no crawled route screenshot was captured",
            )
            self.assertTrue(
                any("route-2-" in name and "console-log" in name for name in names),
                "no crawled route console log was captured",
            )
            self.assertTrue(
                any("route-2-" in name and "network-log" in name for name in names),
                "no crawled route network log was captured",
            )
            for entry in manifest["evidence"]:
                self.assertGreater(entry["bytes"], 0)
                self.assertTrue(entry["sha256"])

    def test_max_pages_one_keeps_the_single_page_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._site(root)
            httpd, thread = _serve(root / "site")
            port = httpd.server_address[1]
            url = "http://127.0.0.1:{}/".format(port)
            try:
                completed = _run_harness(
                    root,
                    url,
                    ["--crawl", "--max-pages", "1"],
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = self._report(root)
            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(checks["browser.load"]["metadata"]["page_count"], 1)
            self.assertFalse(checks["browser.load"]["metadata"].get("crawled", False))


if __name__ == "__main__":
    unittest.main()
