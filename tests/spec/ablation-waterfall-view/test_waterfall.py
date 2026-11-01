"""ablation-waterfall-view C2: ``report_views.waterfall`` groups one tier's ladder into the four steps.

Oracle: Requirement 2 by hand (step table macrorag | xbrlrag = narrative + facts | events = news |
market_intelligence = crowding + state): bars from the ladder at each step's present layers, Shapley summed
over the step's table layers, floor hits of the matching prefix subset, calls / latency / cost from the
matching cost rows. Hand-built records live in conftest; the property records are built with
``ablation.ladder_attribution`` and ``ablation.shapley_attribution`` from a random characteristic
function, whose sums give the exact identities (ladder telescoping, Shapley efficiency).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.ablation import ladder_attribution, shapley_attribution
from src.scenariogen.report_views import waterfall

STEP_LAYERS = {
    "macrorag": ("macrorag",),
    "xbrlrag": ("xbrlrag_narrative", "xbrlrag_facts"),
    "events": ("news",),
    "market_intelligence": ("crowding", "state"),
}


# ------------------------------------------------------------------------------------------ point tests
@pytest.mark.criterion("C2")
def test_c2_point_six_layer_record_with_cost_rows(h):
    # oracle: Requirement 2 by hand (conftest.taiwan_waterfall): ladder -10 | -14 | -13, -15 | -19 |
    #   -18, -18.5 -> macrorag -10 -> -14 (shapley -4.0, floor [TSM]); xbrlrag -14 -> -15 (1.5 - 2.5 =
    #   -1.0, floor [AMD, TSM]); events -15 -> -19 (-4.0, no subset -> []); market_intelligence -19 ->
    #   -18.5 (1.75 - 1.25 = 0.5). Costs: macrorag 3 calls / 1.5 s / None -> 4.0 / 1.5, None; xbrlrag
    #   2 + 1 calls, 0.5 + 0.25 s, 0.25 + None = 0.25 -> 1.0 / 0.75, 4.0; events 0 / 0.0 / None -> None,
    #   None; market_intelligence (state row) 1 / 0.5 / 0.125 -> 1.0, 4.0. ai_bust and extreme rows ignored.
    h.assert_close(waterfall(h.taiwan_record(), h.taiwan_cost_rows()), h.taiwan_waterfall())


@pytest.mark.criterion("C2")
def test_c2_point_six_layer_record_without_cost_rows(h):
    # oracle: Requirement 2 by hand: cost_rows=None -> n_calls, latency_s, cost_usd and both ratios None
    #   on every step; bars, Shapley and floor hits as in the hand table.
    h.assert_close(waterfall(h.taiwan_record(), None), h.taiwan_waterfall(with_cost=False))
    h.assert_close(waterfall(h.taiwan_record()), h.taiwan_waterfall(with_cost=False))


@pytest.mark.criterion("C2")
def test_c2_point_without_subsets_floor_hits_are_empty(h):
    # oracle: Requirement 2 by hand: no "subsets" key -> every step's floor_hits and authored_floor_hits
    #   are []; everything else unchanged.
    h.assert_close(waterfall(h.taiwan_record(with_subsets=False), h.taiwan_cost_rows()),
                   h.taiwan_waterfall(with_subsets=False))


@pytest.mark.criterion("C2")
def test_c2_point_order_without_state_and_facts_gives_partial_steps(h):
    # oracle: Requirement 2 by hand: order [macrorag, narrative, news, crowding], ladder -10 | -14 | -13 |
    #   -17 | -16. xbrlrag keeps only [narrative]: -14 -> -13, delta 1; market_intelligence only
    #   [crowding]: -17 -> -16. Shapley sums every table layer present or not: xbrlrag 1.0 + facts 0.25 =
    #   1.25; market_intelligence crowding 1.0 + state 0.5 = 1.5. No subsets / cost rows -> [] / None.
    order = ["macrorag", "xbrlrag_narrative", "news", "crowding"]
    rec = h.record("s1", "mild", "dualrag-v1", order, [-10.0, -14.0, -13.0, -17.0, -16.0],
                   {"macrorag": -4.0, "xbrlrag_narrative": 1.0, "xbrlrag_facts": 0.25, "news": -4.0,
                    "crowding": 1.0, "state": 0.5})
    expected = {"scenario": "s1", "severity": "mild", "composition_rule": "dualrag-v1",
                "authored_pnl": -10.0, "final_pnl": -16.0, "authored_floor_hits": [],
                "steps": [h.step("macrorag", ["macrorag"], -10.0, -14.0, -4.0, []),
                          h.step("xbrlrag", ["xbrlrag_narrative"], -14.0, -13.0, 1.0 + 0.25, []),
                          h.step("events", ["news"], -13.0, -17.0, -4.0, []),
                          h.step("market_intelligence", ["crowding"], -17.0, -16.0, 1.0 + 0.5, [])]}
    h.assert_close(waterfall(rec), expected)


@pytest.mark.criterion("C2")
def test_c2_point_empty_steps_follow_in_table_order_and_still_get_costs(h):
    # oracle: Requirement 2 by hand: order [macrorag, narrative, facts], ladder -10 | -12 | -11, -13.
    #   events and market_intelligence have no present layer -> both bars = final -13, delta 0, shapley
    #   0.0 (no keys), floor [] although the subset {macrorag, narrative, facts} has ["Z"] (that is
    #   xbrlrag's prefix). Costs still count the step's table layers: the news row (2 calls, 1.0 s, 0.5)
    #   -> events 2 / 1.0 / 0.5, ratios |0.0| / 1.0 = 0.0 and |0.0| / 0.5 = 0.0. macrorag row 1 call,
    #   0.5 s, cost 0.0 -> per_s 2.0 / 0.5 = 4.0, per_usd None (0.0 is not > 0).
    order = ["macrorag", "xbrlrag_narrative", "xbrlrag_facts"]
    rec = h.record("s2", "severe", "dualrag-v3", order, [-10.0, -12.0, -11.0, -13.0],
                   {"macrorag": -2.0, "xbrlrag_narrative": 0.5, "xbrlrag_facts": -1.5},
                   subsets=[{"layers": [], "floor_hits": []},
                            {"layers": ["xbrlrag_narrative", "macrorag", "xbrlrag_facts"], "floor_hits": ["Z"]}])
    rows = [h.cost_row("s2", "severe", "news", 2, 1.0, 0.5), h.cost_row("s2", "severe", "macrorag", 1, 0.5, 0.0)]
    expected = {"scenario": "s2", "severity": "severe", "composition_rule": "dualrag-v3",
                "authored_pnl": -10.0, "final_pnl": -13.0, "authored_floor_hits": [],
                "steps": [h.step("macrorag", ["macrorag"], -10.0, -12.0, -2.0, [], 1, 0.5, 0.0, 2.0 / 0.5, None),
                          h.step("xbrlrag", ["xbrlrag_narrative", "xbrlrag_facts"], -12.0, -13.0, 0.5 - 1.5,
                                 ["Z"], 0, 0.0, None, None, None),
                          h.step("events", [], -13.0, -13.0, 0.0, [], 2, 1.0, 0.5, 0.0 / 1.0, 0.0 / 0.5),
                          h.step("market_intelligence", [], -13.0, -13.0, 0.0, [], 0, 0.0, None, None, None)]}
    h.assert_close(waterfall(rec, rows), expected)


@pytest.mark.criterion("C2")
def test_c2_point_reordered_news_first(h):
    # oracle: Requirement 2 by hand: order [news, macrorag, narrative, facts, crowding, state], ladder
    #   -10 | news -13 | macrorag -17 | -16, -18 | -17, -17.5 -> steps ordered by first present layer:
    #   events -10 -> -13 (shapley -4.0), macrorag -13 -> -17 (-4.0), xbrlrag -17 -> -18 (-1.0),
    #   market_intelligence -18 -> -17.5 (0.5). Floor hits use the order's prefix: macrorag's prefix is
    #   {news, macrorag} -> ["C", "B"] sorted [B, C] (the {macrorag} entry ["B"] does not match); events'
    #   prefix {news} -> [A].
    order = ["news", "macrorag", "xbrlrag_narrative", "xbrlrag_facts", "crowding", "state"]
    rec = h.record("s3", "extreme", "dualrag-v3", order, [-10.0, -13.0, -17.0, -16.0, -18.0, -17.0, -17.5],
                   h.taiwan_shapley,
                   subsets=[{"layers": ["news"], "floor_hits": ["A"]}, {"layers": ["macrorag"], "floor_hits": ["B"]},
                            {"layers": ["macrorag", "news"], "floor_hits": ["C", "B"]}])
    expected = {"scenario": "s3", "severity": "extreme", "composition_rule": "dualrag-v3",
                "authored_pnl": -10.0, "final_pnl": -17.5, "authored_floor_hits": [],
                "steps": [h.step("events", ["news"], -10.0, -13.0, -4.0, ["A"]),
                          h.step("macrorag", ["macrorag"], -13.0, -17.0, -4.0, ["B", "C"]),
                          h.step("xbrlrag", ["xbrlrag_narrative", "xbrlrag_facts"], -17.0, -18.0, 1.5 - 2.5, []),
                          h.step("market_intelligence", ["crowding", "state"], -18.0, -17.5, 1.75 - 1.25, [])]}
    h.assert_close(waterfall(rec), expected)


def _invalid_records(h):
    good_order = ["macrorag", "xbrlrag_narrative", "xbrlrag_facts"]
    totals = [-10.0, -12.0, -11.0, -13.0]
    shap = {"macrorag": -2.0}

    empty = h.record("x", "severe", "dualrag-v1", good_order, totals, shap)
    empty["ladder"] = []
    not_authored = h.record("x", "severe", "dualrag-v1", good_order, totals, shap)
    not_authored["ladder"][0]["layer"] = "base"
    off_order = h.record("x", "severe", "dualrag-v1", good_order, totals, shap)
    off_order["ladder"][2]["layer"], off_order["ladder"][3]["layer"] = "xbrlrag_facts", "xbrlrag_narrative"
    unknown = h.record("x", "severe", "dualrag-v1", ["macrorag", "bogus"], [-10.0, -12.0, -11.0], shap)
    repeated = h.record("x", "severe", "dualrag-v1", ["macrorag", "news", "macrorag"],
                        [-10.0, -12.0, -11.0, -13.0], shap)
    split = h.record("x", "severe", "dualrag-v1", ["xbrlrag_narrative", "macrorag", "xbrlrag_facts"], totals, shap)
    return {"empty_ladder": empty, "ladder_not_authored": not_authored, "ladder_off_order": off_order,
            "unknown_layer": unknown, "repeated_layer": repeated, "step_not_consecutive": split}


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("case", ["empty_ladder", "ladder_not_authored", "ladder_off_order", "unknown_layer",
                                  "repeated_layer", "step_not_consecutive"])
def test_c2_point_invalid_record_raises(h, case):
    # oracle: Requirement 2 by hand, one ValueError rule each: ladder [] ; ladder[0]["layer"] "base" !=
    #   "authored"; ladder[1:] layers [macrorag, facts, narrative] != order [macrorag, narrative, facts];
    #   "bogus" not in LAYERS; "macrorag" twice; order [narrative, macrorag, facts] puts macrorag (another
    #   step) between the two xbrlrag layers.
    rec = _invalid_records(h)[case]
    with pytest.raises(ValueError, match=r"\S"):
        waterfall(rec, h.taiwan_cost_rows())


@pytest.mark.criterion("C2")
def test_c2_point_inputs_not_mutated(h):
    # oracle: Requirement 2 by hand: "The input is not mutated" -> record and rows equal deep copies taken
    #   before the call; the result is still the hand table.
    rec, rows = h.taiwan_record(), h.taiwan_cost_rows()
    rec_before, rows_before = copy.deepcopy(rec), copy.deepcopy(rows)
    out = waterfall(rec, rows)
    assert rec == rec_before
    assert rows == rows_before
    h.assert_close(out, h.taiwan_waterfall())


# -------------------------------------------------------------------------------------- property tests
@st.composite
def _order(draw):
    """A valid order: steps in random sequence, each with a random (possibly empty) subset of its layers in
    random internal order, so every step's present layers are consecutive."""
    steps = draw(st.permutations(list(STEP_LAYERS)))
    order: list[str] = []
    for s in steps:
        present = draw(st.lists(st.sampled_from(STEP_LAYERS[s]), unique=True))
        order.extend(present)
    return order


