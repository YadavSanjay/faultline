"""Hand-built gate results and hand oracles for the gate-findings-recorded spec tests.

Everything is synthetic (docs/specs/gate-findings-recorded.md, Notes). ``mixed_result`` is a failed
gate result whose findings interleave ``"fail"``, ``"flag"`` and two other severities, so the
grouping and the stability within groups are both visible; ``mixed_entry`` is Requirement 1 applied
to it by hand (it never calls the code under test) and is also the hand-built input for the
``held_out_summary`` tests.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from src.scenariogen.plausibility import PlausibilityFinding, PlausibilityResult


def mixed_result() -> PlausibilityResult:
    """A failed result; findings in gate order: flag, fail, info, flag, fail, warn."""
    return PlausibilityResult(
        scenario="ai_deflation",
        status="fail",
        findings=[
            PlausibilityFinding("EQ_SPX", "envelope", -0.2345678, 0.15, "flag"),
            PlausibilityFinding("IR_USD_10Y", "bound", 0.0123456, 0.01, "fail"),
            PlausibilityFinding("EQ_NVDA", "market_implied", -0.45, 0.01, "info"),
            PlausibilityFinding("FX_EURUSD", "envelope", 0.0812345, 0.06, "flag"),
            PlausibilityFinding("CR_IG", "bound", 250.0, 200.0, "fail"),
            PlausibilityFinding("VOL_VIX", "bound", 12345.6, 10000.0, "warn"),
        ],
    )


def mixed_findings_sorted() -> list[dict[str, Any]]:
    """Requirement 1 by hand: fails (IR_USD_10Y, CR_IG), flags (EQ_SPX, FX_EURUSD), others (EQ_NVDA, VOL_VIX)."""
    return [
        {"factor": "IR_USD_10Y", "check": "bound", "observed": 0.0123456, "limit": 0.01, "severity": "fail"},
        {"factor": "CR_IG", "check": "bound", "observed": 250.0, "limit": 200.0, "severity": "fail"},
        {"factor": "EQ_SPX", "check": "envelope", "observed": -0.2345678, "limit": 0.15, "severity": "flag"},
        {"factor": "FX_EURUSD", "check": "envelope", "observed": 0.0812345, "limit": 0.06, "severity": "flag"},
        {"factor": "EQ_NVDA", "check": "market_implied", "observed": -0.45, "limit": 0.01, "severity": "info"},
        {"factor": "VOL_VIX", "check": "bound", "observed": 12345.6, "limit": 10000.0, "severity": "warn"},
    ]


def mixed_entry() -> dict[str, Any]:
    """The hand-built ``held_out_entry("ai_deflation", "severe", "v3", mixed_result())``."""
    return {
        "scenario": "ai_deflation",
        "severity": "severe",
        "scenario_version": "v3",
        "status": "fail",
        "findings": mixed_findings_sorted(),
    }


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        mixed_result=mixed_result,
        mixed_findings_sorted=mixed_findings_sorted,
        mixed_entry=mixed_entry,
    )
