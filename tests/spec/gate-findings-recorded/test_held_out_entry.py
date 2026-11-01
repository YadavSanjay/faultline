"""gate-findings-recorded C1: ``report.held_out_entry`` records a held-out tier and its findings.

Oracle: Requirement 1 by hand: ``{"scenario", "severity", "scenario_version", "status", "findings"}``
with ``status = result.status`` and ``findings = [f.as_dict() for f in result.findings]`` reordered
fails first, then flags, then any other severity, stable within each group; the input is not mutated.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.plausibility import PlausibilityFinding, PlausibilityResult
from src.scenariogen.report import held_out_entry

FINITE = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6)
FINDINGS = st.lists(
    st.builds(
        PlausibilityFinding,
        factor=st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789", min_size=1, max_size=12),
        check=st.sampled_from(["bound", "envelope", "market_implied"]),
        observed=FINITE,
        limit=FINITE,
        severity=st.sampled_from(["fail", "flag", "info", "warn"]),
    ),
    max_size=12,
)
STATUSES = st.sampled_from(["pass", "flag", "fail"])


def _group(severity: str) -> int:
    """Requirement 1's three groups: fail, flag, any other severity."""
    if severity == "fail":
        return 0
    if severity == "flag":
        return 1
    return 2


@pytest.mark.criterion("C1")
def test_c1_point_mixed_findings_fails_then_flags_then_others(h):
    # oracle: Requirement 1 by hand (conftest.mixed_findings_sorted): gate order flag, fail, info, flag,
    #   fail, warn -> fails IR_USD_10Y, CR_IG; flags EQ_SPX, FX_EURUSD; others EQ_NVDA (info), VOL_VIX
    #   (warn), each group in gate order; status "fail" copied; floats passed through unrounded.
    result = h.mixed_result()
    entry = held_out_entry("ai_deflation", "severe", "v3", result)
    assert entry == {
        "scenario": "ai_deflation",
        "severity": "severe",
        "scenario_version": "v3",
        "status": "fail",
        "findings": h.mixed_findings_sorted(),
    }


@pytest.mark.criterion("C1")
def test_c1_point_no_findings_gives_empty_list():
    # oracle: Requirement 1 by hand: a result with no findings -> findings == [], status copied ("pass").
    result = PlausibilityResult(scenario="soft_landing", status="pass", findings=[])
    entry = held_out_entry("soft_landing", "adverse", "v1", result)
    assert entry == {
        "scenario": "soft_landing",
        "severity": "adverse",
        "scenario_version": "v1",
        "status": "pass",
        "findings": [],
    }


@pytest.mark.criterion("C1")
def test_c1_point_status_is_copied_not_derived():
    # oracle: Requirement 1 "status is result.status": a "flag" result keeps "flag"; the single finding
    #   is its as_dict() written out by hand.
    result = PlausibilityResult(
        scenario="taiwan", status="flag",
        findings=[PlausibilityFinding("EQ_TSM", "envelope", -0.61, 0.5, "flag")],
    )
    entry = held_out_entry("taiwan", "adverse", "v2", result)
    assert entry == {
        "scenario": "taiwan",
        "severity": "adverse",
        "scenario_version": "v2",
        "status": "flag",
        "findings": [{"factor": "EQ_TSM", "check": "envelope", "observed": -0.61, "limit": 0.5,
                      "severity": "flag"}],
    }


@pytest.mark.criterion("C1")
def test_c1_point_input_not_mutated(h):
    # oracle: Requirement 1 "The input is not mutated": the result equals a deepcopy taken before the
    #   call (finding order included), and the entry holds a list other than the result's own.
    result = h.mixed_result()
    before = copy.deepcopy(result)
    entry = held_out_entry("ai_deflation", "severe", "v3", result)
    assert result == before
    assert entry["findings"] is not result.findings
    assert entry["findings"] == h.mixed_findings_sorted()


@pytest.mark.criterion("C1")
@given(findings=FINDINGS, status=STATUSES)
def test_c1_property_permutation_and_fails_first(findings, status):
    # oracle: Requirement 1: findings are a permutation of [f.as_dict() for f in result.findings] (the
    #   existing as_dict, not the code under test), no "fail" follows a non-"fail", each group keeps its
    #   input order, status is copied and the input is not mutated.
    result = PlausibilityResult(scenario="s", status=status, findings=list(findings))
    before = copy.deepcopy(result)
    entry = held_out_entry("s", "severe", "v9", result)
    got = entry["findings"]
    source = [f.as_dict() for f in before.findings]

    remaining = list(source)
    for d in got:
        assert d in remaining
        remaining.remove(d)
    assert remaining == []

    groups = [_group(d["severity"]) for d in got]
    assert groups == sorted(groups)
    for g in (0, 1, 2):
        assert [d for d in got if _group(d["severity"]) == g] == [d for d in source if _group(d["severity"]) == g]

    assert entry["status"] == status
    assert result == before
