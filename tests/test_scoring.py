"""Confidence-score math, caps, and gap handling."""

import unittest

from harness.models import Check, CheckStatus, Finding
from harness.scoring import FORMULA_VERSION, score_confidence


def check(check_id, weight, kind="static", status=CheckStatus.PASSED.value, domain="functional"):
    return Check(
        id=check_id,
        title=check_id,
        domain=domain,
        persona="functional",
        kind=kind,
        intent="test",
        expected="test",
        base_weight=weight,
        weight=weight,
        status=status,
    )


def finding(severity, title="Same finding", location="src/app.py:1", fingerprint=""):
    return Finding(
        id="finding-{}".format(severity),
        title=title,
        severity=severity,
        domain="functional",
        persona="functional",
        strength="observed",
        consequence="A customer cannot finish the task.",
        location=location,
        fingerprint=fingerprint,
    )


class ScoringTests(unittest.TestCase):
    def test_no_checks_cannot_claim_confidence(self):
        confidence = score_confidence([], [])
        self.assertEqual(confidence.score, 0.0)
        self.assertEqual(confidence.gate["status"], "fail")
        self.assertEqual(confidence.breakdown.get("planned_checks"), 0)

    def test_full_coverage_and_all_passes_scores_one_hundred(self):
        checks = [
            check("a", 1.0, kind="static"),
            check("b", 1.0, kind="browser", domain="reliability"),
        ]
        confidence = score_confidence(checks, [])
        self.assertEqual(confidence.score, 100.0)
        self.assertEqual(confidence.band, "high")
        self.assertEqual(confidence.gate["status"], "pass")
        self.assertEqual(confidence.coverage, 100.0)
        self.assertEqual(confidence.breakdown["formula"], FORMULA_VERSION)

    def test_source_only_scope_applies_a_penalty_factor(self):
        confidence = score_confidence([check("a", 1.0)], [])
        self.assertEqual(confidence.score, 90.0)
        self.assertEqual(confidence.breakdown["scope_mode"], "source")
        self.assertEqual(confidence.breakdown["scope_factor"], 0.9)

    def test_high_finding_subtracts_twenty_points(self):
        checks = [
            check("a", 1.0, kind="static"),
            check("b", 1.0, kind="browser"),
        ]
        confidence = score_confidence(checks, [finding("high")])
        self.assertEqual(confidence.score, 80.0)
        self.assertEqual(confidence.penalties["severity_total"], 20.0)

    def test_critical_finding_caps_the_score_at_thirty(self):
        confidence = score_confidence([check("a", 1.0, kind="browser")], [finding("critical")])
        self.assertEqual(confidence.score, 30.0)
        self.assertEqual(confidence.gate["status"], "fail")
        self.assertIn("critical finding", [item["reason"] for item in confidence.penalties["caps"]])

    def test_duplicate_fingerprints_are_penalised_once(self):
        checks = [
            check("a", 1.0, kind="static"),
            check("b", 1.0, kind="browser"),
        ]
        findings = [
            finding("high", fingerprint="same"),
            finding("high", title="Duplicate wording", fingerprint="same"),
        ]
        confidence = score_confidence(checks, findings)
        self.assertEqual(confidence.penalties["severity_total"], 20.0)
        self.assertEqual(confidence.score, 80.0)

    def test_suspected_findings_carry_half_penalty(self):
        checks = [
            check("a", 1.0, kind="static"),
            check("b", 1.0, kind="browser"),
        ]
        suspected = finding("high")
        suspected.strength = "suspected"
        confidence = score_confidence(checks, [suspected])
        self.assertEqual(confidence.score, 90.0)

    def test_blocked_check_is_a_coverage_gap_not_a_pass(self):
        checks = [
            check("a", 1.0, kind="browser"),
            check("b", 1.0, kind="browser", status=CheckStatus.BLOCKED.value),
        ]
        confidence = score_confidence(checks, [])
        self.assertEqual(confidence.coverage, 50.0)
        self.assertEqual(confidence.score, 56.2)
        self.assertEqual(confidence.gate["status"], "fail")
        self.assertTrue(any("blocked" in note for note in confidence.limitations))

    def test_halted_runs_are_capped_at_twenty_five(self):
        confidence = score_confidence(
            [check("a", 1.0, kind="browser")],
            [],
            halted=True,
            kill_reason="critical finding",
        )
        self.assertEqual(confidence.score, 25.0)
        self.assertIn("kill switch halted execution", [item["reason"] for item in confidence.penalties["caps"]])

    def test_low_score_band_and_reasons_are_explicit(self):
        checks = [
            check("a", 1.0, kind="browser"),
            check("b", 1.0, kind="browser", status=CheckStatus.FAILED.value),
        ]
        confidence = score_confidence(checks, [finding("medium")])
        self.assertLessEqual(confidence.score, 60.0)
        self.assertTrue(confidence.gate["reasons"])
        self.assertTrue(confidence.rationale)


if __name__ == "__main__":
    unittest.main()
