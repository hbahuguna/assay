"""Operate layer: Playwright browser control, evidence capture, and checks.

Playwright is an optional dependency.  If it is missing or a browser cannot be
launched, every browser check is reported as blocked with an actionable reason;
the run continues with whatever Inspect evidence exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from pathlib import Path

from .errors import BrowserUnavailable, TargetError
from .evidence import EvidenceStore
from .ssrf import validate_url, load_allowlist
from .models import Check, CheckStatus, Severity, Strength
from .outcomes import BlockerDraft, CheckOutcome

MAX_EVENTS = 500


def sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "[unparseable-url]"
    sensitive = (
        "token",
        "access_token",
        "id_token",
        "key",
        "api_key",
        "apikey",
        "secret",
        "password",
        "passwd",
        "code",
        "session",
        "sessionid",
        "jwt",
        "auth",
        "signature",
        "sig",
    )
    try:
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        query = []
    redacted = []
    for key, value in query:
        if any(token in key.lower() for token in sensitive):
            redacted.append((key, "[REDACTED]"))
        else:
            redacted.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(redacted, doseq=True), parts.fragment))


TOKEN_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b"),
    re.compile(r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)['\"]?[^\s'\"&,;]{6,}"),
]


def redact_text(value: str) -> str:
    text = str(value or "")
    for pattern in TOKEN_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: match.group(1) + "[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text[:2000]


JS_SNAPSHOT = r"""
() => {
  const text = (el) => ((el && (el.innerText || el.textContent)) || '').trim();
  const selectorFor = (el) => {
    if (!el || !el.tagName) return '';
    if (el.id) return '#' + el.id;
    let index = 0;
    let node = el;
    while (node && node.previousElementSibling) { index += 1; node = node.previousElementSibling; }
    const cls = (el.className && typeof el.className === 'string') ? '.' + el.className.trim().split(/\s+/).slice(0,2).join('.') : '';
    return el.tagName.toLowerCase() + cls + ':nth-of-type(' + (index + 1) + ')';
  };
  const explicitName = (el) => {
    const aria = (el.getAttribute('aria-label') || '').trim();
    if (aria) return aria;
    const labelledby = (el.getAttribute('aria-labelledby') || '').trim();
    if (labelledby) {
      const parts = labelledby.split(/\s+/).map(id => text(document.getElementById(id))).filter(Boolean);
      if (parts.length) return parts.join(' ');
    }
    const title = (el.getAttribute('title') || '').trim();
    if (title) return title;
    if (el.tagName === 'INPUT' && (el.type === 'submit' || el.type === 'button' || el.type === 'reset')) return (el.value || '').trim();
    if (el.tagName === 'IMG') return (el.getAttribute('alt') || '').trim();
    return text(el);
  };
  const images = Array.from(document.querySelectorAll('img')).slice(0, 200).map(el => ({
    selector: selectorFor(el),
    src: el.currentSrc || el.getAttribute('src') || '',
    altPresent: el.hasAttribute('alt'),
    alt: el.getAttribute('alt'),
    role: el.getAttribute('role') || ''
  }));
  const inputs = Array.from(document.querySelectorAll('input, select, textarea')).slice(0, 200).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    id: el.id || '',
    name: el.getAttribute('name') || '',
    selector: selectorFor(el),
    hasLabel: !!(el.labels && el.labels.length) || !!(el.getAttribute('aria-label') || '').trim() ||
              !!(el.getAttribute('aria-labelledby') || '').trim() || !!(el.getAttribute('title') || '').trim(),
    placeholder: el.getAttribute('placeholder') || ''
  }));
  const controls = Array.from(document.querySelectorAll('button, a[href], [role="button"], [role="link"]')).slice(0, 300).map(el => ({
    tag: el.tagName.toLowerCase(),
    selector: selectorFor(el),
    name: explicitName(el),
    href: el.getAttribute('href') || '',
    target: el.getAttribute('target') || '',
    rel: el.getAttribute('rel') || ''
  }));
  const forms = Array.from(document.querySelectorAll('form')).slice(0, 50).map(el => ({
    selector: selectorFor(el),
    action: el.getAttribute('action') || '',
    method: (el.getAttribute('method') || 'get').toLowerCase()
  }));
  const idCounts = {};
  Array.from(document.querySelectorAll('[id]')).forEach(el => { idCounts[el.id] = (idCounts[el.id] || 0) + 1; });
  const duplicateIds = Object.keys(idCounts).filter(id => idCounts[id] > 1).slice(0, 50);
  const positiveTabindex = Array.from(document.querySelectorAll('[tabindex]')).filter(el => {
    const value = parseInt(el.getAttribute('tabindex'), 10);
    return !isNaN(value) && value > 0;
  }).length;
  const h1s = Array.from(document.querySelectorAll('h1')).slice(0, 20).map(text);
  const headingCounts = {};
  ['h1','h2','h3','h4','h5','h6'].forEach(tag => { headingCounts[tag] = document.querySelectorAll(tag).length; });
  const scripts = Array.from(document.querySelectorAll('script[src]')).slice(0, 200).map(el => el.getAttribute('src') || '');
  const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 500).map(el => ({
    selector: selectorFor(el),
    href: el.getAttribute('href') || '',
    text: text(el).slice(0, 120),
    target: el.getAttribute('target') || '',
    rel: el.getAttribute('rel') || '',
    download: el.hasAttribute('download')
  }));
  return {
    url: location.href,
    title: document.title || '',
    lang: document.documentElement.getAttribute('lang') || '',
    bodyTextLength: (document.body && (document.body.innerText || '').trim().length) || 0,
    metaDescription: (document.querySelector('meta[name="description"]') || {}).content || '',
    images,
    inputs,
    controls,
    forms,
    duplicateIds,
    positiveTabindex,
    h1s,
    headingCounts,
    scripts,
    links,
    viewportWidth: window.innerWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
    overflowingElements: Array.from(document.querySelectorAll('*')).filter(el => {
      try { return el.getBoundingClientRect().right > window.innerWidth + 1; } catch (e) { return false; }
    }).slice(0, 5).map(el => ({
      selector: selectorFor(el),
      width: Math.round(el.getBoundingClientRect().width),
      right: Math.round(el.getBoundingClientRect().right)
    })),
    focusOrderMatchesDom: Array.from(document.querySelectorAll('[tabindex]')).every(el => parseInt(el.getAttribute('tabindex') || '0', 10) <= 0)
  };
}
"""


@dataclass
class BrowserOptions:
    headless: bool = True
    timeout_ms: int = 30000
    viewport_width: int = 1440
    viewport_height: int = 900
    capture_har: bool = False
    capture_trace: bool = False
    capture_video: bool = False
    storage_state: Optional[str] = None
    crawl: bool = False
    max_pages: int = 5


@dataclass
class NavigationResult:
    url: str
    final_url: str
    status: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    error: str = ""


@dataclass
class PageView:
    """A single captured page, used to evaluate checks across crawled routes.

    The browser evaluators historically read these attributes directly from the
    controller. Keeping the same attribute names on a per-page view lets one
    controller capture several routes while each check remains deterministic.
    """

    options: BrowserOptions
    navigation: NavigationResult
    snapshot: Dict[str, Any] = field(default_factory=dict)
    console_messages: List[Dict[str, Any]] = field(default_factory=list)
    page_errors: List[Dict[str, Any]] = field(default_factory=list)
    network_entries: List[Dict[str, Any]] = field(default_factory=list)
    cookies: List[Dict[str, Any]] = field(default_factory=list)
    evidence_refs: Dict[str, str] = field(default_factory=dict)
    label: str = "landing"


class BrowserController:
    def __init__(self, evidence: EvidenceStore, options: BrowserOptions) -> None:
        self.evidence = evidence
        self.options = options
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.console_messages: List[Dict[str, Any]] = []
        self.page_errors: List[Dict[str, Any]] = []
        self.network_entries: List[Dict[str, Any]] = []
        self.cookies: List[Dict[str, Any]] = []
        self.snapshot: Dict[str, Any] = {}
        self.navigation: Optional[NavigationResult] = None
        self.evidence_refs: Dict[str, str] = {}
        self.capture_notes: List[str] = []
        self.views: List[PageView] = []
        self.current_label: str = "landing"
        self.har_path: Optional[Path] = None
        self.trace_path: Optional[Path] = None
        self.video_dir: Optional[Path] = None
        self.video_path: Optional[Path] = None

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise BrowserUnavailable(
                "Playwright is not installed ({}). Install it with `pip install playwright` "
                "and `python -m playwright install chromium`, or run with --no-browser.".format(exc)
            )
        try:
            self.playwright = await async_playwright().start()
            try:
                self.browser = await self.playwright.chromium.launch(headless=self.options.headless)
            except Exception as launch_error:
                if self.options.headless:
                    raise
                # Interactive runs ask for a visible window, but a headless
                # environment (CI, remote agent) must still produce evidence.
                self.capture_notes.append(
                    "Headed browser launch failed; fell back to headless: {}".format(
                        redact_text(str(launch_error))
                    )
                )
                self.options.headless = True
                self.browser = await self.playwright.chromium.launch(headless=True)
            context_options: Dict[str, Any] = {
                "viewport": {"width": self.options.viewport_width, "height": self.options.viewport_height},
                "ignore_https_errors": False,
            }
            if self.options.storage_state:
                context_options["storage_state"] = self._load_storage_state(
                    self.options.storage_state
                )
            if self.options.capture_har:
                self.har_path = self.evidence.run_dir / ".capture.har"
                context_options["record_har_path"] = str(self.har_path)
                context_options["record_har_mode"] = "full"
            if self.options.capture_video:
                self.video_dir = self.evidence.run_dir / ".capture-video"
                self.video_dir.mkdir(parents=True, exist_ok=True)
                context_options["record_video_dir"] = str(self.video_dir)
                context_options["record_video_size"] = {
                    "width": self.options.viewport_width,
                    "height": self.options.viewport_height,
                }
            self.context = await self.browser.new_context(**context_options)
            self.page = await self.context.new_page()
            self.page.on("console", self._on_console)
            self.page.on("pageerror", self._on_page_error)
            self.page.on("response", self._on_response)
            self.page.on("requestfailed", self._on_request_failed)
            if self.options.capture_trace:
                self.trace_path = self.evidence.run_dir / ".capture-trace.zip"
                await self.context.tracing.start(
                    screenshots=True, snapshots=True, sources=True
                )
        except Exception as exc:
            await self.close()
            raise BrowserUnavailable(
                "Could not launch Chromium ({}). Run `python -m playwright install chromium`, "
                "or use --no-browser.".format(exc)
            )

    def _on_console(self, message: Any) -> None:
        if len(self.console_messages) >= MAX_EVENTS:
            return
        try:
            self.console_messages.append(
                {"type": message.type, "text": redact_text(message.text), "location": redact_text(str(message.location))}
            )
        except Exception:
            pass

    def _on_page_error(self, error: Any) -> None:
        if len(self.page_errors) >= MAX_EVENTS:
            return
        self.page_errors.append({"text": redact_text(str(error))})

    def _on_response(self, response: Any) -> None:
        if len(self.network_entries) >= MAX_EVENTS:
            return
        try:
            request = response.request
            self.network_entries.append(
                {
                    "url": sanitize_url(response.url),
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "status": response.status,
                    "ok": 200 <= response.status < 400,
                    "failed": False,
                }
            )
        except Exception:
            pass

    def _on_request_failed(self, request: Any) -> None:
        if len(self.network_entries) >= MAX_EVENTS:
            return
        try:
            self.network_entries.append(
                {
                    "url": sanitize_url(request.url),
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "status": None,
                    "ok": False,
                    "failed": True,
                    "failure": redact_text(str(request.failure or "request failed")),
                }
            )
        except Exception:
            pass

    async def navigate(self, url: str, label: Optional[str] = None) -> NavigationResult:
        self.current_label = label or (
            "landing" if not self.views else self._route_label(url, len(self.views) + 1)
        )
        # Each route is a separate page observation. Reset the event buffers so
        # console/network evidence is attributed to the route that produced it.
        self.console_messages = []
        self.page_errors = []
        self.network_entries = []
        self.cookies = []
        self.snapshot = {}
        self.evidence_refs = {}
        result = NavigationResult(url=url, final_url=url)
        if self.page is None:
            result.error = "Browser page is not available."
            self.navigation = result
            self.views.append(self._current_view())
            return result
        allowlist = load_allowlist()
        try:
            validate_url(url, allowlist=allowlist)
        except TargetError as exc:
            result.error = str(exc)
            self.navigation = result
            self.views.append(self._current_view())
            return result
        before_url = url
        try:
            before_url = self.page.url
            response = await self.page.goto(url, wait_until="load", timeout=self.options.timeout_ms)
            try:
                await self.page.wait_for_timeout(600)
            except Exception:
                pass
            result.status = response.status if response else None
            result.final_url = self.page.url
            if response is not None:
                # Re-validate every redirect hop and the final URL.
                request = response.request
                chain = []
                while request is not None:
                    chain.append(request.url)
                    request = request.redirected_from
                for candidate in reversed(chain):
                    validate_url(candidate, allowlist=allowlist)
                validate_url(result.final_url, allowlist=allowlist)
                try:
                    result.headers = {str(k).lower(): str(v) for k, v in (await response.all_headers()).items()}
                except Exception as exc:
                    self.capture_notes.append("Could not read response headers: {}".format(exc))
        except Exception as exc:
            result.error = redact_text(str(exc))
            current_url = self.page.url if self.page else url
            # A failed download or aborted navigation can leave the page on the
            # previous route; report the requested route instead.
            result.final_url = current_url if current_url and current_url != before_url else url
        self.navigation = result
        await self._capture(url)
        self.views.append(self._current_view())
        return result

    def discover_routes(self, seed: str, max_pages: int) -> List[str]:
        """Return bounded, same-origin GET routes discovered from the seed page.

        Only links rendered on the seed page are followed. Forms, non-HTTP
        schemes, external hosts, binary assets, API endpoints, and destructive
        actions are excluded so a crawl cannot submit data or wander off-site.
        """

        limit = max(0, int(max_pages) - 1)
        if limit <= 0 or not self.snapshot:
            return []
        try:
            seed_parts = urlsplit(seed)
        except ValueError:
            return []
        if seed_parts.scheme not in ("http", "https") or not seed_parts.netloc:
            return []
        seed_host = seed_parts.netloc.lower()
        seen = {self._normalize_route(seed)}
        routes: List[str] = []
        skip_tokens = (
            "/logout",
            "/signout",
            "/sign-out",
            "/delete",
            "/remove",
            "/unsubscribe",
            "/api/",
        )
        skip_suffixes = (
            ".pdf",
            ".zip",
            ".tar",
            ".gz",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".webp",
            ".ico",
            ".css",
            ".js",
            ".mjs",
            ".json",
            ".xml",
            ".txt",
            ".csv",
            ".mp4",
            ".webm",
            ".mp3",
            ".wav",
            ".woff",
            ".woff2",
            ".ttf",
            ".otf",
            ".pid",
            ".log",
            ".env",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".conf",
            ".bak",
            ".lock",
            ".map",
            ".webmanifest",
        )
        for item in self.snapshot.get("links", []):
            if item.get("download"):
                continue
            href = str(item.get("href", "")).strip()
            if not href or href.startswith("#"):
                continue
            if href.lower().startswith(("mailto:", "tel:", "javascript:", "data:", "blob:")):
                continue
            try:
                absolute = urljoin(seed, href)
                parts = urlsplit(absolute)
            except ValueError:
                continue
            if parts.scheme not in ("http", "https") or parts.netloc.lower() != seed_host:
                continue
            path = parts.path or "/"
            lower_path = path.lower()
            if any(token in lower_path for token in skip_tokens):
                continue
            if any(lower_path.endswith(suffix) for suffix in skip_suffixes):
                continue
            normalized = self._normalize_route(absolute)
            if normalized in seen:
                continue
            seen.add(normalized)
            routes.append(normalized)
            if len(routes) >= limit:
                break
        return routes

    @staticmethod
    def _normalize_route(url: str) -> str:
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))

    @staticmethod
    def _route_label(url: str, index: int) -> str:
        try:
            path = urlsplit(url).path or "/"
        except ValueError:
            path = "/"
        slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "root"
        return "route-{}-{}".format(index, slug[:40])

    def _current_view(self) -> PageView:
        return PageView(
            options=self.options,
            navigation=self.navigation
            or NavigationResult(url="", final_url=""),
            snapshot=self.snapshot,
            console_messages=list(self.console_messages),
            page_errors=list(self.page_errors),
            network_entries=list(self.network_entries),
            cookies=list(self.cookies),
            evidence_refs=dict(self.evidence_refs),
            label=self.current_label,
        )

    async def finalize_capture(self) -> None:
        """Persist optional HAR, trace, and video artifacts before context close."""

        if self.context is None:
            return
        video_path = None
        if not self.options.capture_video:
            self.capture_notes.append("Video capture disabled by configuration.")
        elif self.page is None:
            self.capture_notes.append("Video capture skipped because the page is unavailable.")
        elif self.page.video is None:
            self.capture_notes.append("Video capture skipped because Playwright did not create a video object.")
        else:
            try:
                video_path = Path(await self.page.video.path())
            except Exception as exc:
                self.capture_notes.append("Video path capture failed: {}".format(redact_text(str(exc))))
        if self.options.capture_trace and self.trace_path is not None:
            try:
                await self.context.tracing.stop(path=str(self.trace_path))
            except Exception as exc:
                self.capture_notes.append("Trace capture failed: {}".format(redact_text(str(exc))))
                self.trace_path = None
        try:
            await self.context.close()
        except Exception as exc:
            self.capture_notes.append("Browser context close failed: {}".format(redact_text(str(exc))))
        self.context = None
        if self.har_path is not None and self.har_path.exists():
            try:
                raw = json.loads(self.har_path.read_text(encoding="utf-8"))
                redacted = self._redact_har(raw)
                self.evidence_refs["har"] = self.evidence.save_json(
                    "har-log",
                    redacted,
                    kind="har",
                    description="Redacted HTTP Archive for the browser run.",
                )
            except Exception as exc:
                self.capture_notes.append("HAR capture failed: {}".format(redact_text(str(exc))))
            finally:
                try:
                    self.har_path.unlink()
                except OSError:
                    pass
        if self.trace_path is not None and self.trace_path.exists():
            try:
                data = self._redact_trace_bytes(self.trace_path.read_bytes())
                self.evidence_refs["trace"] = self.evidence.save_bytes(
                    "playwright-trace",
                    data,
                    kind="trace",
                    description="Playwright trace for the browser run.",
                    suffix=".zip",
                )
            except Exception as exc:
                self.capture_notes.append("Trace artifact save failed: {}".format(redact_text(str(exc))))
            finally:
                try:
                    self.trace_path.unlink()
                except OSError:
                    pass
        if video_path is not None and video_path.exists():
            try:
                data = video_path.read_bytes()
                self.evidence_refs["video"] = self.evidence.save_bytes(
                    "browser-video",
                    data,
                    kind="video",
                    description="Browser video for the run.",
                    suffix=video_path.suffix or ".webm",
                )
            except Exception as exc:
                self.capture_notes.append("Video capture failed: {}".format(redact_text(str(exc))))
            finally:
                try:
                    video_path.unlink()
                except OSError:
                    pass
                if self.video_dir is not None:
                    try:
                        for leftover in self.video_dir.iterdir():
                            leftover.unlink()
                        self.video_dir.rmdir()
                    except OSError:
                        pass
        self.page = None

    @staticmethod
    def _redact_trace_bytes(data: bytes) -> bytes:
        """Redact sensitive values from a Playwright trace archive."""

        import io
        import zipfile

        try:
            source = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return data

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as destination:
            for info in source.infolist():
                raw = source.read(info)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    destination.writestr(info, raw)
                    continue
                for pattern in TOKEN_PATTERNS:
                    if pattern.groups:
                        text = pattern.sub(
                            lambda match: match.group(1) + "[REDACTED]", text
                        )
                    else:
                        text = pattern.sub("[REDACTED]", text)
                text = re.sub(
                    r'(?i)("?(?:authorization|cookie|set-cookie|x-api-key)"?\s*[:=]\s*"?)'
                    r'[^"\n]{6,}',
                    r"\1[REDACTED]",
                    text,
                )
                destination.writestr(info, text.encode("utf-8"))
        return output.getvalue()
        sensitive_headers = {
            "authorization",
            "cookie",
            "set-cookie",
            "proxy-authorization",
            "x-api-key",
        }
        sensitive_query = {
            "token",
            "access_token",
            "id_token",
            "api_key",
            "apikey",
            "secret",
            "password",
            "passwd",
            "session",
            "sessionid",
            "jwt",
            "auth",
            "code",
        }

        def redact_headers(headers: Any) -> None:
            if not isinstance(headers, list):
                return
            for header in headers:
                if not isinstance(header, dict):
                    continue
                name = str(header.get("name", "")).lower()
                if name in sensitive_headers:
                    header["value"] = "[REDACTED]"

        def redact_cookies(cookies: Any) -> None:
            if not isinstance(cookies, list):
                return
            for cookie in cookies:
                if isinstance(cookie, dict) and "value" in cookie:
                    cookie["value"] = "[REDACTED]"

        def redact_query(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if isinstance(item, dict) and str(item.get("name", "")).lower() in sensitive_query:
                    item["value"] = "[REDACTED]"

        def visit(node: Any) -> Any:
            if isinstance(node, dict):
                for key, value in list(node.items()):
                    if key == "url" and isinstance(value, str):
                        node[key] = sanitize_url(value)
                    elif key in ("headers",):
                        redact_headers(value)
                    elif key in ("cookies",):
                        redact_cookies(value)
                    elif key == "queryString":
                        redact_query(value)
                    else:
                        visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)
            return node

        return visit(payload)

    async def _capture(self, requested_url: str) -> None:
        assert self.page is not None
        label = self.current_label
        is_landing = label == "landing"
        try:
            capture_path = self.evidence.run_dir / ".capture-{}.png".format(label)
            await self.page.screenshot(path=str(capture_path), full_page=True)
            # Register the screenshot through the store so it is hashed and manifest-backed.
            data = capture_path.read_bytes()
            self.evidence_refs["screenshot"] = self.evidence.save_screenshot(
                "landing-page" if is_landing else "{}-screenshot".format(label),
                data,
                description=(
                    "Landing page screenshot after navigation."
                    if is_landing
                    else "Screenshot for crawled route {}.".format(label)
                ),
            )
            try:
                capture_path.unlink()
            except OSError:
                pass
        except Exception as exc:
            self.capture_notes.append("Screenshot capture failed: {}".format(redact_text(str(exc))))
        try:
            snapshot = await self.page.evaluate(JS_SNAPSHOT)
            self.snapshot = (
                self._sanitize_snapshot(snapshot) if isinstance(snapshot, dict) else {}
            )
        except Exception as exc:
            self.capture_notes.append("DOM snapshot failed: {}".format(redact_text(str(exc))))
            self.snapshot = {}
        try:
            self.cookies = [
                {
                    "name": str(cookie.get("name", "")),
                    "domain": str(cookie.get("domain", "")),
                    "path": str(cookie.get("path", "")),
                    "secure": bool(cookie.get("secure", False)),
                    "http_only": bool(cookie.get("httpOnly", False)),
                    "same_site": str(cookie.get("sameSite", "")),
                    "expires": cookie.get("expires"),
                    "value": "[REDACTED]",
                }
                for cookie in await self.context.cookies()
            ]
        except Exception as exc:
            self.capture_notes.append("Cookie capture failed: {}".format(redact_text(str(exc))))
        if self.navigation is not None:
            self.evidence_refs["headers"] = self.evidence.save_json(
                "response-headers" if is_landing else "{}-response-headers".format(label),
                {"url": sanitize_url(self.navigation.final_url), "status": self.navigation.status, "headers": self.navigation.headers},
                kind="network_headers",
                description=(
                    "Response headers for the landing page."
                    if is_landing
                    else "Response headers for crawled route {}.".format(label)
                ),
            )
        console_name = "console-log" if is_landing else "{}-console-log".format(label)
        if self.console_messages or self.page_errors:
            self.evidence_refs["console"] = self.evidence.save_console_log(
                {"messages": self.console_messages, "page_errors": self.page_errors},
                name=console_name,
            )
        else:
            self.evidence_refs["console"] = self.evidence.save_console_log(
                {"messages": [], "page_errors": []},
                name=console_name,
            )
        self.evidence_refs["network"] = self.evidence.save_network_log(
            self.network_entries,
            name="network-log" if is_landing else "{}-network-log".format(label),
        )
        self.evidence_refs["snapshot"] = self.evidence.save_json(
            "dom-snapshot" if is_landing else "{}-dom-snapshot".format(label),
            self.snapshot,
            kind="dom_snapshot",
            description=(
                "Sanitized DOM snapshot used by deterministic browser checks."
                if is_landing
                else "Sanitized DOM snapshot for crawled route {}.".format(label)
            ),
        )
        self.evidence_refs["cookies"] = self.evidence.save_json(
            "cookies" if is_landing else "{}-cookies".format(label),
            self.cookies,
            kind="cookies",
            description=(
                "Cookie names and security flags; values are redacted."
                if is_landing
                else "Cookie flags for crawled route {}; values are redacted.".format(label)
            ),
        )
        self.evidence_refs["navigation"] = self.evidence.save_json(
            "navigation" if is_landing else "{}-navigation".format(label),
            {
                "requested_url": sanitize_url(requested_url),
                "final_url": sanitize_url(self.navigation.final_url if self.navigation else requested_url),
                "status": self.navigation.status if self.navigation else None,
                "error": self.navigation.error if self.navigation else "",
                "capture_notes": self.capture_notes,
            },
            kind="navigation",
            description=(
                "Navigation result and capture limitations."
                if is_landing
                else "Navigation result for crawled route {}.".format(label)
            ),
        )

    async def close(self) -> None:
        for closer in (self.context, self.browser):
            if closer is not None:
                try:
                    await closer.close()
                except Exception:
                    pass
        if self.playwright is not None:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        self.context = None
        self.browser = None
        self.playwright = None


    @staticmethod
    def _sanitize_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        url_keys = {"src", "href", "action", "url", "final_url", "location", "finalUrl"}

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in list(node.items()):
                    if isinstance(value, str):
                        if key in url_keys:
                            node[key] = BrowserController._redact_text(
                                sanitize_url(value)
                            )
                        else:
                            node[key] = BrowserController._redact_text(value)
                    else:
                        visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(snapshot)
        return snapshot


    @staticmethod
    def _load_storage_state(value: str) -> Dict[str, Any]:
        raw = value
        if value.startswith("env:"):
            variable = value[4:]
            raw = os.environ.get(variable)
            if raw is None:
                raise BrowserUnavailable(
                    "Storage state environment variable '{}' is not set.".format(variable)
                )
        else:
            path = Path(value)
            if not path.is_file():
                raise BrowserUnavailable(
                    "Storage state file was not found: {}".format(value)
                )
            raw = path.read_text(encoding="utf-8")

        try:
            state = json.loads(raw)
        except (TypeError, ValueError):
            raise BrowserUnavailable("Storage state is not valid JSON.")

        if not isinstance(state, dict):
            raise BrowserUnavailable("Storage state must be a JSON object.")

        cookies = state.get("cookies", [])
        origins = state.get("origins", [])
        if not isinstance(cookies, list) or not isinstance(origins, list):
            raise BrowserUnavailable("Storage state cookies and origins must be arrays.")

        now = time.time()
        has_valid_cookie = False
        for cookie in cookies:
            if not isinstance(cookie, dict):
                raise BrowserUnavailable("Storage state contains an invalid cookie entry.")
            expires = cookie.get("expires", -1)
            if expires in (None, 0, -1):
                has_valid_cookie = True
            elif isinstance(expires, (int, float)) and not isinstance(expires, bool):
                if expires > now:
                    has_valid_cookie = True

        if cookies and not has_valid_cookie and not origins:
            raise BrowserUnavailable(
                "Storage state has expired; provide a fresh state file or secret."
            )
        if not cookies and not origins:
            raise BrowserUnavailable("Storage state is empty.")

        return state

    @staticmethod
    def _redact_text(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value
        for pattern in TOKEN_PATTERNS:
            if pattern.groups:
                text = pattern.sub(lambda match: match.group(1) + "[REDACTED]", text)
            else:
                text = pattern.sub("[REDACTED]", text)
        return text

    @staticmethod
    def _redact_har(payload: Any) -> Any:
        sensitive_headers = {
            "authorization",
            "cookie",
            "set-cookie",
            "proxy-authorization",
            "x-api-key",
        }
        sensitive_query = {
            "token",
            "access_token",
            "id_token",
            "api_key",
            "apikey",
            "secret",
            "password",
            "passwd",
            "session",
            "sessionid",
            "jwt",
            "auth",
            "code",
        }

        def redact_headers(headers: Any) -> None:
            if not isinstance(headers, list):
                return
            for header in headers:
                if not isinstance(header, dict):
                    continue
                name = str(header.get("name", "")).lower()
                if name in sensitive_headers:
                    header["value"] = "[REDACTED]"

        def redact_cookies(cookies: Any) -> None:
            if not isinstance(cookies, list):
                return
            for cookie in cookies:
                if isinstance(cookie, dict) and "value" in cookie:
                    cookie["value"] = "[REDACTED]"

        def redact_query(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if isinstance(item, dict) and str(item.get("name", "")).lower() in sensitive_query:
                    item["value"] = "[REDACTED]"

        def visit(node: Any) -> Any:
            if isinstance(node, dict):
                for key, value in list(node.items()):
                    if isinstance(value, str):
                        if key == "url":
                            node[key] = BrowserController._redact_text(
                                sanitize_url(value)
                            )
                        else:
                            node[key] = BrowserController._redact_text(value)
                    elif key == "headers":
                        redact_headers(value)
                        visit(value)
                    elif key == "cookies":
                        redact_cookies(value)
                        visit(value)
                    elif key == "queryString":
                        redact_query(value)
                        visit(value)
                    else:
                        visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)
            return node

        return visit(payload)

class BrowserCheckRunner:
    def __init__(
        self,
        evidence: EvidenceStore,
        options: BrowserOptions,
        on_outcome: Callable[[Check, CheckOutcome], bool],
    ) -> None:
        self.evidence = evidence
        self.options = options
        self.on_outcome = on_outcome

    def run(self, url: str, checks: List[Check]) -> None:
        try:
            asyncio.run(self._run(url, checks))
        except BrowserUnavailable as exc:
            for check in checks:
                self.on_outcome(
                    check,
                    CheckOutcome(
                        status=CheckStatus.BLOCKED.value,
                        actual="Browser checks were not executed: {}".format(exc),
                        metadata={"limitation": str(exc)},
                        blocker=BlockerDraft(
                            attempted_action=check.title,
                            boundary="Browser execution is unavailable: {}".format(exc),
                            unblock_evidence=[
                                "Install Playwright and Chromium, serve the target, and rerun the check."
                            ],
                        ),
                    ),
                )

    async def _run(self, url: str, checks: List[Check]) -> None:
        controller = BrowserController(self.evidence, self.options)
        await controller.start()
        try:
            navigation = await controller.navigate(url)
            if self.options.crawl and not navigation.error:
                for route in controller.discover_routes(url, self.options.max_pages):
                    await controller.navigate(route)
            views = list(controller.views)
            for index, check in enumerate(checks):
                if navigation.error and check.id != "browser.load":
                    outcome = CheckOutcome(
                        status=CheckStatus.BLOCKED.value,
                        actual="Navigation did not complete: {}".format(navigation.error),
                        evidence=[controller.evidence_refs.get("navigation", "")],
                        metadata={"limitation": navigation.error},
                        blocker=BlockerDraft(
                            attempted_action=check.title,
                            boundary="Navigation did not complete: {}".format(navigation.error),
                            unblock_evidence=[
                                "Restore the target URL or navigation path, then rerun the check."
                            ],
                        ),
                    )
                else:
                    outcome = self._evaluate_routes(check, views, navigation)
                halt = self.on_outcome(check, outcome)
                if halt:
                    for remaining in checks[index + 1 :]:
                        self.on_outcome(
                            remaining,
                            CheckOutcome(
                                status=CheckStatus.SKIPPED.value,
                                actual="Skipped because the kill switch halted execution.",
                                metadata={"halted": True},
                            ),
                        )
                    break
        finally:
            try:
                await controller.finalize_capture()
            except Exception as exc:
                controller.capture_notes.append(
                    "Capture finalization failed: {}".format(redact_text(str(exc)))
                )
            await controller.close()

    def _evaluate_routes(
        self,
        check: Check,
        views: List[PageView],
        landing_navigation: NavigationResult,
    ) -> CheckOutcome:
        """Evaluate one check across every captured route and merge the results."""

        if not views:
            return CheckOutcome(
                status=CheckStatus.BLOCKED.value,
                actual="No browser page was captured for this check.",
                metadata={"limitation": "No browser page was captured."},
                blocker=BlockerDraft(
                    attempted_action=check.title,
                    boundary="No browser page was captured.",
                    unblock_evidence=["Restore navigation and rerun the check."],
                ),
            )
        outcomes: List[CheckOutcome] = []
        skipped: List[str] = []
        for view in views:
            if view.navigation.error and check.id != "browser.load":
                skipped.append(sanitize_url(view.navigation.url))
                continue
            outcome = self._evaluate(check, view, view.navigation)
            outcome.metadata.setdefault(
                "route", sanitize_url(view.navigation.final_url or view.navigation.url)
            )
            outcome.metadata.setdefault("page", view.label)
            outcomes.append(outcome)
        if not outcomes:
            # Every route failed to navigate; the load check owns that finding.
            return CheckOutcome(
                status=CheckStatus.BLOCKED.value,
                actual="No route could be evaluated: {}".format(
                    "; ".join(skipped[:5]) or "navigation failed"
                ),
                metadata={"limitation": "No route could be evaluated.", "routes_not_evaluated": skipped},
                blocker=BlockerDraft(
                    attempted_action=check.title,
                    boundary="No route could be evaluated after navigation failures.",
                    unblock_evidence=["Restore the target routes and rerun the check."],
                ),
            )
        if len(outcomes) == 1:
            outcomes[0].metadata["crawled"] = len(views) > 1
            outcomes[0].metadata["page_count"] = 1
            if skipped:
                outcomes[0].metadata["routes_not_evaluated"] = skipped
            return outcomes[0]
        merged = self._merge_outcomes(check, outcomes)
        if skipped:
            merged.metadata["routes_not_evaluated"] = skipped
        return merged

    def _merge_outcomes(self, check: Check, outcomes: List[CheckOutcome]) -> CheckOutcome:
        merged = CheckOutcome()
        merged.metadata["crawled"] = True
        merged.metadata["page_count"] = len(outcomes)
        merged.metadata["routes"] = [
            {
                "page": outcome.metadata.get("page", ""),
                "route": outcome.metadata.get("route", ""),
                "status": outcome.status,
            }
            for outcome in outcomes
        ]
        findings: Dict[tuple, Any] = {}
        truncated = False
        for outcome in outcomes:
            for finding in outcome.findings:
                key = (
                    finding.title,
                    finding.severity,
                    finding.domain,
                    finding.strength,
                    finding.consequence,
                    finding.remediation,
                )
                existing = findings.get(key)
                if existing is None:
                    if len(merged.findings) >= 10:
                        truncated = True
                        continue
                    findings[key] = finding
                    merged.findings.append(finding)
                else:
                    if finding.location and finding.location not in existing.location:
                        existing.location = (
                            existing.location + ", " + finding.location
                        ).strip(", ")[:800]
                    for evidence_path in finding.evidence:
                        if evidence_path not in existing.evidence:
                            existing.evidence.append(evidence_path)
            for evidence_path in outcome.evidence:
                if evidence_path and evidence_path not in merged.evidence:
                    merged.evidence.append(evidence_path)
        statuses = [outcome.status for outcome in outcomes]
        if CheckStatus.ERROR.value in statuses:
            merged.status = CheckStatus.ERROR.value
        elif CheckStatus.FAILED.value in statuses:
            merged.status = CheckStatus.FAILED.value
        elif CheckStatus.BLOCKED.value in statuses:
            merged.status = CheckStatus.BLOCKED.value
        elif all(status == CheckStatus.SKIPPED.value for status in statuses):
            merged.status = CheckStatus.SKIPPED.value
        else:
            merged.status = CheckStatus.PASSED.value
        summary = []
        for outcome in outcomes:
            page = str(outcome.metadata.get("page", "page"))
            summary.append("{}={} ({})".format(page, outcome.status, outcome.actual))
        merged.actual = "{} route(s): {}".format(len(outcomes), " | ".join(summary))
        if len(merged.actual) > 2000:
            merged.actual = merged.actual[:1997] + "..."
        if truncated:
            merged.metadata["findings_truncated"] = True
        return merged

    def _eval_overflow(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        snapshot = controller.snapshot
        viewport_width = int(controller.options.viewport_width)
        scroll_width = int(snapshot.get("documentScrollWidth", 0) or 0)
        overflowing = snapshot.get("overflowingElements") or []
        if scroll_width > viewport_width + 1 or overflowing:
            offender = overflowing[0] if overflowing else {"selector": "document", "right": scroll_width}
            outcome.add_finding(
                title="The page overflows the {}px mobile viewport".format(viewport_width),
                severity=Severity.MEDIUM.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="Customers on small screens must scroll horizontally or lose content.",
                location=str(offender.get("selector", "document")),
                reproduction="Resize the viewport to {}px and inspect the overflowing element.".format(viewport_width),
                remediation="Constrain widths, wrapping, and media so content fits the viewport.",
                verification="Re-run the mobile check and confirm no horizontal overflow.",
                tags=["responsive", "overflow", "mobile"],
            )
            outcome.actual = "Horizontal overflow: scroll width {} > viewport {}; offending element {}.".format(
                scroll_width, viewport_width, offender.get("selector", "document")
            )
            outcome.metadata["offending_element"] = offender
            outcome.metadata["viewport_width"] = viewport_width
            outcome.metadata["scroll_width"] = scroll_width
        else:
            outcome.status = CheckStatus.PASSED.value
            outcome.actual = "No horizontal overflow: scroll width {} <= viewport {}.".format(
                scroll_width, viewport_width
            )
            outcome.metadata["viewport_width"] = viewport_width
            outcome.metadata["scroll_width"] = scroll_width
        return outcome

    def _eval_focus_order(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        snapshot = controller.snapshot
        positive = int(snapshot.get("positiveTabindex", 0) or 0)
        matches = bool(snapshot.get("focusOrderMatchesDom", True))
        if positive > 0 or not matches:
            outcome.add_finding(
                title="Keyboard focus order does not follow document order",
                severity=Severity.MEDIUM.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="Keyboard and screen-reader users can be sent to controls out of visual order.",
                location=sanitize_url(navigation.final_url),
                reproduction="Tab through the page and compare focus order with the visual order.",
                remediation="Remove positive tabindex values and rely on DOM order.",
                verification="Re-run the check and confirm focus follows the document order.",
                tags=["a11y", "focus", "keyboard"],
            )
            outcome.actual = "{} positive tabindex value(s) disturbed focus order.".format(positive)
        else:
            outcome.status = CheckStatus.PASSED.value
            outcome.actual = "Focus order follows document order."
        outcome.metadata["positive_tabindex_count"] = positive
        return outcome

    def _common_evidence(self, controller: PageView) -> List[str]:
        keys = ["screenshot", "headers", "console", "network", "snapshot", "cookies", "navigation"]
        return [controller.evidence_refs[key] for key in keys if controller.evidence_refs.get(key)]

    def _evaluate(
        self, check: Check, controller: PageView, navigation: NavigationResult
    ) -> CheckOutcome:
        method = getattr(self, "_eval_" + check.id.split(".", 1)[-1], None)
        if method is None:
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="No browser evaluator is registered for '{}'.".format(check.id),
            )
        try:
            outcome = method(controller, navigation)
            if not outcome.evidence:
                outcome.evidence = self._common_evidence(controller)
            return outcome
        except Exception as exc:
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Browser check crashed: {}: {}".format(type(exc).__name__, redact_text(str(exc))),
                evidence=self._common_evidence(controller),
                metadata={"error": redact_text(str(exc))},
            )

    def _eval_load(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        if navigation.error:
            outcome.add_finding(
                title="The live target could not be loaded",
                severity=Severity.HIGH.value,
                domain="functional",
                strength=Strength.OBSERVED.value,
                consequence="No customer can complete a journey if the page does not render.",
                location=sanitize_url(navigation.url),
                reproduction="Open the target URL in a clean browser session and observe the navigation error.",
                remediation="Confirm the service is reachable, the URL is correct, and TLS is valid.",
                verification="Re-run the harness and confirm a 2xx/3xx response and visible content.",
                tags=["runtime", "navigation"],
            )
            outcome.actual = "Navigation failed: {}".format(navigation.error)
            return outcome
        if navigation.status is not None and navigation.status >= 400:
            outcome.add_finding(
                title="The live target returned HTTP {}".format(navigation.status),
                severity=Severity.HIGH.value if navigation.status >= 500 else Severity.MEDIUM.value,
                domain="functional",
                strength=Strength.OBSERVED.value,
                consequence="Customers see an error or an incomplete surface instead of the intended product.",
                location=sanitize_url(navigation.final_url),
                reproduction="Request the URL and inspect the status code and response body.",
                remediation="Fix the server route, dependency, or deployment failure and add a smoke check.",
                verification="Confirm a 2xx/3xx response and the expected page title.",
                tags=["runtime", "http-status"],
            )
        title = str(controller.snapshot.get("title", "")).strip()
        if not title:
            outcome.add_finding(
                title="The rendered page has no title",
                severity=Severity.MEDIUM.value,
                domain="content",
                strength=Strength.OBSERVED.value,
                consequence="Customers, tabs, and assistive technology lack a concise page identity.",
                location=sanitize_url(navigation.final_url),
                reproduction="Inspect document.title after load.",
                remediation="Set a unique, descriptive title for every route.",
                verification="Confirm the title is present and specific to the page.",
                tags=["content", "title"],
            )
        body_length = int(controller.snapshot.get("bodyTextLength", 0) or 0)
        if body_length < 20:
            outcome.add_finding(
                title="The rendered page has little or no visible content",
                severity=Severity.MEDIUM.value,
                domain="functional",
                strength=Strength.OBSERVED.value,
                consequence="A blank or placeholder page can look like a successful response while delivering nothing.",
                location=sanitize_url(navigation.final_url),
                reproduction="Load the page and inspect the visible body text.",
                remediation="Confirm data loading, error handling, and the empty-state experience.",
                verification="Confirm the page shows the intended content or an explicit empty state.",
                tags=["runtime", "empty-state"],
            )
        outcome.actual = "HTTP {}; title {!r}; visible text length {}".format(
            navigation.status, title[:80], body_length
        )
        if not outcome.findings:
            outcome.status = CheckStatus.PASSED.value
        return outcome

    def _eval_console(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        errors = [item for item in controller.console_messages if item.get("type") == "error"]
        errors.extend(controller.page_errors)
        if not errors:
            outcome.actual = "No console errors or unhandled page errors were observed."
            outcome.status = CheckStatus.PASSED.value
            return outcome
        for item in errors[:5]:
            text = str(item.get("text", ""))[:180]
            outcome.add_finding(
                title="Runtime console error: {}".format(text[:80]),
                severity=Severity.MEDIUM.value,
                domain="reliability",
                strength=Strength.OBSERVED.value,
                consequence="A runtime exception can break interactive behavior that a static screenshot appears to pass.",
                location=sanitize_url(navigation.final_url),
                reproduction="Open the browser console and reproduce the page load.",
                remediation="Fix the exception and add a regression check around the affected interaction.",
                verification="Reload the page and confirm the console remains clean.",
                tags=["console", "runtime"],
            )
        outcome.actual = "{} console/page error(s) observed.".format(len(errors))
        return outcome

    def _eval_network(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        problems = []
        for entry in controller.network_entries:
            if entry.get("failed"):
                # A direct route change aborts in-flight resources from the
                # previous page. That is a crawl artifact, not a broken asset.
                if "ERR_ABORTED" in str(entry.get("failure", "")).upper():
                    continue
                problems.append(entry)
            elif isinstance(entry.get("status"), int) and entry["status"] >= 400:
                if entry["status"] == 404 and "favicon" in entry.get("url", ""):
                    continue
                problems.append(entry)
        if not problems:
            outcome.actual = "{} request(s); no failed or unexpected 4xx/5xx responses.".format(
                len(controller.network_entries)
            )
            outcome.status = CheckStatus.PASSED.value
            return outcome
        for entry in problems[:5]:
            status = entry.get("status")
            severity = Severity.HIGH.value if status is None or int(status) >= 500 else Severity.MEDIUM.value
            outcome.add_finding(
                title="Network request failed: {} {}".format(entry.get("method", "GET"), entry.get("url", "")[:100]),
                severity=severity,
                domain="reliability",
                strength=Strength.OBSERVED.value,
                consequence="A missing API or asset can leave a customer journey incomplete.",
                location=sanitize_url(entry.get("url", "")),
                reproduction="Load the page and inspect the failed request in the network log.",
                remediation="Fix the endpoint, credential, or asset path; add a smoke assertion for the request.",
                verification="Confirm the request succeeds with a 2xx/3xx response.",
                tags=["network", "runtime"],
            )
        outcome.actual = "{} request(s); {} failed or non-success response(s).".format(
            len(controller.network_entries), len(problems)
        )
        return outcome

    def _eval_security(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        final_url = navigation.final_url or navigation.url
        is_https = final_url.lower().startswith("https://")
        headers = navigation.headers
        missing: List[str] = []
        if is_https and "strict-transport-security" not in headers:
            missing.append("Strict-Transport-Security")
        if "content-security-policy" not in headers and "content-security-policy-report-only" not in headers:
            missing.append("Content-Security-Policy")
        if "x-content-type-options" not in headers:
            missing.append("X-Content-Type-Options")
        if "x-frame-options" not in headers and "frame-ancestors" not in headers.get("content-security-policy", ""):
            missing.append("frame protection (X-Frame-Options or CSP frame-ancestors)")
        if "referrer-policy" not in headers:
            missing.append("Referrer-Policy")
        if not is_https:
            outcome.add_finding(
                title="The live target is served over plain HTTP",
                severity=Severity.HIGH.value,
                domain="security",
                strength=Strength.OBSERVED.value,
                consequence="Credentials, session cookies, and page content can be read or altered in transit.",
                location=sanitize_url(final_url),
                reproduction="Inspect the final URL scheme in the browser.",
                remediation="Serve the product over HTTPS and redirect HTTP traffic.",
                verification="Confirm HTTP redirects to HTTPS and HSTS is present.",
                tags=["transport", "tls"],
            )
        if missing:
            outcome.add_finding(
                title="Baseline security headers are missing: {}".format(", ".join(missing)),
                severity=Severity.MEDIUM.value,
                domain="security",
                strength=Strength.OBSERVED.value,
                consequence="The browser has fewer defenses against injection, MIME confusion, framing, and referrer leakage.",
                location=sanitize_url(final_url),
                reproduction="Inspect the response headers for the landing document.",
                remediation="Add the missing headers at the edge and tune CSP for the application's real resources.",
                verification="Re-request the page and confirm every baseline header is present.",
                tags=["headers", "hardening"],
            )
        mixed = self._mixed_content(controller.snapshot, is_https)
        if mixed:
            outcome.add_finding(
                title="The page loads insecure resources or links on an HTTPS page",
                severity=Severity.HIGH.value,
                domain="security",
                strength=Strength.OBSERVED.value,
                consequence="Mixed content can be modified in transit or blocked by the browser.",
                location=sanitize_url(final_url),
                reproduction="Inspect script/link/form URLs that start with http:// on the page.",
                remediation="Serve every resource over HTTPS and update hard-coded URLs.",
                verification="Re-run the check and confirm no http:// resources remain.",
                tags=["mixed-content", "transport"],
            )
        insecure_forms = [
            form for form in controller.snapshot.get("forms", []) if str(form.get("action", "")).lower().startswith("http://")
        ]
        if insecure_forms:
            outcome.add_finding(
                title="A form submits over plain HTTP",
                severity=Severity.HIGH.value,
                domain="security",
                strength=Strength.OBSERVED.value,
                consequence="Form data, including credentials or payment details, can be intercepted.",
                location=sanitize_url(final_url),
                reproduction="Inspect form action attributes for http:// targets.",
                remediation="Submit forms to HTTPS endpoints only.",
                verification="Confirm the action is HTTPS and the browser does not warn.",
                tags=["forms", "transport"],
            )
        weak_cookies = [
            cookie
            for cookie in controller.cookies
            if any(token in str(cookie.get("name", "")).lower() for token in ("session", "sid", "auth", "token", "jwt"))
            and (not cookie.get("secure") or not cookie.get("http_only"))
        ]
        if weak_cookies:
            outcome.add_finding(
                title="Session-like cookies lack Secure or HttpOnly flags",
                severity=Severity.MEDIUM.value,
                domain="security",
                strength=Strength.OBSERVED.value,
                consequence="Script or network access can steal a session that should be protected.",
                location=", ".join(sorted({str(cookie.get("name")) for cookie in weak_cookies})),
                reproduction="Inspect Set-Cookie/response cookie flags for session-like names.",
                remediation="Set Secure, HttpOnly, and an appropriate SameSite value.",
                verification="Re-load the page and confirm the flags on every session cookie.",
                tags=["cookies", "session"],
            )
        outcome.actual = "HTTPS={}; missing headers: {}; mixed content: {}; weak cookies: {}".format(
            is_https,
            ", ".join(missing) or "none",
            len(mixed),
            len(weak_cookies),
        )
        if not outcome.findings:
            outcome.status = CheckStatus.PASSED.value
        return outcome

    @staticmethod
    def _mixed_content(snapshot: Dict[str, Any], is_https: bool) -> List[str]:
        if not is_https:
            return []
        urls: List[str] = []
        for script in snapshot.get("scripts", []):
            if str(script).lower().startswith("http://"):
                urls.append(str(script))
        for link in snapshot.get("links", []):
            if str(link.get("href", "")).lower().startswith("http://"):
                urls.append(str(link.get("href")))
        for form in snapshot.get("forms", []):
            if str(form.get("action", "")).lower().startswith("http://"):
                urls.append(str(form.get("action")))
        return urls[:20]

    def _eval_accessibility(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        snapshot = controller.snapshot
        images = [item for item in snapshot.get("images", []) if not item.get("altPresent")]
        unlabeled = []
        for item in snapshot.get("inputs", []):
            if item.get("type") in ("hidden", "submit", "button", "reset", "image"):
                continue
            if not item.get("hasLabel"):
                unlabeled.append(item)
        unnamed = [item for item in snapshot.get("controls", []) if not str(item.get("name", "")).strip()]
        if images:
            outcome.add_finding(
                title="{} image(s) are missing an alt attribute".format(len(images)),
                severity=Severity.MEDIUM.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="Screen-reader users may miss information or hear an unhelpful URL.",
                location=", ".join(item.get("selector", "") for item in images[:5]),
                reproduction="Inspect the accessibility tree for images without an accessible name.",
                remediation="Add meaningful alt text, or alt=\"\" for purely decorative images.",
                verification="Confirm every image has an intentional alternative in the browser tree.",
                tags=["a11y", "images"],
            )
        if unlabeled:
            outcome.add_finding(
                title="{} form control(s) may lack a programmatic label".format(len(unlabeled)),
                severity=Severity.MEDIUM.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="Assistive-technology users may not know what the field expects.",
                location=", ".join(item.get("selector", "") for item in unlabeled[:5]),
                reproduction="Inspect each control's accessible name and visible label.",
                remediation="Associate a persistent visible label; placeholders are not labels.",
                verification="Confirm every control has a meaningful accessible name.",
                tags=["a11y", "forms"],
            )
        if unnamed:
            outcome.add_finding(
                title="{} interactive control(s) have no accessible name".format(len(unnamed)),
                severity=Severity.MEDIUM.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="A keyboard or screen-reader user cannot tell what the control does.",
                location=", ".join(item.get("selector", "") for item in unnamed[:5]),
                reproduction="Inspect the accessibility tree for buttons and links with empty names.",
                remediation="Add visible text, aria-label, or aria-labelledby; icon-only controls need a name.",
                verification="Confirm every control announces a useful name and role.",
                tags=["a11y", "controls"],
            )
        if snapshot.get("duplicateIds"):
            outcome.add_finding(
                title="{} duplicate element id(s) were found".format(len(snapshot["duplicateIds"])),
                severity=Severity.LOW.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="Labels and references can point at the wrong element, confusing assistive technology.",
                location=", ".join(str(item) for item in snapshot["duplicateIds"][:5]),
                reproduction="Inspect the DOM for repeated id values.",
                remediation="Make ids unique or use refs/relationships instead of global ids.",
                verification="Re-scan the DOM and confirm ids are unique.",
                tags=["a11y", "dom"],
            )
        if int(snapshot.get("positiveTabindex", 0) or 0) > 0:
            outcome.add_finding(
                title="Positive tabindex values change the natural focus order",
                severity=Severity.LOW.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="Keyboard users can be sent to unexpected controls and lose context.",
                location=sanitize_url(navigation.final_url),
                reproduction="Tab through the page and compare focus order with the visual order.",
                remediation="Rely on DOM order; use tabindex 0 or -1 only where necessary.",
                verification="Confirm the focus order follows the reading order.",
                tags=["a11y", "keyboard"],
            )
        if not str(snapshot.get("lang", "")).strip():
            outcome.add_finding(
                title="The document language is not declared",
                severity=Severity.LOW.value,
                domain="accessibility",
                strength=Strength.OBSERVED.value,
                consequence="Screen readers may pronounce content with the wrong language rules.",
                location=sanitize_url(navigation.final_url),
                reproduction="Inspect the html element's lang attribute.",
                remediation="Declare a valid language such as lang=\"en\".",
                verification="Confirm the language is exposed in the accessibility tree.",
                tags=["a11y", "language"],
            )
        outcome.actual = "{} image(s) without alt; {} unlabeled control(s); {} unnamed control(s); {} duplicate id(s).".format(
            len(images), len(unlabeled), len(unnamed), len(snapshot.get("duplicateIds", []))
        )
        outcome.metadata["manual_review_required"] = True
        if not outcome.findings:
            outcome.status = CheckStatus.PASSED.value
        return outcome

    def _eval_content(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        snapshot = controller.snapshot
        h1s = snapshot.get("h1s", [])
        title = str(snapshot.get("title", "")).strip()
        if len(h1s) != 1:
            outcome.add_finding(
                title="The page has {} primary heading(s) instead of one".format(len(h1s)),
                severity=Severity.LOW.value,
                domain="content",
                strength=Strength.OBSERVED.value,
                consequence="Customers and assistive technology receive an ambiguous page outline.",
                location=sanitize_url(navigation.final_url),
                reproduction="Inspect the rendered heading structure.",
                remediation="Use one descriptive h1 and a coherent heading hierarchy.",
                verification="Confirm one h1 and a logical outline in the accessibility tree.",
                tags=["content", "headings"],
            )
        if title and (len(title) < 10 or len(title) > 70):
            outcome.add_finding(
                title="The page title is outside a useful length range",
                severity=Severity.LOW.value,
                domain="content",
                strength=Strength.OBSERVED.value,
                consequence="The page identity is truncated or too vague in tabs and search results.",
                location=sanitize_url(navigation.final_url),
                reproduction="Measure the rendered document title.",
                remediation="Write a concise, specific title between roughly 10 and 70 characters.",
                verification="Confirm the title remains readable when truncated.",
                tags=["content", "title"],
            )
        outcome.actual = "h1 count: {}; title length: {}; text length: {}".format(
            len(h1s), len(title), snapshot.get("bodyTextLength", 0)
        )
        if not outcome.findings:
            outcome.status = CheckStatus.PASSED.value
        return outcome

    def _eval_links(self, controller: PageView, navigation: NavigationResult) -> CheckOutcome:
        outcome = CheckOutcome(evidence=self._common_evidence(controller))
        is_https = (navigation.final_url or navigation.url).lower().startswith("https://")
        links = controller.snapshot.get("links", [])
        empty = [item for item in links if str(item.get("href", "")).strip() in ("", "#") or str(item.get("href", "")).lower().startswith("javascript:")]
        insecure = [item for item in links if is_https and str(item.get("href", "")).lower().startswith("http://")]
        unsafe_target = [
            item
            for item in links
            if str(item.get("target", "")).lower() == "_blank"
            and "noopener" not in str(item.get("rel", "")).lower()
            and "noreferrer" not in str(item.get("rel", "")).lower()
        ]
        if empty:
            outcome.add_finding(
                title="{} link(s) are empty, placeholder, or javascript: targets".format(len(empty)),
                severity=Severity.LOW.value,
                domain="functional",
                strength=Strength.OBSERVED.value,
                consequence="A customer can hit a dead end or a control that looks interactive but does nothing.",
                location=", ".join(item.get("selector", "") for item in empty[:5]),
                reproduction="Inspect the href values in the rendered DOM.",
                remediation="Point links at a real destination or render a non-interactive element.",
                verification="Click each link and confirm a meaningful destination or disabled state.",
                tags=["links", "dead-end"],
            )
        if insecure:
            outcome.add_finding(
                title="{} link(s) point at plain HTTP from an HTTPS page".format(len(insecure)),
                severity=Severity.MEDIUM.value,
                domain="security",
                strength=Strength.OBSERVED.value,
                consequence="A customer can be downgraded to an interceptable destination.",
                location=", ".join(item.get("selector", "") for item in insecure[:5]),
                reproduction="Inspect the href values for http:// destinations.",
                remediation="Use HTTPS destinations and fix hard-coded URLs.",
                verification="Re-scan and confirm no http:// links remain.",
                tags=["links", "transport"],
            )
        if unsafe_target:
            outcome.add_finding(
                title="{} new-tab link(s) omit noopener/noreferrer".format(len(unsafe_target)),
                severity=Severity.LOW.value,
                domain="security",
                strength=Strength.OBSERVED.value,
                consequence="The opened page can access window.opener and navigate the original tab.",
                location=", ".join(item.get("selector", "") for item in unsafe_target[:5]),
                reproduction="Inspect target=\"_blank\" links and their rel attribute.",
                remediation="Add rel=\"noopener noreferrer\" to new-tab links.",
                verification="Re-scan and confirm the relationship is present.",
                tags=["links", "tabnabbing"],
            )
        outcome.actual = "{} link(s); {} placeholder; {} insecure; {} missing noopener.".format(
            len(links), len(empty), len(insecure), len(unsafe_target)
        )
        if not outcome.findings:
            outcome.status = CheckStatus.PASSED.value
        return outcome