VALUE = st.integers(min_value=-4000, max_value=4000).map(lambda i: i / 8.0)


@st.composite
def _record(draw, order=None):
    order = draw(_order()) if order is None else order
    values = {}
    for mask in range(2 ** len(order)):
        values[frozenset(x for i, x in enumerate(order) if mask >> i & 1)] = draw(VALUE)
    return order, values


def _build(order, values, scenario="s", severity="severe"):
    lad = ladder_attribution(lambda s: {"t1": values[s]}, order)
    shap = shapley_attribution(lambda s: values[s], order)
    return {"scenario": scenario, "severity": severity, "composition_rule": "dualrag-v1", "order": list(order),
            "ladder": lad, "shapley": shap}


@pytest.mark.criterion("C2")
@given(rv=_record())
def test_c2_property_deltas_and_shapley_sum_to_final_minus_authored(rv):
    # oracle: exact identities: the step bars telescope over the ladder (empty steps add 0) and Shapley
    #   efficiency gives sum(phi) = v(order) - v({}); both equal final_pnl - authored_pnl. Tolerance
    #   rel=1e-9 (spec), abs=1e-9 for sums that cancel to ~0 (Shapley weights are not dyadic).
    order, values = rv
    out = waterfall(_build(order, values))
    target = values[frozenset(order)] - values[frozenset()]
    assert out["final_pnl"] - out["authored_pnl"] == pytest.approx(target, rel=1e-9, abs=1e-9)
    assert [s["step"] for s in out["steps"]] and sorted(s["step"] for s in out["steps"]) == sorted(STEP_LAYERS)
    assert sum(s["delta"] for s in out["steps"]) == pytest.approx(target, rel=1e-9, abs=1e-9)
    assert sum(s["shapley"] for s in out["steps"]) == pytest.approx(target, rel=1e-9, abs=1e-9)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(rv=_record(), data=st.data())
