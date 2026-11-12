"""Post or update a concise quality-harness PR comment."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- quality-harness -->"


def _load_report(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _render(report: dict, run_url: str, artifact_name: str) -> str:
    confidence = report.get("confidence", {})
    coverage = report.get("coverage", {})
    decision = report.get("decision", {})
    findings = report.get("findings", [])
    trend = report.get("trend", {})
    new_findings = list(trend.get("new", []))
    severity_counts = report.get("finding_counts", {})
    lines = [
        MARKER,
        "## Quality Harness",
        "",
        "- **Score:** {}/100".format(confidence.get("score", 0)),
        "- **Decision:** {}".format(decision.get("decision", "unknown")),
        "- **Coverage:** {}% ({} executed of {})".format(
            coverage.get("executed_pct", 0),
            coverage.get("executed", 0),
            coverage.get("total_checks", 0),
        ),
        "- **Findings:** {} total ({})".format(
            len(findings),
            ", ".join(
                "{} {}".format(count, severity)
                for severity, count in sorted(severity_counts.items())
                if count
            )
            or "none",
        ),
    ]
    if new_findings:
        lines.append("- **New findings:** {}".format(", ".join(str(item) for item in new_findings[:10])))
    else:
        lines.append("- **New findings:** none")
    lines.extend(
        [
            "",
            "Evidence and the full JSON report are available in the workflow artifacts.",
            "Artifact: `{}`".format(artifact_name),
        ]
    )
    if run_url:
        lines.append("Run: {}".format(run_url))
    return "\n".join(lines) + "\n"


def _api_request(method: str, url: str, token: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", "Bearer {}".format(token))
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    pr_number = os.environ.get("PR_NUMBER", "").strip()
    report_path = os.environ.get("REPORT_PATH", "").strip()
    run_url = os.environ.get("RUN_URL", "").strip()
    artifact_name = os.environ.get("ARTIFACT_NAME", "quality-harness-evidence").strip()

    if not token or not repository or not pr_number or not report_path:
        print("Skipping PR comment: missing GitHub context.")
        return 0

    try:
        report = _load_report(report_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("Skipping PR comment: could not read report: {}".format(exc))
        return 0

    body = _render(report, run_url, artifact_name)
    base = "https://api.github.com/repos/{}".format(repository)
    comments_url = "{}/issues/{}/comments".format(base, pr_number)

    try:
        comments = _api_request("GET", comments_url, token)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        print("Skipping PR comment: could not list comments: {}".format(exc))
        return 0

    existing = None
    for comment in comments:
        if MARKER in str(comment.get("body", "")):
            existing = comment
            break

    payload = {"body": body}
    try:
        if existing is not None:
            _api_request("PATCH", "{}/issues/comments/{}".format(base, existing["id"]), token, payload)
            print("Updated existing quality-harness PR comment.")
        else:
            _api_request("POST", comments_url, token, payload)
            print("Created quality-harness PR comment.")
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print("Skipping PR comment: could not write comment: {}".format(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY