"""SARIF, JUnit, and detached report-signature helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

from .browser import redact_text


SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"


_SENSITIVE_KEY = re.compile(
    r"(?i)((?:authorization|cookie|set-cookie|token|secret|password|passwd|api[_-]?key|session|jwt)\s*[=:]\s*)[^\s,;]{3,}"
)


def _redact_string(value: str) -> str:
    text = _SENSITIVE_KEY.sub(lambda match: match.group(1) + "[REDACTED]", value)
    return redact_text(text)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _sarif_level(severity: str) -> str:
    severity = str(severity or "").lower()
    if severity in ("critical", "high"):
        return "error"
    if severity == "medium":
        return "warning"
    return "note"


def build_sarif(report: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a quality-harness report into a SARIF 2.1.0 document."""

    report = _redact(report)
    findings = report.get("findings", []) or []
    rules: List[Dict[str, Any]] = []
    rule_ids: set = set()
    results: List[Dict[str, Any]] = []

    for finding in findings:
        check_id = str(finding.get("check_id") or finding.get("id") or "finding")
        rule_id = "quality-harness/{}".format(check_id)
        if rule_id not in rule_ids:
            rule_ids.add(rule_id)
            rules.append(
                {
                    "id": rule_id,
                    "name": check_id,
                    "shortDescription": {"text": str(finding.get("title", check_id))},
                    "fullDescription": {"text": str(finding.get("consequence", ""))},
                    "help": {"text": str(finding.get("remediation", ""))},
                    "defaultConfiguration": {"level": _sarif_level(finding.get("severity", ""))},
                    "properties": {
                        "severity": finding.get("severity"),
                        "domain": finding.get("domain"),
                        "tags": finding.get("tags", []),
                    },
                }
            )
        location_uri = str(finding.get("location", "") or "")
        result = {
            "ruleId": rule_id,
            "level": _sarif_level(finding.get("severity", "")),
            "message": {
                "text": "{}\n\nConsequence: {}\nRemediation: {}".format(
                    finding.get("title", ""),
                    finding.get("consequence", ""),
                    finding.get("remediation", ""),
                )
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": location_uri},
                    }
                }
            ],
            "properties": {
                "fingerprint": finding.get("fingerprint", ""),
                "persona": finding.get("persona", ""),
                "strength": finding.get("strength", ""),
            },
        }
        if finding.get("evidence"):
            result["relatedLocations"] = [
                {
                    "id": index,
                    "physicalLocation": {"artifactLocation": {"uri": str(path)}},
                }
                for index, path in enumerate(finding.get("evidence", []))
            ]
        results.append(result)

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "quality-harness",
                        "informationUri": "https://github.com/hbahuguna/assay",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def build_junit(report: Dict[str, Any]) -> str:
    """Convert a quality-harness report into a JUnit XML string."""

    report = _redact(report)
    checks = report.get("checks", []) or []
    failures = sum(1 for check in checks if check.get("status") == "failed")
    errors = sum(1 for check in checks if check.get("status") in ("error", "blocked"))
    skipped = sum(1 for check in checks if check.get("status") in ("skipped", "blocked"))
    suite = ET.Element(
        "testsuite",
        {
            "name": "quality-harness",
            "tests": str(len(checks)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
            "time": "0",
        },
    )
    for check in checks:
        status = str(check.get("status", "planned"))
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "name": str(check.get("id", "")),
                "classname": str(check.get("domain", "")),
                "time": "0",
                "status": status,
            },
        )
        actual = str(check.get("actual", ""))
        if status == "failed":
            failure = ET.SubElement(case, "failure", {"message": actual})
            failure.text = actual
        elif status == "error":
            error = ET.SubElement(case, "error", {"message": actual})
            error.text = actual
        elif status == "blocked":
            skipped_element = ET.SubElement(case, "skipped", {"message": "blocked: {}".format(actual)})
            skipped_element.text = "blocked: {}".format(actual)
        elif status == "skipped":
            skipped_element = ET.SubElement(case, "skipped", {"message": actual})
            skipped_element.text = actual
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(suite, encoding="unicode")


def canonical_report_bytes(report: Dict[str, Any]) -> bytes:
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_report(report: Dict[str, Any], key: Any) -> str:
    key_bytes = key.encode("utf-8") if isinstance(key, str) else bytes(key)
    return hmac.new(key_bytes, canonical_report_bytes(report), hashlib.sha256).hexdigest()


def verify_report_signature(report: Dict[str, Any], signature: str, key: Any) -> bool:
    key_bytes = key.encode("utf-8") if isinstance(key, str) else bytes(key)
    expected = hmac.new(key_bytes, canonical_report_bytes(report), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature or ""))