def test_c2_property_swapping_layers_within_a_step_keeps_the_bars(rv, data):
    # oracle: Requirement 2 by hand: a step's bars are v(prefix through its last layer) and v(prefix before
    #   its first layer), which are the same sets after swapping two layers of one step; so pnl_before,
    #   pnl_after and delta of every step are unchanged (same v values; tolerance abs=1e-12 is rounding
    #   of one subtraction only).
    order, values = rv
    pairs = [(i, i + 1) for i in range(len(order) - 1)
             if any(order[i] in ls and order[i + 1] in ls for ls in STEP_LAYERS.values())]
    swapped = list(order)
    if pairs:
        i, j = data.draw(st.sampled_from(pairs))
        swapped[i], swapped[j] = swapped[j], swapped[i]
    base = {s["step"]: s for s in waterfall(_build(order, values))["steps"]}
    other = {s["step"]: s for s in waterfall(_build(swapped, values))["steps"]}
    assert set(base) == set(other) == set(STEP_LAYERS)
    for sid in STEP_LAYERS:
        for k in ("pnl_before", "pnl_after", "delta"):
            assert other[sid][k] == pytest.approx(base[sid][k], rel=0, abs=1e-12)


ROW = st.tuples(
    st.sampled_from(["s", "other"]), st.sampled_from(["severe", "mild"]),
    st.sampled_from(["macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state"]),
    st.integers(min_value=0, max_value=3), st.sampled_from([0.0, 0.25, 1.5]),
    st.sampled_from([None, 0.0, 0.125, 2.0]))


