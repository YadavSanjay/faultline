"""Unit tests for gate-findings-recorded wiring: the stress report carries the held-out tiers."""

from __future__ import annotations

from src.scenariogen.plausibility import PlausibilityFinding, PlausibilityResult
from src.scenariogen.report import StressReport, held_out_entry, held_out_summary


def test_stress_report_serialises_held_out_tiers_with_their_findings():
    result = PlausibilityResult("ai_deflation", status="fail", findings=[
        PlausibilityFinding("eqvol", "envelope", 0.5, 0.3, "flag"),
        PlausibilityFinding("eq:MU", "bound", -97.0, 96.0, "fail")])
    report = StressReport("2026-10-06", "run", "snap")
    entry = held_out_entry("ai_deflation", "mild", "v1", result)
    report.held_out.append(entry)
    out = report.to_dict()["held_out"]
    assert out == [entry] and out[0]["findings"][0]["factor"] == "eq:MU"        # fail first
    assert held_out_summary(entry) == "1 fail / 2 finding(s): eq:MU bound -97 vs 96; eqvol envelope 0.5 vs 0.3"
    assert StressReport("d", "r", "s").to_dict()["held_out"] == []
