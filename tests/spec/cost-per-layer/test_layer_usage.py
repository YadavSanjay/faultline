"""cost-per-layer C3: ``layer_cost.layer_usage`` totals call records per scenario and layer.

Oracle: Requirement 3 by hand: for every scenario in the records, all six ``LAYERS`` plus "overhead";
per layer ``n_calls``, ``n_errors`` (ok false), ``latency_s`` (sum of latency_ms / 1000),
``n_cost_reported`` and ``cost_usd`` (sum over reported records, ``None`` when none reported). No
records -> ``{}``; ``ValueError`` for a negative latency or a negative reported cost; input not mutated.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.layer_cost import layer_usage

CALLS = [
    ("macrorag", "/query"),
    ("arag", "/api/v1/narrative/search"),
    ("arag", "/api/v1/facts/lookup"),
    ("arag", "/health"),
    ("llm", "narrative"),
]
KEYS = ("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state", "overhead")


@st.composite
def _record(draw):
    service, purpose = draw(st.sampled_from(CALLS))
    reported = draw(st.booleans())
    cost = draw(st.sampled_from([0.0, 0.0625, 0.125, 0.25, 1.5])) if reported else 0.0
    return {"scenario": draw(st.sampled_from(["s1", "s2", "s3"])), "service": service, "purpose": purpose,
            "ok": draw(st.booleans()), "latency_ms": float(draw(st.integers(min_value=0, max_value=5000))),
            "cost_usd": cost, "cost_reported": reported}


RECORDS = st.lists(_record(), max_size=25)


def _merge(a, b):
    """Adds two layer_usage outputs field by field (cost: None + None = None, else sum of the numbers)."""
    out = copy.deepcopy(a)
    for scen, layers in b.items():
        if scen not in out:
            out[scen] = copy.deepcopy(layers)
            continue
        for layer, u in layers.items():
            mine = out[scen][layer]
            for k in ("n_calls", "n_errors", "n_cost_reported", "latency_s"):
                mine[k] = mine[k] + u[k]
            if mine["cost_usd"] is None:
                mine["cost_usd"] = u["cost_usd"]
            elif u["cost_usd"] is not None:
                mine["cost_usd"] = mine["cost_usd"] + u["cost_usd"]
    return out


@pytest.mark.criterion("C3")
def test_c3_point_mixed_records(h):
    # oracle: Requirement 3 by hand (conftest.mixed_usage): taiwan macrorag 2 calls / 1 error /
    #   1200+300 ms = 1.5 s / no cost reported -> None; narrative 0.8 s, $0.25; facts 0.5 s, None;
    #   overhead (llm) 2.0 s, $0.125. ai_bust macrorag 0.4 s; facts 2 calls / 1 error / 1.0 s /
    #   $0.0625 + $0.1875 = $0.25; overhead (/health) 0.05 s, None. Every other layer empty.
    h.assert_close(layer_usage(h.mixed_records()), h.mixed_usage())


@pytest.mark.criterion("C3")
def test_c3_point_unreported_cost_is_ignored_and_layer_without_calls_is_zero(h):
    # oracle: Requirement 3 by hand: one unreported record carrying cost_usd 0.5 -> cost_usd None and
    #   n_cost_reported 0 (the number is not summed); 250 ms -> 0.25 s; all other six keys are the
    #   zero entry (0 calls, 0.0 s, None).
    records = [h.rec("only", "arag", "/api/v1/narrative/search", latency_ms=250.0, cost_usd=0.5,
                     cost_reported=False)]
    expected = h.empty_scenario_usage()
    expected["xbrlrag_narrative"] = h.usage_entry(1, 0, 0.25, 0, None)
    h.assert_close(layer_usage(records), {"only": expected})


@pytest.mark.criterion("C3")
def test_c3_point_reported_zero_cost_is_zero_not_none(h):
    # oracle: Requirement 3 by hand: a reported cost of 0.0 counts (n_cost_reported 1) -> cost_usd 0.0,
    #   not None; 1000 ms -> 1.0 s.
    records = [h.rec("z", "macrorag", "/query", latency_ms=1000.0, cost_usd=0.0, cost_reported=True)]
    expected = h.empty_scenario_usage()
    expected["macrorag"] = h.usage_entry(1, 0, 1.0, 1, 0.0)
    h.assert_close(layer_usage(records), {"z": expected})


@pytest.mark.criterion("C3")
def test_c3_point_no_records_is_empty_dict():
    # oracle: Requirement 3 by hand: no records -> {}.
    # red-exempt: empty the correct answer for no records is the empty dict (other tests pin non-empty input)
    assert layer_usage([]) == {}


@pytest.mark.criterion("C3")
def test_c3_point_negative_latency_raises(h):
    # oracle: Requirement 3 by hand: a negative latency_ms -> ValueError.
    records = [h.rec("a", "macrorag", "/query", latency_ms=100.0),
               h.rec("a", "macrorag", "/query", latency_ms=-1.0)]
    with pytest.raises(ValueError, match=r"\S"):
        layer_usage(records)


@pytest.mark.criterion("C3")
def test_c3_point_negative_reported_cost_raises(h):
    # oracle: Requirement 3 by hand: a negative cost_usd on a record with cost_reported true -> ValueError.
    records = [h.rec("a", "arag", "/api/v1/facts/lookup", cost_usd=-0.25, cost_reported=True)]
    with pytest.raises(ValueError, match=r"\S"):
        layer_usage(records)


@pytest.mark.criterion("C3")
def test_c3_point_input_not_mutated(h):
    # oracle: Requirement 3 by hand: "The input is not mutated" -> the list equals a deep copy taken
    #   before the call; the result still matches the hand dict.
    records = h.mixed_records()
    before = copy.deepcopy(records)
    out = layer_usage(records)
    assert records == before
    h.assert_close(out, h.mixed_usage())


@pytest.mark.criterion("C3")
@given(records=RECORDS)
def test_c3_property_every_scenario_has_all_seven_keys_and_counts_add_up(records):
    # oracle: Requirement 3 by hand: the scenarios are exactly those in the records, each with the six
    #   LAYERS + "overhead"; per scenario the n_calls sum to its number of records and latency_s sums to
    #   its total latency_ms / 1000; errors and reported counts never exceed calls; cost None exactly when
    #   n_cost_reported is 0.
    out = layer_usage(records)
    assert set(out) == {r["scenario"] for r in records}
    for scen, layers in out.items():
        mine = [r for r in records if r["scenario"] == scen]
        assert set(layers) == set(KEYS)
        assert sum(u["n_calls"] for u in layers.values()) == len(mine)
        assert sum(u["n_errors"] for u in layers.values()) == sum(1 for r in mine if not r["ok"])
        assert sum(u["latency_s"] for u in layers.values()) == pytest.approx(
            sum(r["latency_ms"] for r in mine) / 1000.0, rel=1e-9, abs=1e-12)  # float summation order only
        for u in layers.values():
            assert 0 <= u["n_errors"] <= u["n_calls"]
            assert 0 <= u["n_cost_reported"] <= u["n_calls"]
            assert (u["cost_usd"] is None) == (u["n_cost_reported"] == 0)
        for local in ("news", "crowding", "state"):
            assert layers[local]["n_calls"] == 0


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(records=RECORDS, cut=st.integers(min_value=0, max_value=25), rnd=st.randoms(use_true_random=False))
def test_c3_metamorphic_split_merge_and_order_do_not_matter(records, cut, rnd, h):
    # oracle: Requirement 3 by hand: every field is a count or a sum over records, so usage(a + b) is the
    #   field-wise merge of usage(a) and usage(b) (cost: None + None = None, else the sum of the numbers),
    #   and shuffling the records changes nothing.
    a, b = records[:cut], records[cut:]
    whole = layer_usage(records)
    assert set(whole) == {r["scenario"] for r in records}
    h.assert_close(whole, _merge(layer_usage(a), layer_usage(b)))
    shuffled = list(records)
    rnd.shuffle(shuffled)
    h.assert_close(layer_usage(shuffled), whole)