@pytest.mark.criterion("C2")
@given(rv=_record(), rows=st.lists(ROW, max_size=10))
def test_c2_property_ratio_none_exactly_when_denominator_missing_or_zero(h, rv, rows):
    # oracle: Requirement 2 by hand: contribution_per_s is None iff latency_s is None or 0, else
    #   |shapley| / latency_s; contribution_per_usd is None iff cost_usd is None or 0, else
    #   |shapley| / cost_usd (one division: rel=1e-12). n_calls and latency_s are the matching rows' sums.
    order, values = rv
    cost_rows = [h.cost_row(*r) for r in rows]
    out = waterfall(_build(order, values), cost_rows)
    assert len(out["steps"]) == 4
    for s in out["steps"]:
        mine = [r for r in rows if r[0] == "s" and r[1] == "severe" and r[2] in STEP_LAYERS[s["step"]]]
        assert s["n_calls"] == sum(r[3] for r in mine)
        assert s["latency_s"] == pytest.approx(sum(r[4] for r in mine), rel=1e-12, abs=0)  # dyadic sums
        for ratio, denom in (("contribution_per_s", s["latency_s"]), ("contribution_per_usd", s["cost_usd"])):
            if denom is None or denom == 0:
                assert s[ratio] is None
            else:
                assert s[ratio] == pytest.approx(abs(s["shapley"]) / denom, rel=1e-12)
