"""scenario-event-replay C5: ``sleeve_direction_accuracy`` scores direction hits per book sleeve.

Oracle: Requirement 5 by hand. A factor qualifies when it is in predicted, realised, scale (with
scale > 0) and sleeves, and ``|realised / scale| >= min_abs_z``; a hit is a non-zero prediction with
the realised move's sign; per sleeve ``hits``, ``n`` and ``rate = hits / n``; sleeves with no
qualifying factor are absent. The synthetic case is ``C5_ROWS`` in conftest.py, annotated per factor.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.event_replay import sleeve_direction_accuracy

SLEEVES = ["semis", "platforms", "power", "index", "rates", "credit", "vol", "fx"]


def _check(result: dict[str, dict[str, float]], want: dict[str, tuple[int, int, float]]) -> None:
    assert set(result) == set(want)
    for sleeve, (hits, n, rate) in want.items():
        assert result[sleeve]["hits"] == hits
        assert result[sleeve]["n"] == n
        assert result[sleeve]["rate"] == pytest.approx(rate, rel=1e-12)  # one float division


@pytest.mark.criterion("C5")
def test_c5_point_synthetic_case_at_threshold_one(h):
    # oracle: Requirement 5 by hand over C5_ROWS with min_abs_z 1.0:
    #   semis: NVDA hit (z -4), AMD miss (z -3, opposite sign), INTC miss (z -2, zero prediction);
    #     MU (z -0.25) and AVGO (not predicted) excluded -> hits 1, n 3, rate 1/3.
    #   platforms: MSFT hit (z -2); GOOGL (scale 0), ORCL (scale < 0), META (no scale) excluded
    #     -> 1, 1, 1.0.
    #   rates: 10Y z = -0.003/0.003 = -1 exactly (>= 1, inclusive) hit, 2Y z 2 hit -> 2, 2, 1.0.
    #   credit: HY z -2 with a positive prediction, miss -> 0, 1, 0.0.
    #   index (SPX z -0.2 below), fx (no realised) absent; VIX has no sleeve.
    predicted, realised, scale, sleeves = h.c5_inputs()
    result = sleeve_direction_accuracy(predicted, realised, scale, sleeves, min_abs_z=1.0)
    _check(result, {"semis": (1, 3, 1 / 3), "platforms": (1, 1, 1.0), "rates": (2, 2, 1.0),
                    "credit": (0, 1, 0.0)})


@pytest.mark.criterion("C5")
def test_c5_point_synthetic_case_at_threshold_two_and_a_half(h):
    # oracle: Requirement 5 by hand over C5_ROWS with min_abs_z 2.5: only |z| >= 2.5 counts -> NVDA
    #   (z -4, hit) and AMD (z -3, miss), both semis -> semis 1, 2, 0.5; INTC, MSFT, both rates and HY
    #   have |z| <= 2 so platforms, rates and credit are absent.
    predicted, realised, scale, sleeves = h.c5_inputs()
    result = sleeve_direction_accuracy(predicted, realised, scale, sleeves, min_abs_z=2.5)
    _check(result, {"semis": (1, 2, 0.5)})


@pytest.mark.criterion("C5")
def test_c5_point_sign_rule_for_up_and_down_moves():
    # oracle: Requirement 5: up realised + up prediction is a hit, down + down a hit, up + down a miss,
    #   up + 0 a miss; all z = 2 (move 0.02, scale 0.01) -> power: 2 hits of 4, rate 0.5.
    keys = ["a", "b", "c", "d"]
    predicted = {"a": 0.5, "b": -0.5, "c": -0.5, "d": 0.0}
    realised = {"a": 0.02, "b": -0.02, "c": 0.02, "d": 0.02}
    scale = dict.fromkeys(keys, 0.01)
    sleeves = dict.fromkeys(keys, "power")
    _check(sleeve_direction_accuracy(predicted, realised, scale, sleeves, min_abs_z=1.0), {"power": (2, 4, 0.5)})


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("min_abs_z", [0.0, -0.5, -1.0])
def test_c5_point_non_positive_threshold_raises(h, min_abs_z):
    # oracle: Requirement 5: ValueError for min_abs_z <= 0.
    predicted, realised, scale, sleeves = h.c5_inputs()
    with pytest.raises(ValueError, match=r"\S"):
        sleeve_direction_accuracy(predicted, realised, scale, sleeves, min_abs_z=min_abs_z)


_MOVE = st.one_of(st.just(0.0), st.floats(min_value=1e-4, max_value=1.0), st.floats(min_value=-1.0, max_value=-1e-4))
_SCALE = st.one_of(st.just(0.0), st.just(-0.01), st.floats(min_value=1e-3, max_value=0.5))
_ROW = st.tuples(_MOVE | st.none(), _MOVE | st.none(), _SCALE | st.none(), st.sampled_from(SLEEVES) | st.none())
_ROWS = st.dictionaries(st.text(alphabet="abcdefgh:_", min_size=1, max_size=5), _ROW, max_size=12)


def _split(rows: dict) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, str]]:
    predicted = {k: r[0] for k, r in rows.items() if r[0] is not None}
    realised = {k: r[1] for k, r in rows.items() if r[1] is not None}
    scale = {k: r[2] for k, r in rows.items() if r[2] is not None}
    sleeves = {k: r[3] for k, r in rows.items() if r[3] is not None}
    # A fixed qualifying factor, so the result is never empty: realised 0.05 / scale 0.01 = z 5, hit.
    predicted["anchor"], realised["anchor"], scale["anchor"], sleeves["anchor"] = 0.3, 0.05, 0.01, "index"
    return predicted, realised, scale, sleeves


@pytest.mark.criterion("C5")
@given(rows=_ROWS, min_abs_z=st.floats(min_value=0.1, max_value=3.0))
def test_c5_property_counts_bounded_and_sleeves_match_rule(rows, min_abs_z):
    # oracle: C5 properties (0 <= rate <= 1, hits <= n, rate = hits / n) and Requirement 5 by hand:
    #   the sleeves present are exactly those with a factor in all four maps, scale > 0 and
    #   |realised / scale| >= min_abs_z; n per sleeve counts those factors.
    predicted, realised, scale, sleeves = _split(rows)
    result = sleeve_direction_accuracy(predicted, realised, scale, sleeves, min_abs_z=min_abs_z)
    qualifying: dict[str, int] = {}
    for k in predicted:
        if k in realised and k in scale and k in sleeves and scale[k] > 0 and abs(realised[k] / scale[k]) >= min_abs_z:
            qualifying[sleeves[k]] = qualifying.get(sleeves[k], 0) + 1
    assert set(result) == set(qualifying)
    for sleeve, row in result.items():
        assert row["n"] == qualifying[sleeve]
        assert 0 <= row["hits"] <= row["n"]
        assert 0.0 <= row["rate"] <= 1.0
        assert row["rate"] == pytest.approx(row["hits"] / row["n"], rel=1e-12)  # one float division
    assert result["index"]["hits"] >= 1


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(rows=_ROWS, min_abs_z=st.floats(min_value=0.1, max_value=3.0),
       factor=st.floats(min_value=1e-3, max_value=1e3))
def test_c5_property_positive_scaling_of_predicted_is_invariant(rows, min_abs_z, factor):
    # oracle: C5 property: a hit depends only on the prediction's sign and non-zero-ness, both kept by
    #   a positive multiplier, so result(c * predicted) == result(predicted); the anchor keeps it
    #   non-empty.
    predicted, realised, scale, sleeves = _split(rows)
    scaled = {k: v * factor for k, v in predicted.items()}
    base = sleeve_direction_accuracy(predicted, realised, scale, sleeves, min_abs_z=min_abs_z)
    moved = sleeve_direction_accuracy(scaled, realised, scale, sleeves, min_abs_z=min_abs_z)
    assert base
    assert set(moved) == set(base)
    for sleeve, row in base.items():
        assert moved[sleeve]["hits"] == row["hits"]
        assert moved[sleeve]["n"] == row["n"]
        assert moved[sleeve]["rate"] == pytest.approx(row["rate"], rel=1e-12)  # same integer ratio
        assert not math.isnan(row["rate"])
