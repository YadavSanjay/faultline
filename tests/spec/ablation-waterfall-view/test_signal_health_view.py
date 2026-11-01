"""ablation-waterfall-view C4: ``report_views.signal_health_view`` builds the signal-health view.

Oracle: Requirement 4 by hand: ``available`` iff ``signal_health`` is present and not None; overall,
counts and sources copied (sources worst-first as stored, each with ``n_holds`` = sum of its holds);
held-out entries copied with ``n_findings``, ``n_fail``, ``n_flag``; stopped copied; ``attention`` =
down + degraded sources + held out + stopped. Inputs are hand-written report dicts (conftest).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.report_views import signal_health_view


@pytest.mark.criterion("C4")
def test_c4_point_report_with_signal_health(h):
    # oracle: Requirement 4 by hand (conftest.health_view): n_holds macrorag 2 + 1 = 3, options 1, news 0,
    #   gpr 0; ai_deflation 4 findings (2 fail, 1 flag, 1 info), taiwan 1 (0 fail, 1 flag); attention =
    #   1 down + 1 degraded + 2 held out + 1 stopped = 5.
    h.assert_close(signal_health_view(h.health_report()), h.health_view())


@pytest.mark.criterion("C4")
def test_c4_point_report_without_signal_health(h):
    # oracle: Requirement 4 by hand: no signal_health key (a pre-15d run) -> available false, overall and
    #   counts None, sources []; held_out (1 entry: ai_deflation 4 / 2 / 1) and stopped (2 entries) still
    #   built; attention = 0 + 1 + 2 = 3.
    rep = h.health_report()
    del rep["signal_health"]
    rep["held_out"] = h.held_out_list()[:1]
    stopped = [*h.stopped_list(), {"scenario": "tariff_shock", "reason": "replay_uncovered", "detail": "x"}]
    rep["stopped"] = copy.deepcopy(stopped)
    expected = {"available": False, "run_id": "run-7", "valuation_date": "2026-10-06",
                "generated_at": "2026-10-18T11:00:00", "overall": None, "counts": None, "sources": [],
                "held_out": [{**h.held_out_list()[0], "n_findings": 4, "n_fail": 2, "n_flag": 1}],
                "stopped": stopped, "attention": 0 + 1 + 2}
    h.assert_close(signal_health_view(rep), expected)


@pytest.mark.criterion("C4")
def test_c4_point_signal_health_none_and_no_held_out_or_stopped(h):
    # oracle: Requirement 4 by hand: signal_health None -> available false; no held_out / stopped keys ->
    #   [] and [] -> attention 0 + 0 + 0 = 0 (with available false and the header copied).
    rep = {"run_id": "run-8", "valuation_date": "2026-10-06", "generated_at": "2026-10-18T12:00:00",
           "signal_health": None, "results": []}
    expected = {"available": False, "run_id": "run-8", "valuation_date": "2026-10-06",
                "generated_at": "2026-10-18T12:00:00", "overall": None, "counts": None, "sources": [],
                "held_out": [], "stopped": [], "attention": 0}
    h.assert_close(signal_health_view(rep), expected)


@pytest.mark.criterion("C4")
def test_c4_point_report_not_mutated(h):
    # oracle: Requirement 4 by hand: the extra keys (n_holds, n_findings ...) go on copies -> the report
    #   equals a deep copy taken before the call; the view is the hand view.
    rep = h.health_report()
    before = copy.deepcopy(rep)
    out = signal_health_view(rep)
    assert rep == before
    h.assert_close(out, h.health_view())


STATUS = st.sampled_from(["down", "degraded", "ok", "unused"])
HOLDS = st.dictionaries(st.sampled_from(["fallback", "gate_held", "stale"]), st.integers(1, 4), max_size=3)
FINDINGS = st.lists(st.sampled_from(["fail", "flag", "info", "warn"]), max_size=6)


@st.composite
def _health_report(draw):
    rep: dict = {"run_id": "r", "valuation_date": "2026-10-06", "generated_at": "2026-10-18T09:00:00"}
    if draw(st.booleans()):
        names = draw(st.lists(st.sampled_from(["macrorag", "xbrlrag", "news", "options", "gpr"]), unique=True))
        sources = [{"source": n, "status": draw(STATUS), "holds": draw(HOLDS)} for n in names]
        rep["signal_health"] = {"overall": "ok", "counts": {}, "sources": sources}
    if draw(st.booleans()):
        rep["held_out"] = [{"scenario": f"s{i}", "severity": "severe",
                            "findings": [{"check": "c", "severity": sev} for sev in draw(FINDINGS)]}
                           for i in range(draw(st.integers(0, 3)))]
    if draw(st.booleans()):
        rep["stopped"] = [{"scenario": f"t{i}", "reason": "x", "detail": ""} for i in range(draw(st.integers(0, 3)))]
    return rep


@pytest.mark.criterion("C4")
@given(rep=_health_report())
def test_c4_property_attention_counts(rep):
    # oracle: Requirement 4 by hand: attention = #sources with status down/degraded + len(held_out) +
    #   len(stopped), each defaulting to empty when absent.
    out = signal_health_view(rep)
    sources = (rep.get("signal_health") or {}).get("sources", [])
    expected = (sum(1 for s in sources if s["status"] in ("down", "degraded"))
                + len(rep.get("held_out", [])) + len(rep.get("stopped", [])))
    assert out["attention"] == expected
    assert out["available"] is ("signal_health" in rep)


@pytest.mark.criterion("C4")
@given(rep=_health_report())
def test_c4_property_fail_plus_flag_within_findings(rep):
    # oracle: Requirement 4 by hand: n_fail and n_flag count disjoint subsets of the findings, so
    #   n_fail + n_flag <= n_findings; n_findings = len(findings); one view entry per held-out entry.
    out = signal_health_view(rep)
    held = rep.get("held_out", [])
    assert len(out["held_out"]) == len(held)
    for e, src in zip(out["held_out"], held, strict=True):
        assert e["n_findings"] == len(src["findings"])
        assert e["n_fail"] + e["n_flag"] <= e["n_findings"]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(rep=_health_report())
def test_c4_property_input_not_mutated(rep):
    # oracle: Requirement 4 by hand: the report equals a deep copy taken before the call, and the view's
    #   n_holds are the sums of each source's holds counts.
    before = copy.deepcopy(rep)
    out = signal_health_view(rep)
    assert rep == before
    sources = (rep.get("signal_health") or {}).get("sources", [])
    assert [s["n_holds"] for s in out["sources"]] == [sum(s["holds"].values()) for s in sources]
