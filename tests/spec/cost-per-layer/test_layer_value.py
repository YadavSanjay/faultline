"""cost-per-layer C4: ``layer_cost.layer_value`` joins per-layer usage to Shapley attributions.

Oracle: Requirement 4 by hand: ``rows`` per attribution (input order) x layer (``LAYERS`` order) with
contribution = |shapley| (missing key = 0.0) and usage from ``usage[scenario][layer]`` (zero / 0.0 /
None when the scenario is absent); ``totals`` per severity (first appearance) x layer, contributions
summed over rows, calls / latency / cost summed once per distinct scenario of the severity (cost None
when None for all); ratios = contribution / denominator when the denominator is a positive number, else
None; ``overhead`` summed over every scenario in ``usage``. Duplicate (scenario, severity) ->
ValueError; inputs not mutated. ``usage`` inputs are hand-written (conftest.mixed_usage), never produced
by ``layer_usage``.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.layer_cost import layer_value

LAYER_IDS = ("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state")
KEYS = (*LAYER_IDS, "overhead")
SCENARIOS = ["s1", "s2", "s3"]

ENTRY = st.fixed_dictionaries({
    "n_calls": st.integers(min_value=0, max_value=4),
    "n_errors": st.just(0),
    "latency_s": st.sampled_from([0.0, 0.25, 1.0, 2.5]),
    "n_cost_reported": st.integers(min_value=0, max_value=2),
    "cost_usd": st.sampled_from([None, 0.0, 0.5, 2.0]),
})
USAGE = st.dictionaries(st.sampled_from(SCENARIOS), st.fixed_dictionaries(dict.fromkeys(KEYS, ENTRY)), max_size=3)
SHAPLEY = st.dictionaries(st.sampled_from(LAYER_IDS), st.sampled_from([-3.0, -0.5, 0.0, 1.5, 2.0]))


@st.composite
def _attributions(draw, severities=("mild", "severe", "extreme")):
    """Distinct (scenario, severity) pairs; "s4" is never in USAGE."""
    pairs = draw(st.lists(st.tuples(st.sampled_from([*SCENARIOS, "s4"]), st.sampled_from(list(severities))),
                          unique=True, max_size=8))
    return [{"scenario": s, "severity": sev, "shapley": draw(SHAPLEY)} for s, sev in pairs]


def _check_ratio(ratio, contribution, denom):
    if denom is None or denom == 0:
        assert ratio is None
    else:
        assert ratio == pytest.approx(contribution / denom, rel=1e-9)  # one division; rounding only


@pytest.mark.criterion("C4")
def test_c4_point_rows_totals_and_overhead(h):
    # oracle: Requirement 4 by hand (conftest.mixed_value): e.g. taiwan/severe narrative |2.0| / 0.8 s =
    #   2.5, / $0.25 = 8.0; ai_bust/severe facts |-4.0| / 1.0 s = 4.0, / $0.25 = 16.0; missing shapley
    #   keys -> 0.0; gulf (not in usage) -> 0 calls, 0.0 s, None. Totals: severe macrorag 3.0 + 1.5 = 4.5
    #   over 1.5 + 0.4 = 1.9 s; severe facts cost None (taiwan) + 0.25 (ai_bust) = 0.25 -> 4.5 / 0.25 =
    #   18.0. Overhead: 1 + 1 calls, 2.0 + 0.05 s, $0.125 (+ None).
    h.assert_close(layer_value(h.mixed_usage(), h.attributions()), h.mixed_value())


@pytest.mark.criterion("C4")
def test_c4_point_zero_reported_cost_gives_no_per_usd_and_orphan_scenario_counts_in_overhead(h):
    # oracle: Requirement 4 by hand: cost 0.0 is not > 0 -> contribution_per_usd None, while 1.5 / 0.5 s =
    #   3.0 per s. "orphan" has no attribution: no row or total, but its overhead (3 calls, 1.25 s, $0.5)
    #   joins s's (1 call, 0.25 s, cost None) -> 4 calls, 1.5 s, $0.5.
    s_usage = h.empty_scenario_usage()
    s_usage["macrorag"] = h.usage_entry(2, 0, 0.5, 2, 0.0)
    s_usage["overhead"] = h.usage_entry(1, 0, 0.25, 0, None)
    orphan = h.empty_scenario_usage()
    orphan["overhead"] = h.usage_entry(3, 1, 1.25, 1, 0.5)
    usage = {"s": s_usage, "orphan": orphan}
    out = layer_value(usage, [{"scenario": "s", "severity": "mild", "shapley": {"macrorag": -1.5}}])
    zero = dict.fromkeys(LAYER_IDS[1:], (0.0, 0, 0.0, None, None, None))
    expected_rows = [h.row("s", "mild", "macrorag", -1.5, 2, 0.5, 0.0, 1.5 / 0.5, None)]
    expected_rows += [h.row("s", "mild", layer, *v) for layer, v in zero.items()]
    expected_totals = [h.total("mild", "macrorag", 1.5, 2, 0.5, 0.0, 1.5 / 0.5, None)]
    expected_totals += [h.total("mild", layer, *v) for layer, v in zero.items()]
    h.assert_close(out, {"rows": expected_rows, "totals": expected_totals,
                         "overhead": {"n_calls": 1 + 3, "latency_s": 0.25 + 1.25, "cost_usd": 0.5}})


@pytest.mark.criterion("C4")
def test_c4_point_no_attributions_and_no_usage(h):
    # oracle: Requirement 4 by hand: no attributions -> no rows or totals; overhead over an empty usage
    #   -> 0 calls, 0.0 s, cost None (None for all of zero scenarios).
    assert layer_value({}, []) == {"rows": [], "totals": [],
                                   "overhead": {"n_calls": 0, "latency_s": 0.0, "cost_usd": None}}


@pytest.mark.criterion("C4")
def test_c4_point_duplicate_scenario_severity_raises(h):
    # oracle: Requirement 4 by hand: two attributions with the same (scenario, severity) -> ValueError,
    #   even when their shapley values differ.
    attributions = [*h.attributions(),
                    {"scenario": "taiwan", "severity": "severe", "shapley": {"macrorag": 1.0}}]
    with pytest.raises(ValueError, match=r"\S"):
        layer_value(h.mixed_usage(), attributions)


@pytest.mark.criterion("C4")
def test_c4_point_inputs_not_mutated(h):
    # oracle: Requirement 4 by hand: "The inputs are not mutated" -> both equal deep copies taken before
    #   the call (in particular the missing shapley keys are not filled in); the result is still the hand
    #   table.
    usage, attributions = h.mixed_usage(), h.attributions()
    usage_before, attributions_before = copy.deepcopy(usage), copy.deepcopy(attributions)
    out = layer_value(usage, attributions)
    assert usage == usage_before
    assert attributions == attributions_before
    h.assert_close(out, h.mixed_value())


@pytest.mark.criterion("C4")
@given(usage=USAGE, attributions=_attributions())
def test_c4_property_ratios_none_exactly_at_missing_or_zero_denominator(usage, attributions):
    # oracle: Requirement 4 by hand: contribution_per_s is None iff latency_s is None or 0 (else
    #   contribution / latency_s); contribution_per_usd is None iff cost_usd is None or 0 (else
    #   contribution / cost_usd) -- in rows and totals alike; rows = attributions x 6 layers in order.
    out = layer_value(usage, attributions)
    assert [(r["scenario"], r["severity"], r["layer"]) for r in out["rows"]] == [
        (a["scenario"], a["severity"], layer) for a in attributions for layer in LAYER_IDS]
    for item in [*out["rows"], *out["totals"]]:
        _check_ratio(item["contribution_per_s"], item["contribution"], item["latency_s"])
        _check_ratio(item["contribution_per_usd"], item["contribution"], item["cost_usd"])


@pytest.mark.criterion("C4")
@given(usage=USAGE, attributions=_attributions())
def test_c4_property_total_contribution_is_sum_of_row_contributions(usage, attributions):
    # oracle: Requirement 4 by hand: totals are (severity in first-appearance order) x LAYERS, and each
    #   total's contribution is the sum of |shapley| over that severity's rows for the layer.
    out = layer_value(usage, attributions)
    severities = list(dict.fromkeys(a["severity"] for a in attributions))
    assert [(t["severity"], t["layer"]) for t in out["totals"]] == [
        (sev, layer) for sev in severities for layer in LAYER_IDS]
    for t in out["totals"]:
        rows = [r for r in out["rows"] if r["severity"] == t["severity"] and r["layer"] == t["layer"]]
        # tolerance: summation order only
        assert t["contribution"] == pytest.approx(sum(r["contribution"] for r in rows), rel=1e-9, abs=1e-12)
        assert t["contribution"] == pytest.approx(
            sum(abs(a["shapley"].get(t["layer"], 0.0)) for a in attributions if a["severity"] == t["severity"]),
            rel=1e-9, abs=1e-12)  # same sum from the inputs; summation order only


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(usage=USAGE, first=_attributions(severities=("severe",)), second=_attributions(severities=("extreme",)))
def test_c4_metamorphic_second_severity_never_changes_first_severity_totals(usage, first, second, h):
    # oracle: Requirement 4 by hand: a scenario's calls are shared by its tiers and each severity counts
    #   them once over its own distinct scenarios, so appending "extreme" attributions (of the same or
    #   other scenarios) leaves every "severe" total's n_calls, latency_s and cost_usd (and contribution)
    #   unchanged.
    def severe(out):
        return [{k: t[k] for k in ("layer", "contribution", "n_calls", "latency_s", "cost_usd")}
                for t in out["totals"] if t["severity"] == "severe"]

    alone = layer_value(usage, first)
    both = layer_value(usage, first + second)
    assert len(severe(alone)) == (len(LAYER_IDS) if first else 0)
    h.assert_close(severe(both), severe(alone))
