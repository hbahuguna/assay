"""Inspect layer: source inventory and deterministic static risk rules.

This is intentionally heuristic, not a substitute for a language-aware analyzer.
Every finding is source-proven (file and line), and sensitive values are redacted
before they reach state, evidence, or a report.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .evidence import EvidenceStore
from .models import Check, CheckStatus, Severity, Strength
from .outcomes import CheckOutcome

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".harness",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".ruff_cache",
    ".next",
    ".nuxt",
    "coverage",
    "htmlcov",
    ".idea",
    ".vscode",
}

LANGUAGE_BY_EXT = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".rs": "Rust",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "CSS",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".sql": "SQL",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".md": "Markdown",
}

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".rs",
    ".swift",
    ".scala",
    ".vue",
    ".svelte",
    ".sh",
    ".sql",
}

MARKUP_EXTENSIONS = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte"}

MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Pipfile",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
}

LOCKFILE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
}

CI_NAMES = {
    ".gitlab-ci.yml",
    "Jenkinsfile",
    "azure-pipelines.yml",
    ".travis.yml",
    "bitbucket-pipelines.yml",
    "appveyor.yml",
    ".drone.yml",
}

ENV_NAMES = {".env", ".env.local", ".env.production", ".env.staging", ".env.development"}

TEST_HINTS = ("test", "spec", "__tests__", "tests", "e2e", "fixture")

# Issue markers are inventoried for the /assay-issues category. Counts only are
# persisted, so an inline note cannot leak a credential into evidence.
ISSUE_MARKER_PATTERN = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")

MAX_SCAN_BYTES = 512 * 1024
MAX_SCAN_FILES = 2000
MAX_INVENTORY_FILES = 5000


@dataclass
class Inventory:
    available: bool
    root: str
    files: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    markup_files: List[str] = field(default_factory=list)
    manifests: List[str] = field(default_factory=list)
    test_files: List[str] = field(default_factory=list)
    ci_files: List[str] = field(default_factory=list)
    lockfiles: List[str] = field(default_factory=list)
    env_files: List[str] = field(default_factory=list)
    languages: Dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0
    truncated: bool = False
    skipped_dirs: List[str] = field(default_factory=list)
    test_frameworks: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "root": self.root,
            "files": self.files,
            "source_files": self.source_files,
            "markup_files": self.markup_files,
            "manifests": self.manifests,
            "test_files": self.test_files,
            "ci_files": self.ci_files,
            "lockfiles": self.lockfiles,
            "env_files": self.env_files,
            "languages": self.languages,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "truncated": self.truncated,
            "skipped_dirs": self.skipped_dirs,
            "test_frameworks": self.test_frameworks,
            "has_manifest": bool(self.manifests),
            "has_markup": bool(self.markup_files),
            "has_tests": bool(self.test_files),
            "has_ci": bool(self.ci_files),
            "has_lockfile": bool(self.lockfiles),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Inventory":
        return cls(
            available=bool(data.get("available", False)),
            root=str(data.get("root", "")),
            files=list(data.get("files", [])),
            source_files=list(data.get("source_files", [])),
            markup_files=list(data.get("markup_files", [])),
            manifests=list(data.get("manifests", [])),
            test_files=list(data.get("test_files", [])),
            ci_files=list(data.get("ci_files", [])),
            lockfiles=list(data.get("lockfiles", [])),
            env_files=list(data.get("env_files", [])),
            languages=dict(data.get("languages", {})),
            total_files=int(data.get("total_files", 0)),
            total_bytes=int(data.get("total_bytes", 0)),
            truncated=bool(data.get("truncated", False)),
            skipped_dirs=list(data.get("skipped_dirs", [])),
            test_frameworks=list(data.get("test_frameworks", [])),
        )


class SourceInspector:
    def __init__(self, root: Path, max_files: int = MAX_INVENTORY_FILES) -> None:
        self.root = Path(root).resolve()
        self.max_files = max_files

    def discover(self) -> Inventory:
        inventory = Inventory(available=True, root=str(self.root))
        if not self.root.exists() or not self.root.is_dir():
            inventory.available = False
            return inventory
        count = 0
        total_bytes = 0
        for current_root, dir_names, file_names in os.walk(str(self.root), followlinks=False):
            allowed_dirs = []
            for name in sorted(dir_names):
                if name in SKIP_DIRS or (name.startswith(".") and name not in (".github", ".circleci")):
                    inventory.skipped_dirs.append(
                        str(Path(current_root, name).relative_to(self.root))
                    )
                else:
                    allowed_dirs.append(name)
            dir_names[:] = allowed_dirs
            for file_name in sorted(file_names):
                path = Path(current_root) / file_name
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                rel = str(path.relative_to(self.root))
                count += 1
                total_bytes += size
                if count <= self.max_files:
                    inventory.files.append(rel)
                else:
                    inventory.truncated = True
                ext = path.suffix.lower()
                if ext in LANGUAGE_BY_EXT:
                    language = LANGUAGE_BY_EXT[ext]
                    inventory.languages[language] = inventory.languages.get(language, 0) + 1
                if file_name in MANIFEST_NAMES:
                    inventory.manifests.append(rel)
                if file_name in LOCKFILE_NAMES:
                    inventory.lockfiles.append(rel)
                if file_name in CI_NAMES:
                    inventory.ci_files.append(rel)
                if file_name in ENV_NAMES:
                    inventory.env_files.append(rel)
                if rel.startswith(".github/workflows/") and ext in (".yml", ".yaml"):
                    inventory.ci_files.append(rel)
                if ext in CODE_EXTENSIONS or ext in MARKUP_EXTENSIONS:
                    inventory.source_files.append(rel)
                if ext in MARKUP_EXTENSIONS:
                    inventory.markup_files.append(rel)
                lower = rel.lower()
                if (
                    file_name.startswith("test_")
                    or file_name.endswith("_test.py")
                    or file_name.endswith("_test.go")
                    or ".test." in file_name
                    or ".spec." in file_name
                    or any(hint in lower for hint in TEST_HINTS)
                ) and ext in (CODE_EXTENSIONS | MARKUP_EXTENSIONS):
                    if rel not in inventory.test_files:
                        inventory.test_files.append(rel)
        inventory.total_files = count
        inventory.total_bytes = total_bytes
        inventory.source_files = inventory.source_files[:5000]
        inventory.markup_files = inventory.markup_files[:1000]
        inventory.test_files = inventory.test_files[:500]
        inventory.manifests = inventory.manifests[:200]
        inventory.ci_files = sorted(set(inventory.ci_files))[:100]
        inventory.lockfiles = sorted(set(inventory.lockfiles))[:100]
        inventory.env_files = sorted(set(inventory.env_files))[:50]
        inventory.test_frameworks = self._detect_frameworks(inventory)
        return inventory

    def _detect_frameworks(self, inventory: Inventory) -> List[str]:
        blob = ""
        for rel in inventory.manifests:
            blob += self._read(Path(self.root) / rel).lower() + "\n"
        found: List[str] = []
        for name in (
            "pytest",
            "unittest",
            "jest",
            "vitest",
            "mocha",
            "playwright",
            "cypress",
            "@testing-library",
            "ava",
            "rspec",
            "minitest",
            "junit",
            "tox",
            "nox",
        ):
            if name in blob and name not in found:
                found.append(name)
        if any(rel.endswith("_test.go") for rel in inventory.test_files):
            found.append("go test")
        if "cargo" in " ".join(inventory.manifests).lower() and inventory.test_files:
            found.append("cargo test")
        return found

    @staticmethod
    def _read(path: Path) -> str:
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                return ""
            data = path.read_bytes()
        except OSError:
            return ""
        if b"\x00" in data[:2048]:
            return ""
        return data.decode("utf-8", errors="replace")


SECRET_RULES = [
    {
        "id": "private-key",
        "pattern": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
        "severity": Severity.CRITICAL.value,
        "title": "Private key material committed to the repository",
        "consequence": "Anyone with repository access can impersonate the key holder or decrypt protected traffic.",
        "remediation": "Revoke the key, remove it from history, rotate dependent secrets, and load the replacement from a secret manager.",
        "verification": "Re-scan the repository and its history; confirm the key is revoked and no live copy remains.",
    },
    {
        "id": "stripe-live",
        "pattern": re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b"),
        "severity": Severity.CRITICAL.value,
        "title": "Live payment provider secret is present in source",
        "consequence": "A leaked live key can move money, read customer data, or disrupt billing.",
        "remediation": "Roll the key immediately, inspect provider audit logs, and move the secret to a managed vault.",
        "verification": "Confirm the old key is revoked and the application uses the vault-backed replacement.",
    },
    {
        "id": "aws-access-key",
        "pattern": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "severity": Severity.HIGH.value,
        "title": "Cloud access key identifier committed to the repository",
        "consequence": "A matching secret could grant cloud account access and data exfiltration.",
        "remediation": "Disable the key, review cloud audit logs, and use short-lived role credentials.",
        "verification": "Confirm the key is inactive and identify any unexpected API activity.",
    },
    {
        "id": "github-token",
        "pattern": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        "severity": Severity.HIGH.value,
        "title": "Source-host access token committed to the repository",
        "consequence": "The token may permit repository, package, or workflow changes.",
        "remediation": "Revoke the token, review organization audit logs, and reissue with least privilege.",
        "verification": "Confirm the token is revoked and no unexpected workflow or repository changes occurred.",
    },
    {
        "id": "slack-token",
        "pattern": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "severity": Severity.HIGH.value,
        "title": "Chat integration token committed to the repository",
        "consequence": "The token may allow message access or impersonation in a workspace.",
        "remediation": "Revoke the token and use a workspace-scoped secret store.",
        "verification": "Confirm revocation and review workspace audit events.",
    },
    {
        "id": "google-api-key",
        "pattern": re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
        "severity": Severity.HIGH.value,
        "title": "Cloud API key committed to the repository",
        "consequence": "An unrestricted key may permit billable API calls or data access.",
        "remediation": "Restrict or rotate the key and move it to a secret manager.",
        "verification": "Confirm restrictions, rotation, and no unexpected quota usage.",
    },
    {
        "id": "jwt",
        "pattern": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
        "severity": Severity.MEDIUM.value,
        "title": "Serialized token embedded in source",
        "consequence": "A live token may grant a session or service identity beyond the test environment.",
        "remediation": "Confirm whether the token is live; revoke it and replace it with runtime-injected credentials.",
        "verification": "Decode the token claims, confirm expiry/revocation, and re-scan.",
    },
    {
        "id": "generic-credential",
        "pattern": re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|client[_-]?secret|access[_-]?key)\b"
            r"\s*[:=]\s*['\"]([^'\"]{8,})['\"]"
        ),
        "severity": Severity.HIGH.value,
        "title": "Credential-like value hard-coded in source",
        "consequence": "The value may grant access to a service, environment, or customer record.",
        "remediation": "Move the value to a secret manager, rotate it, and add a pre-commit secret scan.",
        "verification": "Confirm the value is no longer present and that the old credential is invalid.",
    },
]

UNSAFE_RULES = [
    {
        "id": "eval",
        "pattern": re.compile(r"\b(?:eval|exec)\s*\("),
        "severity": Severity.HIGH.value,
        "title": "Dynamic code execution primitive",
        "consequence": "Untrusted input can become arbitrary code execution.",
        "remediation": "Remove eval/exec, parse a constrained format, or isolate execution in a sandbox.",
        "verification": "Add an adversarial input test and confirm the code path rejects it.",
    },
    {
        "id": "shell-true",
        "pattern": re.compile(r"shell\s*=\s*True"),
        "severity": Severity.HIGH.value,
        "title": "Shell command constructed with shell=True",
        "consequence": "User-controlled arguments can inject operating-system commands.",
        "remediation": "Pass an argument vector with shell=False and validate each argument.",
        "verification": "Exercise metacharacters and confirm they are treated as data.",
    },
    {
        "id": "os-system",
        "pattern": re.compile(r"\bos\.system\s*\("),
        "severity": Severity.HIGH.value,
        "title": "Unsafe operating-system command execution",
        "consequence": "Concatenated input can execute arbitrary commands.",
        "remediation": "Use subprocess with an argument list and strict validation.",
        "verification": "Test shell metacharacters and path traversal inputs.",
    },
    {
        "id": "inner-html",
        "pattern": re.compile(r"\.innerHTML\s*="),
        "severity": Severity.MEDIUM.value,
        "title": "HTML assigned through innerHTML",
        "consequence": "Untrusted content can become script in the customer's browser.",
        "remediation": "Use a safe DOM API or a reviewed sanitizer with a strict policy.",
        "verification": "Add an XSS payload test and confirm it renders as text.",
    },
    {
        "id": "dangerous-html",
        "pattern": re.compile(r"dangerouslySetInnerHTML"),
        "severity": Severity.MEDIUM.value,
        "title": "React raw HTML injection point",
        "consequence": "Unsanitized content can execute script in the customer's browser.",
        "remediation": "Render structured content or sanitize with a maintained allowlist.",
        "verification": "Test script, event-handler, and URL payloads.",
    },
    {
        "id": "tls-verify-off",
        "pattern": re.compile(r"verify\s*=\s*False"),
        "severity": Severity.HIGH.value,
        "title": "TLS certificate verification disabled",
        "consequence": "A network attacker can impersonate the upstream service and read or alter traffic.",
        "remediation": "Enable certificate verification and configure a trusted CA bundle.",
        "verification": "Confirm a bad certificate is rejected in a test environment.",
    },
    {
        "id": "pickle-loads",
        "pattern": re.compile(r"\bpickle\.loads?\s*\("),
        "severity": Severity.HIGH.value,
        "title": "Untrusted pickle deserialization",
        "consequence": "A crafted payload can execute arbitrary code during deserialization.",
        "remediation": "Use a data-only format such as JSON with an explicit schema.",
        "verification": "Send a malicious object and confirm it is rejected before deserialization.",
    },
    {
        "id": "cors-wildcard",
        "pattern": re.compile(r"(?i)access-control-allow-origin['\"]?\s*[:=]\s*['\"]\*['\"]"),
        "severity": Severity.MEDIUM.value,
        "title": "Wildcard cross-origin access policy",
        "consequence": "Any website can read responses that were intended for a trusted origin.",
        "remediation": "Use an explicit allowlist and keep credentials out of wildcard responses.",
        "verification": "Send requests from an untrusted origin and confirm they are rejected.",
    },
    {
        "id": "debug-true",
        "pattern": re.compile(r"(?i)\bdebug\s*=\s*True\b"),
        "severity": Severity.MEDIUM.value,
        "title": "Debug mode enabled in source configuration",
        "consequence": "Debug pages can expose stack traces, configuration, or interactive consoles.",
        "remediation": "Drive debug mode from an environment flag that defaults to false.",
        "verification": "Confirm production configuration reports debug as disabled.",
    },
    {
        "id": "node-tls-off",
        "pattern": re.compile(r"NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0"),
        "severity": Severity.HIGH.value,
        "title": "Node TLS verification globally disabled",
        "consequence": "All outbound TLS in the process becomes vulnerable to impersonation.",
        "remediation": "Remove the global override and fix the trust store instead.",
        "verification": "Confirm an invalid certificate is rejected.",
    },
]

A11Y_RULES = [
    {
        "id": "img-alt",
        "pattern": re.compile(r"<img\b(?![^>]*\balt\s*=)[^>]*>", re.IGNORECASE),
        "severity": Severity.MEDIUM.value,
        "title": "Image is missing an alt attribute",
        "consequence": "Screen-reader users may miss information or hear an unhelpful filename.",
        "remediation": "Add meaningful alt text, or alt=\"\" for decorative images.",
        "verification": "Inspect the accessibility tree and confirm each image has an intentional alternative.",
    },
    {
        "id": "input-label",
        "pattern": re.compile(r"<input\b(?![^>]*(?:aria-label|aria-labelledby|id\s*=))[^>]*>", re.IGNORECASE),
        "severity": Severity.LOW.value,
        "title": "Input may lack a programmatic label",
        "consequence": "Assistive-technology users may not know what the field expects.",
        "remediation": "Associate a visible label with the input, or add aria-label/aria-labelledby.",
        "verification": "Confirm the control has an accessible name in the browser tree.",
    },
    {
        "id": "html-lang",
        "pattern": re.compile(r"<html\b(?![^>]*\blang\s*=)[^>]*>", re.IGNORECASE),
        "severity": Severity.LOW.value,
        "title": "Document language is not declared",
        "consequence": "Screen readers may pronounce content with the wrong language rules.",
        "remediation": "Add a valid lang attribute to the root html element.",
        "verification": "Confirm the language is exposed in the accessibility tree.",
    },
    {
        "id": "positive-tabindex",
        "pattern": re.compile(r"tabIndex\s*=\s*['\"]?[1-9]", re.IGNORECASE),
        "severity": Severity.MEDIUM.value,
        "title": "Positive tabindex changes the natural focus order",
        "consequence": "Keyboard users can be sent to unexpected controls and lose context.",
        "remediation": "Use DOM order and tabindex=\"0\"/\"-1\" only where necessary.",
        "verification": "Tab through the page and confirm the order matches the visual flow.",
    },
    {
        "id": "clickable-div",
        "pattern": re.compile(r"<div\b[^>]*\bonClick\b", re.IGNORECASE),
        "severity": Severity.LOW.value,
        "title": "Click handler on a non-semantic container",
        "consequence": "Keyboard and assistive-technology users may not be able to activate the control.",
        "remediation": "Use a button or link, or add the required role, tabindex, and key handling.",
        "verification": "Activate the control with keyboard only and inspect its accessible role.",
    },
    {
        "id": "aria-hidden-focusable",
        "pattern": re.compile(
            r"<(?:button|a|input|select|textarea)\b[^>]*aria-hidden\s*=\s*['\"]true['\"]",
            re.IGNORECASE,
        ),
        "severity": Severity.MEDIUM.value,
        "title": "Focusable control is hidden from assistive technology",
        "consequence": "Keyboard users can focus a control that screen readers cannot describe.",
        "remediation": "Remove aria-hidden from focusable controls or remove them from the tab order.",
        "verification": "Confirm focus and accessibility-tree exposure agree.",
    },
]


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: Dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = float(len(value))
    return -sum((count / length) * math.log(count / length, 2) for count in counts.values())


def _looks_placeholder(value: str) -> bool:
    lower = value.lower()
    placeholders = (
        "example",
        "sample",
        "dummy",
        "changeme",
        "change_me",
        "your_",
        "your-",
        "placeholder",
        "xxx",
        "****",
        "...",
        "process.env",
        "os.environ",
        "getenv",
        "${",
        "<",
        ">",
        "redacted",
    )
    return any(token in lower for token in placeholders)


def redact(value: str) -> str:
    value = value.strip()
    if len(value) <= 6:
        return "[REDACTED]"
    return "{}…[REDACTED {} chars]".format(value[:4], len(value))


def _is_test_path(rel: str) -> bool:
    lower = rel.lower()
    return any(hint in lower for hint in TEST_HINTS)


def _downgrade(severity: str, rel: str) -> str:
    if not _is_test_path(rel):
        return severity
    order = [
        Severity.CRITICAL.value,
        Severity.HIGH.value,
        Severity.MEDIUM.value,
        Severity.LOW.value,
        Severity.INFO.value,
    ]
    try:
        index = order.index(severity)
    except ValueError:
        return severity
    return order[min(index + 1, len(order) - 1)]


class StaticCheckRunner:
    def __init__(self, root: Path, inventory: Inventory, evidence: EvidenceStore) -> None:
        self.root = Path(root).resolve()
        self.inventory = inventory
        self.evidence = evidence

    def run(self, check: Check) -> CheckOutcome:
        method = getattr(self, "check_" + check.id.replace(".", "_"), None)
        if method is None:
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="No static runner is registered for check '{}'.".format(check.id),
            )
        try:
            return method(check)
        except Exception as exc:  # A broken rule must not kill the run.
            return CheckOutcome(
                status=CheckStatus.ERROR.value,
                actual="Static check crashed: {}: {}".format(type(exc).__name__, exc),
                metadata={"error": str(exc)},
            )

    def _save(self, name: str, payload: Any, check: Check, description: str) -> str:
        return self.evidence.save_json(
            name,
            payload,
            kind="static_analysis",
            description=description,
            check_id=check.id,
        )

    def _iter_files(self, extensions: Optional[Iterable[str]] = None) -> Iterable[Tuple[str, Path]]:
        wanted = set(extensions) if extensions else None
        seen = 0
        for rel in self.inventory.source_files:
            path = self.root / rel
            if not path.exists():
                continue
            if wanted is not None and path.suffix.lower() not in wanted:
                continue
            if ".min." in path.name:
                continue
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    continue
            except OSError:
                continue
            seen += 1
            if seen > MAX_SCAN_FILES:
                break
            yield rel, path

    @staticmethod
    def _read(path: Path) -> str:
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        if b"\x00" in data[:2048]:
            return ""
        return data.decode("utf-8", errors="replace")

    def check_static_inventory(self, check: Check) -> CheckOutcome:
        evidence = self._save(
            "source-inventory",
            self.inventory.to_dict(),
            check,
            "Source inventory: file counts, languages, manifests, tests, and CI configuration.",
        )
        actual = "{} files, {} bytes; languages: {}; manifests: {}; tests: {}; CI: {}".format(
            self.inventory.total_files,
            self.inventory.total_bytes,
            ", ".join(sorted(self.inventory.languages)) or "none",
            len(self.inventory.manifests),
            len(self.inventory.test_files),
            len(self.inventory.ci_files),
        )
        return CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual=actual,
            evidence=[evidence],
            metadata={"inventory": self.inventory.to_dict()},
        )

    def check_static_tests(self, check: Check) -> CheckOutcome:
        evidence = self._save(
            "test-inventory",
            {
                "test_files": self.inventory.test_files,
                "test_frameworks": self.inventory.test_frameworks,
            },
            check,
            "Automated test inventory.",
        )
        outcome = CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="{} test file(s) found; frameworks: {}".format(
                len(self.inventory.test_files),
                ", ".join(self.inventory.test_frameworks) or "not detected",
            ),
            evidence=[evidence],
        )
        if not self.inventory.test_files:
            outcome.add_finding(
                title="No automated tests detected in the release",
                severity=Severity.MEDIUM.value,
                domain="functional",
                consequence="Change safety depends on unrecorded manual exploration, so regressions can ship silently.",
                location="repository root",
                reproduction="Search the source tree for test/spec files and a runnable test command.",
                remediation="Add a focused test suite around the highest-risk customer outcome and run it in CI.",
                verification="Confirm the suite fails on a seeded regression and passes after the fix.",
                tags=["coverage-gap"],
            )
        return outcome

    def check_static_ci(self, check: Check) -> CheckOutcome:
        evidence = self._save(
            "ci-inventory",
            {"ci_files": self.inventory.ci_files},
            check,
            "Continuous integration inventory.",
        )
        outcome = CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="CI files: {}".format(", ".join(self.inventory.ci_files) or "none"),
            evidence=[evidence],
        )
        if not self.inventory.ci_files:
            outcome.add_finding(
                title="No continuous integration configuration detected",
                severity=Severity.LOW.value,
                domain="reliability",
                consequence="Test evidence is not refreshed automatically and can drift from the release.",
                location="repository root",
                reproduction="Inspect for .github/workflows, .gitlab-ci.yml, Jenkinsfile, or another CI config.",
                remediation="Run the test suite and this harness on pull requests.",
                verification="Open a pull request and confirm the checks execute and report.",
                tags=["coverage-gap"],
            )
        return outcome

    def check_static_secrets(self, check: Check) -> CheckOutcome:
        matches: List[Dict[str, Any]] = []
        for rel, path in self._iter_files():
            text = self._read(path)
            if not text:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                for rule in SECRET_RULES:
                    match = rule["pattern"].search(line)
                    if not match:
                        continue
                    if rule["id"] == "generic-credential":
                        value = match.group(2)
                        if _looks_placeholder(value) or _entropy(value) < 2.5:
                            continue
                        redacted = redact(value)
                    elif rule["id"] == "private-key":
                        redacted = "[REDACTED PRIVATE KEY HEADER]"
                    else:
                        redacted = redact(match.group(0))
                    matches.append(
                        {
                            "rule": rule["id"],
                            "severity": _downgrade(rule["severity"], rel),
                            "file": rel,
                            "line": line_number,
                            "excerpt": redacted,
                        }
                    )
                    if len(matches) >= 25:
                        break
                if len(matches) >= 25:
                    break
            if len(matches) >= 25:
                break
        evidence = self._save(
            "secret-scan",
            {"matches": matches, "note": "Values are redacted; length and prefix only."},
            check,
            "Redacted secret scan results.",
        )
        outcome = CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="No credential-like values found in scanned source.",
            evidence=[evidence],
            metadata={"matches": len(matches)},
        )
        if not matches:
            return outcome
        outcome.actual = "{} credential-like match(es) found; values are redacted in evidence.".format(len(matches))
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in matches:
            group_key = (item["rule"], item["file"])
            if group_key not in grouped:
                grouped[group_key] = {
                    "rule": item["rule"],
                    "file": item["file"],
                    "line": item["line"],
                    "excerpt": item["excerpt"],
                    "severity": item["severity"],
                    "count": 0,
                }
            grouped[group_key]["count"] += 1
        rule_meta = {rule["id"]: rule for rule in SECRET_RULES}
        for (rule_id, rel), item in list(grouped.items())[:8]:
            rule = rule_meta[rule_id]
            outcome.add_finding(
                title="{} ({} match{} in {})".format(
                    rule["title"], item["count"], "" if item["count"] == 1 else "es", rel
                ),
                severity=item["severity"],
                domain="security",
                consequence=rule["consequence"],
                location="{}:{}".format(rel, item["line"]),
                reproduction="Inspect the redacted match in the evidence file and confirm whether the value is live.",
                remediation=rule["remediation"],
                verification=rule["verification"],
                tags=["secret", "redacted"],
            )
        return outcome

    def check_static_unsafe(self, check: Check) -> CheckOutcome:
        matches: List[Dict[str, Any]] = []
        for rel, path in self._iter_files(CODE_EXTENSIONS):
            text = self._read(path)
            if not text:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                for rule in UNSAFE_RULES:
                    if rule["pattern"].search(line):
                        matches.append(
                            {
                                "rule": rule["id"],
                                "severity": _downgrade(rule["severity"], rel),
                                "file": rel,
                                "line": line_number,
                                "excerpt": line.strip()[:180],
                            }
                        )
                if len(matches) >= 30:
                    break
            if len(matches) >= 30:
                break
        evidence = self._save(
            "unsafe-pattern-scan",
            {"matches": matches},
            check,
            "High-risk source pattern matches.",
        )
        outcome = CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="No high-risk source patterns matched.",
            evidence=[evidence],
            metadata={"matches": len(matches)},
        )
        if not matches:
            return outcome
        outcome.actual = "{} high-risk source pattern match(es) found.".format(len(matches))
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in matches:
            key = (item["rule"], item["file"])
            if key not in grouped:
                grouped[key] = dict(item)
                grouped[key]["count"] = 0
            grouped[key]["count"] += 1
        rule_meta = {rule["id"]: rule for rule in UNSAFE_RULES}
        for (rule_id, rel), item in list(grouped.items())[:8]:
            rule = rule_meta[rule_id]
            outcome.add_finding(
                title="{} ({} occurrence{} in {})".format(
                    rule["title"], item["count"], "" if item["count"] == 1 else "s", rel
                ),
                severity=item["severity"],
                domain="security",
                consequence=rule["consequence"],
                location="{}:{}".format(rel, item["line"]),
                reproduction="Inspect the cited line and trace whether untrusted input can reach it.",
                remediation=rule["remediation"],
                verification=rule["verification"],
                tags=["unsafe-pattern", rule_id],
            )
        return outcome

    def check_static_dependencies(self, check: Check) -> CheckOutcome:
        payload = {
            "manifests": self.inventory.manifests,
            "lockfiles": self.inventory.lockfiles,
            "env_files": self.inventory.env_files,
        }
        evidence = self._save(
            "dependency-and-env-inventory",
            payload,
            check,
            "Dependency lockfile and committed environment-file inventory.",
        )
        outcome = CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="Manifests: {}; lockfiles: {}; committed env files: {}".format(
                len(self.inventory.manifests),
                len(self.inventory.lockfiles),
                len(self.inventory.env_files),
            ),
            evidence=[evidence],
        )
        if self.inventory.env_files:
            for rel in self.inventory.env_files[:3]:
                outcome.add_finding(
                    title="Local environment file is present in the repository",
                    severity=Severity.HIGH.value,
                    domain="data_integrity",
                    consequence="The file may contain live credentials, customer endpoints, or machine-specific settings.",
                    location=rel,
                    reproduction="Inspect the file's tracked status and confirm whether it contains secrets.",
                    remediation="Remove the file from tracking, rotate any exposed values, and ship a .env.example instead.",
                    verification="Confirm .gitignore covers local env files and the value is revoked.",
                    tags=["secrets", "configuration"],
                )
        if self.inventory.manifests and not self.inventory.lockfiles:
            outcome.add_finding(
                title="Package manifests exist without a lockfile",
                severity=Severity.LOW.value,
                domain="data_integrity",
                consequence="Builds can resolve different dependency versions and produce non-reproducible evidence.",
                location=", ".join(self.inventory.manifests[:4]),
                reproduction="Compare the manifest inventory with the lockfile inventory.",
                remediation="Commit the ecosystem lockfile and use a frozen install in CI.",
                verification="Run two clean installs and confirm the resolved dependency set is identical.",
                tags=["reproducibility"],
            )
        return outcome

    def check_static_full_assessment(self, check: Check) -> CheckOutcome:
        payload = {
            "category": "full",
            "intent": check.intent,
            "expected": check.expected,
            "source_available": self.inventory.available,
            "source_files": len(self.inventory.source_files),
            "test_files": len(self.inventory.test_files),
            "markup_files": len(self.inventory.markup_files),
            "manifests": len(self.inventory.manifests),
        }
        evidence = self._save(
            "full-assessment-scope",
            payload,
            check,
            "Full category assessment scope and inventory summary.",
        )
        return CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="Full category assessment recorded for {} source file(s).".format(
                len(self.inventory.source_files)
            ),
            evidence=[evidence],
            metadata={"category": "full"},
        )

    def check_static_test_category(self, check: Check) -> CheckOutcome:
        payload = {
            "category": "test",
            "intent": check.intent,
            "expected": check.expected,
            "source_available": self.inventory.available,
            "source_files": len(self.inventory.source_files),
            "test_files": len(self.inventory.test_files),
            "test_frameworks": self.inventory.test_frameworks,
            "manifests": len(self.inventory.manifests),
        }
        evidence = self._save(
            "test-category-scope",
            payload,
            check,
            "Test category assessment scope and test inventory summary.",
        )
        return CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="Test category assessment recorded with {} test file(s).".format(
                len(self.inventory.test_files)
            ),
            evidence=[evidence],
            metadata={"category": "test"},
        )

    def check_static_functionality_category(self, check: Check) -> CheckOutcome:
        payload = {
            "category": "functionality",
            "intent": check.intent,
            "expected": check.expected,
            "source_available": self.inventory.available,
            "source_files": len(self.inventory.source_files),
            "test_files": len(self.inventory.test_files),
            "test_frameworks": self.inventory.test_frameworks,
            "manifests": len(self.inventory.manifests),
        }
        evidence = self._save(
            "functionality-category-scope",
            payload,
            check,
            "Functionality category assessment scope and inventory summary.",
        )
        return CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="Functionality category assessment recorded for {} source file(s).".format(
                len(self.inventory.source_files)
            ),
            evidence=[evidence],
            metadata={"category": "functionality"},
        )

    def check_static_browser_category(self, check: Check) -> CheckOutcome:
        payload = {
            "category": "browser",
            "intent": check.intent,
            "expected": check.expected,
            "source_available": self.inventory.available,
            "source_files": len(self.inventory.source_files),
            "markup_files": len(self.inventory.markup_files),
            "manifests": len(self.inventory.manifests),
        }
        evidence = self._save(
            "browser-category-scope",
            payload,
            check,
            "Browser category assessment scope and inventory summary.",
        )
        return CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="Browser category assessment recorded for {} source file(s).".format(
                len(self.inventory.source_files)
            ),
            evidence=[evidence],
            metadata={"category": "browser"},
        )

    def check_static_issues_category(self, check: Check) -> CheckOutcome:
        marker_counts: Dict[str, int] = {}
        marker_files: List[str] = []
        for rel, path in self._iter_files(CODE_EXTENSIONS):
            text = self._read(path)
            if not text:
                continue
            matches = ISSUE_MARKER_PATTERN.findall(text)
            if not matches:
                continue
            marker_files.append(rel)
            for marker in matches:
                key = marker.upper()
                marker_counts[key] = marker_counts.get(key, 0) + 1
        payload = {
            "category": "issues",
            "intent": check.intent,
            "expected": check.expected,
            "source_available": self.inventory.available,
            "source_files": len(self.inventory.source_files),
            "test_files": len(self.inventory.test_files),
            "manifests": len(self.inventory.manifests),
            "issue_markers": marker_counts,
            "files_with_issue_markers": marker_files[:20],
        }
        evidence = self._save(
            "issues-category-scope",
            payload,
            check,
            "Issues category assessment scope and issue-marker inventory summary.",
        )
        marker_total = sum(marker_counts.values())
        return CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="Issues category assessment recorded with {} issue marker(s) in {} file(s).".format(
                marker_total, len(marker_files)
            ),
            evidence=[evidence],
            metadata={
                "category": "issues",
                "issue_markers": marker_total,
                "files_with_issue_markers": len(marker_files),
            },
        )

    def check_static_accessibility(self, check: Check) -> CheckOutcome:
        matches: List[Dict[str, Any]] = []
        for rel, path in self._iter_files(MARKUP_EXTENSIONS):
            text = self._read(path)
            if not text:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                for rule in A11Y_RULES:
                    if rule["pattern"].search(line):
                        matches.append(
                            {
                                "rule": rule["id"],
                                "severity": _downgrade(rule["severity"], rel),
                                "file": rel,
                                "line": line_number,
                                "excerpt": line.strip()[:180],
                            }
                        )
                if len(matches) >= 30:
                    break
            if len(matches) >= 30:
                break
        evidence = self._save(
            "source-accessibility-scan",
            {"matches": matches, "files_scanned": self.inventory.markup_files},
            check,
            "Static accessibility heuristics on markup and component templates.",
        )
        outcome = CheckOutcome(
            status=CheckStatus.PASSED.value,
            actual="No obvious source-level accessibility barriers matched.",
            evidence=[evidence],
            metadata={"matches": len(matches)},
        )
        if not matches:
            return outcome
        outcome.actual = "{} source-level accessibility match(es) across {} markup file(s).".format(
            len(matches), len(self.inventory.markup_files)
        )
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in matches:
            key = (item["rule"], item["file"])
            if key not in grouped:
                grouped[key] = dict(item)
                grouped[key]["count"] = 0
            grouped[key]["count"] += 1
        rule_meta = {rule["id"]: rule for rule in A11Y_RULES}
        for (rule_id, rel), item in list(grouped.items())[:8]:
            rule = rule_meta[rule_id]
            outcome.add_finding(
                title="{} ({} occurrence{} in {})".format(
                    rule["title"], item["count"], "" if item["count"] == 1 else "s", rel
                ),
                severity=item["severity"],
                domain="accessibility",
                consequence=rule["consequence"],
                location="{}:{}".format(rel, item["line"]),
                reproduction="Inspect the cited markup and verify the rendered accessibility tree.",
                remediation=rule["remediation"],
                verification=rule["verification"],
                tags=["accessibility", rule_id],
            )
        return outcome
