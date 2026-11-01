"""ablation-waterfall-view C3: ``report_views.ablation_view`` builds the view of one ablation report.

Oracle: Requirement 3 by hand: header fields copied (None when missing); one waterfall per scenario in
report order with ``layer_cost["rows"]`` (None without the block); ``composition_rules`` the sorted
distinct per-record rules, ``mixed_rules`` when there are two or more; ``stopped`` copied (default []);
``overhead`` from ``layer_cost`` (None without it). ValueError for a duplicate (scenario, severity) or an
invalid waterfall.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.ablation import LAYERS, ladder_attribution, shapley_attribution
from src.scenariogen.report_views import ablation_view, waterfall


@pytest.mark.criterion("C3")
def test_c3_point_two_scenarios_with_layer_cost_and_mixed_rules(h):
    # oracle: Requirement 3 by hand (conftest.ablation_report_view): taiwan (dualrag-v3) and ai_bust
    #   (dualrag-v1) -> rules sorted [dualrag-v1, dualrag-v3], mixed true (the top-level
    #   composition_rule is ignored); scenarios = the two hand waterfalls with the report's cost rows
    #   (ai_bust picks up only the 9-call ai_bust row); stopped and overhead copied.
    h.assert_close(ablation_view(h.ablation_report()), h.ablation_report_view())


@pytest.mark.criterion("C3")
def test_c3_point_without_layer_cost_and_stopped(h):
    # oracle: Requirement 3 by hand: no layer_cost -> every step's cost fields None and overhead None; no
    #   stopped -> []; no note -> None; one rule [dualrag-v3] -> mixed false.
    rep = h.ablation_report()
    del rep["layer_cost"], rep["stopped"], rep["note"]
    rep["scenarios"] = [h.taiwan_record()]
    expected = {"run_id": "abl-1", "valuation_date": "2026-10-06", "generated_at": "2026-10-18T09:00:00",
                "severity": "severe", "note": None, "composition_rules": ["dualrag-v3"], "mixed_rules": False,
                "scenarios": [h.taiwan_waterfall(with_cost=False)], "stopped": [], "overhead": None}
    h.assert_close(ablation_view(rep), expected)


@pytest.mark.criterion("C3")
def test_c3_point_layer_cost_none_counts_as_absent(h):
    # oracle: Requirement 3 by hand: "layer_cost" present but None -> cost_rows None (cost fields None)
    #   and overhead None; the rest as in the full hand view.
    rep = h.ablation_report()
    rep["layer_cost"] = None
    expected = h.ablation_report_view()
    expected["scenarios"] = [{**w, "steps": h.no_cost(w["steps"])} for w in expected["scenarios"]]
    expected["overhead"] = None
    h.assert_close(ablation_view(rep), expected)


@pytest.mark.criterion("C3")
def test_c3_point_duplicate_scenario_severity_raises(h):
    # oracle: Requirement 3 by hand: taiwan/severe twice -> ValueError (ai_bust/severe and taiwan/extreme
    #   would be distinct).
    rep = h.ablation_report()
    rep["scenarios"] = [h.taiwan_record(), h.ai_bust_record(), h.taiwan_record(with_subsets=False)]
    with pytest.raises(ValueError, match=r"\S"):
        ablation_view(rep)


@pytest.mark.criterion("C3")
def test_c3_point_invalid_ladder_propagates_value_error(h):
    # oracle: Requirement 3 by hand: the ai_bust record's ladder starts at "base", not "authored" ->
    #   waterfall's ValueError propagates.
    rep = h.ablation_report()
    rep["scenarios"][1]["ladder"][0]["layer"] = "base"
    with pytest.raises(ValueError, match=r"\S"):
        ablation_view(rep)


@pytest.mark.criterion("C3")
def test_c3_point_report_not_mutated(h):
    # oracle: Requirement 3 by hand (inputs of Requirement 2 are not mutated): the report equals a deep
    #   copy taken before the call; the view is the hand view.
    rep = h.ablation_report()
    before = copy.deepcopy(rep)
    out = ablation_view(rep)
    assert rep == before
    h.assert_close(out, h.ablation_report_view())


VALUE = st.integers(min_value=-800, max_value=800).map(lambda i: i / 4.0)


@st.composite
def _report(draw):
    """1-4 records over distinct (scenario, severity), order = LAYERS, random v(S), random rule, random
    layer_cost presence."""
    pairs = draw(st.lists(st.tuples(st.sampled_from(["a", "b", "c"]), st.sampled_from(["mild", "severe"])),
                          unique=True, min_size=1, max_size=4))
    order = list(LAYERS)
    records = []
    for scen, sev in pairs:
        vals = {}
        for mask in range(2 ** len(order)):
            vals[frozenset(x for i, x in enumerate(order) if mask >> i & 1)] = draw(VALUE)
        records.append({"scenario": scen, "severity": sev,
                        "composition_rule": draw(st.sampled_from(["dualrag-v1", "dualrag-v3"])),
                        "order": order,
                        "ladder": ladder_attribution(lambda s, v=vals: {"t": v[s]}, order),
                        "shapley": shapley_attribution(lambda s, v=vals: v[s], order)})
    rep = {"run_id": "r", "valuation_date": "2026-10-06", "generated_at": "2026-10-18T09:00:00",
           "severity": "severe", "scenarios": records}
    if draw(st.booleans()):
        rows = [{"scenario": scen, "severity": sev, "layer": layer, "n_calls": draw(st.integers(0, 3)),
                 "latency_s": draw(st.sampled_from([0.0, 0.5, 2.0])),
                 "cost_usd": draw(st.sampled_from([None, 0.0, 0.25]))}
                for scen, sev in pairs for layer in LAYERS]
        rep["layer_cost"] = {"rows": rows, "totals": [], "overhead": {"n_calls": 0, "latency_s": 0.0,
                                                                      "cost_usd": None}}
    return rep


@pytest.mark.criterion("C3")
@given(rep=_report())
def test_c3_property_mixed_rules_exactly_when_two_or_more_rules(rep):
    # oracle: Requirement 3 by hand: composition_rules = sorted distinct record rules; mixed_rules iff
    #   len(composition_rules) >= 2.
    out = ablation_view(rep)
    assert out["composition_rules"] == sorted({r["composition_rule"] for r in rep["scenarios"]})
    assert out["mixed_rules"] is (len(out["composition_rules"]) >= 2)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(rep=_report())
def test_c3_property_each_scenario_equals_its_waterfall(rep):
    # oracle: Requirement 3 by hand: scenarios[i] == waterfall(report["scenarios"][i], rows) with rows =
    #   layer_cost["rows"] or None, in report order (same function on the same input: exact equality).
    out = ablation_view(rep)
    rows = rep["layer_cost"]["rows"] if rep.get("layer_cost") is not None else None
    assert len(out["scenarios"]) == len(rep["scenarios"])
    for i, rec in enumerate(rep["scenarios"]):
        assert out["scenarios"][i] == waterfall(rec, rows)
