"""scenario-forward-log C4: a layer's value as paired with-minus-without score deltas.

Oracle: Requirement 4 by hand. Records pair when they share run_id, scenario and severity and their layers
sets differ by exactly the layer. Metric values are dyadic so deltas and means are exact.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.forward_log import layer_value

OUT_KEYS = {"layer", "metric", "n_pairs", "mean_delta", "n_improved", "n_worse"}


def _rec(run: str, scen: str, sev: str, layers: list[str], cosine: float | None, **extra: Any) -> dict[str, Any]:
    return {"run_id": run, "scenario": scen, "severity": sev, "layers": layers, "cosine": cosine, **extra}


RECORDS = [
    # pair 1 (r1, S1, severe): [] 0.25 vs [news] 0.75 -> +0.5
    _rec("r1", "S1", "severe", [], 0.25, rank_corr_equity=0.5),
    _rec("r1", "S1", "severe", ["news"], 0.75, rank_corr_equity=None),
    # pair 2 (r1, S1, severe): [macrorag] 0.5 vs [macrorag, news] 0.25 -> -0.25
    # ([] vs [macrorag, news] differ by two layers; [] vs [macrorag] differ by macrorag, not news)
    _rec("r1", "S1", "severe", ["macrorag"], 0.5, rank_corr_equity=0.25),
    _rec("r1", "S1", "severe", ["macrorag", "news"], 0.25, rank_corr_equity=-0.25),
    # pair 3 (r2, S1, severe), layers listed out of order: [macrorag] 0.125 vs [news, macrorag] 1.0 -> +0.875
    _rec("r2", "S1", "severe", ["macrorag"], 0.125, rank_corr_equity=0.0),
    _rec("r2", "S1", "severe", ["news", "macrorag"], 1.0, rank_corr_equity=1.0),
    # pair 4 (r2, S2, mild): [state] 0.5 vs [news, state] 0.5 -> 0 (neither improved nor worse)
    _rec("r2", "S2", "mild", ["state"], 0.5, rank_corr_equity=0.75),
    _rec("r2", "S2", "mild", ["news", "state"], 0.5, rank_corr_equity=0.5),
    # skipped pair (r1, S2, severe): without-value is None
    _rec("r1", "S2", "severe", [], None, rank_corr_equity=0.0),
    _rec("r1", "S2", "severe", ["news"], 0.875, rank_corr_equity=0.5),
    # unpaired: same run/scenario but another severity; same layers but another run or scenario
    _rec("r1", "S1", "extreme", [], 0.0, rank_corr_equity=0.0),
    _rec("r3", "S1", "severe", ["news"], 0.5, rank_corr_equity=0.25),
    _rec("r3", "S3", "severe", [], -1.0, rank_corr_equity=-1.0),
]


@pytest.mark.criterion("C4")
def test_c4_point_news_on_cosine():
    # oracle: Requirement 4 by hand: pairs 1-4 give deltas +0.5, -0.25, +0.875, 0; the None pair is skipped;
    # mean = 1.125 / 4 = 0.28125; improved 2 (+0.5, +0.875), worse 1 (-0.25).
    out = layer_value(RECORDS, "news", "cosine")
    assert set(out) == OUT_KEYS
    assert out["layer"] == "news"
    assert out["metric"] == "cosine"
    assert out["n_pairs"] == 4
    # rel 1e-12: dyadic arithmetic, exact
    assert out["mean_delta"] == pytest.approx(0.28125, rel=1e-12)
    assert out["n_improved"] == 2
    assert out["n_worse"] == 1


@pytest.mark.criterion("C4")
def test_c4_point_other_metric_and_layer():
    # oracle: Requirement 4 by hand, metric rank_corr_equity, layer news: pair 1 skipped (with = None);
    # pair 2: -0.25 - 0.25 = -0.5; pair 3: 1.0 - 0.0 = +1.0; pair 4: 0.5 - 0.75 = -0.25;
    # the r1/S2 pair: 0.5 - 0.0 = +0.5 -> n 4, mean 0.75 / 4 = 0.1875, improved 2, worse 2.
    # Layer macrorag on cosine: (r1, S1, severe) [] 0.25 vs [macrorag] 0.5 -> +0.25 and
    # [news] 0.75 vs [macrorag, news] 0.25 -> -0.5 -> n 2, mean -0.125, improved 1, worse 1.
    rank = layer_value(RECORDS, "news", "rank_corr_equity")
    macro = layer_value(RECORDS, "macrorag", "cosine")
    assert rank["metric"] == "rank_corr_equity"
    assert rank["n_pairs"] == 4
    # rel 1e-12: dyadic arithmetic, exact
    assert rank["mean_delta"] == pytest.approx(0.1875, rel=1e-12)
    assert rank["n_improved"] == 2
    assert rank["n_worse"] == 2
    assert macro["layer"] == "macrorag"
    assert macro["n_pairs"] == 2
    assert macro["mean_delta"] == pytest.approx(-0.125, rel=1e-12)
    assert macro["n_improved"] == 1
    assert macro["n_worse"] == 1


@pytest.mark.criterion("C4")
def test_c4_no_pairs():
    # oracle: Requirement 4 by hand: layer "options" never differs between records; and a list whose only pair
    # has a None value -> n_pairs 0, mean_delta None, counts 0.
    for records, layer in ((RECORDS, "options"), (RECORDS[8:10], "news"), ([], "news")):
        out = layer_value(records, layer, "cosine")
        assert set(out) == OUT_KEYS
        assert out["layer"] == layer
        assert out["metric"] == "cosine"
        assert out["n_pairs"] == 0
        assert out["mean_delta"] is None
        assert out["n_improved"] == 0
        assert out["n_worse"] == 0


_OTHER_LAYERS = ["macrorag", "state", "crowding"]
_VAL = st.one_of(st.none(), st.integers(min_value=-16, max_value=16).map(lambda i: i / 8))


@st.composite
def _paired_records(draw: st.DrawFn):
    n = draw(st.integers(min_value=0, max_value=6))
    pairs = []
    for i in range(n):
        base = sorted(draw(st.sets(st.sampled_from(_OTHER_LAYERS))))
        key = (f"r{i % 3}", f"S{i}", draw(st.sampled_from(["mild", "adverse", "severe"])))
        pairs.append((key, base, draw(_VAL), draw(_VAL)))
    return pairs


def _build(pairs, swap: bool) -> list[dict[str, Any]]:
    out = []
    for (run, scen, sev), base, without, with_ in pairs:
        w, wo = (without, with_) if swap else (with_, without)
        out.append(_rec(run, scen, sev, list(base), wo))
        out.append(_rec(run, scen, sev, [*base, "news"], w))
    return out


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(pairs=_paired_records())
def test_c4_metamorphic_swap_negates(pairs):
    # oracle: C4 swap property: exchanging the with and without values of every pair negates mean_delta,
    # swaps n_improved and n_worse, and keeps n_pairs.
    a = layer_value(_build(pairs, swap=False), "news", "cosine")
    b = layer_value(_build(pairs, swap=True), "news", "cosine")
    assert b["n_pairs"] == a["n_pairs"]
    assert b["n_improved"] == a["n_worse"]
    assert b["n_worse"] == a["n_improved"]
    if a["mean_delta"] is None:
        assert b["mean_delta"] is None
    else:
        # abs 1e-12: dyadic deltas, exact up to summation order
        assert b["mean_delta"] == pytest.approx(-a["mean_delta"], abs=1e-12)


@pytest.mark.criterion("C4")
@given(pairs=_paired_records())
def test_c4_property_counts_match_hand_rule(pairs):
    # oracle: Requirement 4 by hand on generated pairs (each key distinct, one with/without pair each):
    # deltas = with - without over pairs with both values not None.
    deltas = [w - wo for _, _, wo, w in pairs if w is not None and wo is not None]
    out = layer_value(_build(pairs, swap=False), "news", "cosine")
    assert out["n_pairs"] == len(deltas)
    assert out["n_improved"] == sum(1 for d in deltas if d > 0)
    assert out["n_worse"] == sum(1 for d in deltas if d < 0)
    if deltas:
        # abs 1e-12: dyadic deltas, exact up to summation order
        assert out["mean_delta"] == pytest.approx(sum(deltas) / len(deltas), abs=1e-12)
    else:
        assert out["mean_delta"] is None
