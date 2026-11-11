from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .models import Check, CheckStatus, Finding, Severity
from .scoring import score_confidence


CALIBRATION_SCHEMA = "quality-harness.calibration/v1"


def _check_from_fixture(item: Dict[str, Any]) -> Check:
    weight = float(item.get("weight", 1.0))
    return Check(
        id=str(item["id"]),
        title=str(item.get("title", item["id"])),
        domain=str(item.get("domain", "functional")),
        persona=str(item.get("persona", "functional")),
        kind=str(item.get("kind", "static")),
        intent="calibration fixture",
        expected="calibration fixture",
        base_weight=weight,
        weight=weight,
        status=str(item.get("status", CheckStatus.PASSED.value)),
    )


def _finding_from_fixture(item: Dict[str, Any]) -> Finding:
    return Finding(
        id=str(item.get("id", "finding-{}".format(item.get("severity", "medium")))),
        title=str(item.get("title", "Calibration finding")),
        severity=str(item.get("severity", Severity.MEDIUM.value)),
        domain=str(item.get("domain", "functional")),
        persona=str(item.get("persona", "functional")),
        strength=str(item.get("strength", "observed")),
        consequence=str(item.get("consequence", "A customer cannot complete the task.")),
        location=str(item.get("location", "src/app.py")),
        fingerprint=str(item.get("fingerprint", "")),
    )


def load_calibration_fixtures(directory: Path) -> List[Dict[str, Any]]:
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("Calibration fixture directory not found: {}".format(directory))
    fixtures: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("_path", str(path))
        fixtures.append(data)
    if not fixtures:
        raise ValueError("No calibration fixtures found in {}".format(directory))
    return fixtures


def decision_from_confidence(confidence: Any, halted: bool = False, status: str = "completed") -> str:
    if status == "halted":
        return "hold"
    if status == "failed":
        return "hold"
    gate_status = str(confidence.gate.get("status", "warn"))
    if gate_status == "pass":
        return "ship"
    if gate_status == "fail":
        return "hold"
    return "review"


def run_calibration(directory: Path) -> Dict[str, Any]:
    fixtures = load_calibration_fixtures(directory)
    results: List[Dict[str, Any]] = []
    counts = {"ship": 0, "review": 0, "hold": 0}
    mismatches: List[str] = []
    intentional: List[str] = []

    for fixture in fixtures:
        checks = [_check_from_fixture(item) for item in fixture.get("checks", [])]
        findings = [_finding_from_fixture(item) for item in fixture.get("findings", [])]
        confidence = score_confidence(
            checks,
            findings,
            halted=bool(fixture.get("halted", False)),
        )
        actual_decision = decision_from_confidence(
            confidence,
            halted=bool(fixture.get("halted", False)),
            status=str(fixture.get("status", "completed")),
        )
        expected_decision = str(fixture.get("expected_decision", ""))
        expected_gate = str(fixture.get("expected_gate", ""))
        gate_status = str(confidence.gate.get("status", ""))
        match = actual_decision == expected_decision
        gate_match = not expected_gate or gate_status == expected_gate
        if not match:
            mismatches.append(str(fixture.get("id", fixture.get("_path"))))
        if fixture.get("intentional_disagreement"):
            intentional.append(str(fixture.get("id", fixture.get("_path"))))
        counts[actual_decision] = counts.get(actual_decision, 0) + 1
        results.append(
            {
                "id": fixture.get("id"),
                "label": fixture.get("label", ""),
                "description": fixture.get("description", ""),
                "expected_decision": expected_decision,
                "expected_gate": expected_gate,
                "actual_decision": actual_decision,
                "decision_match": match,
                "gate_match": gate_match,
                "intentional_disagreement": bool(fixture.get("intentional_disagreement", False)),
                "human_note": fixture.get("human_note", ""),
                "score": confidence.score,
                "band": confidence.band,
                "gate": confidence.gate.get("status"),
                "gate_reasons": list(confidence.gate.get("reasons", [])),
                "coverage": confidence.coverage,
                "breakdown": confidence.breakdown,
                "penalties": confidence.penalties,
                "domain_scores": confidence.domain_scores,
            }
        )

    return {
        "schema_version": CALIBRATION_SCHEMA,
        "formula": "weighted-coverage-v1",
        "fixture_count": len(fixtures),
        "fixtures": results,
        "summary": {
            "expected_decisions": {
                decision: sum(1 for fixture in fixtures if fixture.get("expected_decision") == decision)
                for decision in ("ship", "review", "hold")
            },
            "actual_decisions": counts,
            "decision_matches": sum(1 for result in results if result["decision_match"]),
            "decision_mismatches": len(mismatches),
            "mismatched_fixtures": mismatches,
            "intentional_disagreements": intentional,
        },
    }


def write_calibration_report(fixtures_dir: Path, output: Path) -> Dict[str, Any]:
    report = run_calibration(fixtures_dir)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report