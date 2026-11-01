"""scenario-forward-log C2: shape scoring of a predicted factor vector against the realised move.

Oracle: Requirement 2 by hand. Inputs use dyadic moves and scales so every z = move / scale is exact.
Spearman is the Pearson correlation of average ranks, ranked by hand in the test (no scipy). The two
invariances (common positive rescaling of the prediction; rescaling one factor's predicted move,
realised move and scale together) are properties; the constants are powers of two so z is unchanged
bit for bit.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.forward_log import score_prediction

KEYS = ("n_factors", "cosine", "rank_corr_equity", "direction_hit_rate", "n_direction")

# Scored factors: z_pred / z_real chosen first, move = z * scale (all dyadic, exact).
#   factor  scale    z_pred  z_real
#   eq:A    0.25     -2      -1.5
#   eq:B    0.5      -2      -0.5
#   eq:C    0.125     0       2
#   eq:D    1.0      -1      -1.25
#   ir:X    0.0625    1      -2
PREDICTED = {
    "eq:A": -0.5,
    "eq:B": -1.0,
    "eq:C": 0.0,
    "eq:D": -1.0,
    "ir:X": 0.0625,
    "eq:E": 3.0,  # scale 0 -> not scored
    "eq:F": -3.0,  # scale < 0 -> not scored
    "fx:Y": 1.0,  # no realised -> not scored
    "eq:G": 2.0,  # no scale -> not scored
}
REALISED = {
    "eq:A": -0.375,
    "eq:B": -0.25,
    "eq:C": 0.25,
    "eq:D": -1.25,
    "ir:X": -0.125,
    "eq:E": -3.0,
    "eq:F": 3.0,
    "eq:G": -2.0,
    "eq:H": 5.0,  # no prediction -> not scored
}
SCALE = {
    "eq:A": 0.25,
    "eq:B": 0.5,
    "eq:C": 0.125,
    "eq:D": 1.0,
    "ir:X": 0.0625,
    "eq:E": 0.0,
    "eq:F": -0.5,
    "fx:Y": 0.5,
    "eq:H": 0.5,
}


def _pearson(x: list[float], y: list[float]) -> float:
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy)


@pytest.mark.criterion("C2")
def test_c2_point_hand_calculation():
    # oracle: Requirement 2 by hand on the table above (5 scored factors).
    # cosine: dot = 3 + 1 + 0 + 1.25 - 2 = 3.25; |p|^2 = 10; |r|^2 = 2.25 + 0.25 + 4 + 1.5625 + 4 = 12.0625.
    # Spearman over eq:A..D (z): ranks pred (-2, -2, 0, -1) -> (1.5, 1.5, 4, 3) [tie averaged];
    # ranks real (-1.5, -0.5, 2, -1.25) -> (1, 3, 4, 2); Pearson of ranks = 3 / sqrt(4.5 * 5).
    # Direction (|z_real| >= 1): A (hit), C (pred 0 -> miss), D (hit), X (pred +, real - -> miss) -> 2 / 4.
    out = score_prediction(PREDICTED, REALISED, SCALE)
    assert out["n_factors"] == 5
    # rel 1e-12: spec tolerance; a handful of exact dyadic products and one sqrt
    assert out["cosine"] == pytest.approx(3.25 / math.sqrt(10.0 * 12.0625), rel=1e-12)
    # abs 1e-12: spec tolerance for Spearman
    assert out["rank_corr_equity"] == pytest.approx(3.0 / math.sqrt(22.5), abs=1e-12)
    assert out["rank_corr_equity"] == pytest.approx(_pearson([1.5, 1.5, 4, 3], [1, 3, 4, 2]), abs=1e-12)
    assert out["direction_hit_rate"] == pytest.approx(0.5, rel=1e-12)  # rel 1e-12: spec tolerance
    assert out["n_direction"] == 4


@pytest.mark.criterion("C2")
def test_c2_point_min_abs_z_threshold():
    # oracle: Requirement 2 by hand: min_abs_z = 1.5 keeps A (|z| = 1.5, boundary is >=), C (2), X (2):
    # A hit, C miss (pred 0), X miss -> 1/3; min_abs_z = 0.5 adds B (0.5, hit) and D (1.25, hit) -> all five
    # qualify, A, B, D hits and C, X misses -> 3/5.
    hi = score_prediction(PREDICTED, REALISED, SCALE, min_abs_z=1.5)
    lo = score_prediction(PREDICTED, REALISED, SCALE, min_abs_z=0.5)
    assert hi["n_direction"] == 3
    # rel 1e-12: spec tolerance
    assert hi["direction_hit_rate"] == pytest.approx(1 / 3, rel=1e-12)
    assert lo["n_direction"] == 5
    assert lo["direction_hit_rate"] == pytest.approx(3 / 5, rel=1e-12)
    # cosine and rank don't depend on min_abs_z
    assert hi["cosine"] == pytest.approx(3.25 / math.sqrt(10.0 * 12.0625), rel=1e-12)
    assert lo["rank_corr_equity"] == pytest.approx(3.0 / math.sqrt(22.5), abs=1e-12)


@pytest.mark.criterion("C2")
def test_c2_point_rank_uses_only_equity_factors_and_negative_direction():
    # oracle: Requirement 2 by hand: three eq factors with z_pred (1, 2, 3) and z_real (-0.5, -1, -4) (moves
    # with scales 0.5, 0.25, 1) -> perfectly reversed ranks -> -1; the fx/ir factors (ranked against them they
    # would break the order) are left out of the rank. Cosine over 5 z's: pred (1, 2, 3, 4, -4),
    # real (-0.5, -1, -4, 4, -4): dot = -0.5 - 2 - 12 + 16 + 16 = 17.5; |p|^2 = 46; |r|^2 = 49.25.
    # Direction (|z_real| >= 1): eq:Q miss, eq:R miss, fx:S hit, ir:T hit -> 2/4.
    pred = {"eq:P": 0.5, "eq:Q": 0.5, "eq:R": 3.0, "fx:S": 2.0, "ir:T": -0.5}
    real = {"eq:P": -0.25, "eq:Q": -0.25, "eq:R": -4.0, "fx:S": 2.0, "ir:T": -0.5}
    scale = {"eq:P": 0.5, "eq:Q": 0.25, "eq:R": 1.0, "fx:S": 0.5, "ir:T": 0.125}
    out = score_prediction(pred, real, scale)
    assert out["n_factors"] == 5
    # abs 1e-12: spec tolerance for Spearman
    assert out["rank_corr_equity"] == pytest.approx(-1.0, abs=1e-12)
    # rel 1e-12: spec tolerance
    assert out["cosine"] == pytest.approx(17.5 / math.sqrt(46.0 * 49.25), rel=1e-12)
    assert out["direction_hit_rate"] == pytest.approx(0.5, rel=1e-12)
    assert out["n_direction"] == 4


@pytest.mark.criterion("C2")
def test_c2_none_cases():
    # oracle: Requirement 2 None rules by hand:
    # (a) one factor -> cosine None, rank None (< 3 eq); |z_real| = 2 qualifies, pred sign right -> 1.0.
    one = score_prediction({"eq:A": 1.0}, {"eq:A": 2.0}, {"eq:A": 1.0})
    assert one["n_factors"] == 1
    assert one["cosine"] is None
    assert one["rank_corr_equity"] is None
    assert one["direction_hit_rate"] == pytest.approx(1.0, rel=1e-12)  # rel 1e-12: spec tolerance
    assert one["n_direction"] == 1
    # (b) zero predicted vector -> cosine None; constant predicted eq vector -> rank None; every pred 0 -> misses.
    zero = score_prediction(
        {"eq:A": 0.0, "eq:B": 0.0, "eq:C": 0.0},
        {"eq:A": 1.0, "eq:B": -2.0, "eq:C": 3.0},
        {"eq:A": 1.0, "eq:B": 1.0, "eq:C": 1.0},
    )
    assert zero["n_factors"] == 3
    assert zero["cosine"] is None
    assert zero["rank_corr_equity"] is None
    assert zero["direction_hit_rate"] == pytest.approx(0.0, abs=0)  # exact: 0 hits of 3
    assert zero["n_direction"] == 3
    # (c) zero realised vector -> cosine None; constant realised eq vector -> rank None; no |z_real| >= 1
    #     -> hit rate None, n_direction 0.
    flat = score_prediction(
        {"eq:A": 1.0, "eq:B": -2.0, "eq:C": 3.0},
        {"eq:A": 0.0, "eq:B": 0.0, "eq:C": 0.0},
        {"eq:A": 1.0, "eq:B": 1.0, "eq:C": 1.0},
    )
    assert flat["n_factors"] == 3
    assert flat["cosine"] is None
    assert flat["rank_corr_equity"] is None
    assert flat["direction_hit_rate"] is None
    assert flat["n_direction"] == 0
    # (d) two eq + two non-eq factors -> rank None (< 3 eq) while cosine is defined:
    #     z_pred (1, 1, 1, 1), z_real (1, 1, 1, 1) -> cosine 1; direction 4/4.
    two = score_prediction(
        {"eq:A": 1.0, "eq:B": 1.0, "ir:C": 1.0, "fx:D": 1.0},
        {"eq:A": 1.0, "eq:B": 1.0, "ir:C": 1.0, "fx:D": 1.0},
        {"eq:A": 1.0, "eq:B": 1.0, "ir:C": 1.0, "fx:D": 1.0},
    )
    assert two["n_factors"] == 4
    assert two["rank_corr_equity"] is None
    assert two["cosine"] == pytest.approx(1.0, rel=1e-12)  # rel 1e-12: spec tolerance
    assert two["direction_hit_rate"] == pytest.approx(1.0, rel=1e-12)
    assert two["n_direction"] == 4
    # (e) nothing in common -> 0 factors, every metric None.
    none = score_prediction({"eq:A": 1.0}, {"eq:B": 1.0}, {"eq:A": 1.0, "eq:B": 1.0})
    assert none["n_factors"] == 0
    assert none["cosine"] is None
    assert none["rank_corr_equity"] is None
    assert none["direction_hit_rate"] is None
    assert none["n_direction"] == 0


_NAMES = ["eq:A", "eq:B", "eq:C", "eq:D", "eq:E", "fx:F", "ir:G", "cr:H"]
_MOVE = st.integers(min_value=-16, max_value=16).map(lambda i: i / 4)
_SCALE = st.sampled_from([0.0, 0.125, 0.25, 0.5, 1.0, 2.0])
_POW2 = st.integers(min_value=-6, max_value=6).map(lambda k: 2.0**k)


@st.composite
def _inputs(draw: st.DrawFn):
    pred: dict[str, float] = {}
    real: dict[str, float] = {}
    scale: dict[str, float] = {}
    for name in _NAMES:
        # each factor may be missing from any of the three mappings
        if draw(st.booleans()) or name.startswith("eq:"):
            pred[name] = draw(_MOVE)
        if draw(st.integers(0, 4)) > 0:
            real[name] = draw(_MOVE)
        if draw(st.integers(0, 4)) > 0:
            scale[name] = draw(_SCALE)
    return pred, real, scale


def _same(a: dict, b: dict) -> None:
    assert set(a) >= set(KEYS)
    assert set(b) >= set(KEYS)
    for k in KEYS:
        if a[k] is None or b[k] is None:
            assert a[k] is None
            assert b[k] is None
        else:
            # rel 1e-12 + abs 1e-12: z-vectors are bit-identical; only summation order can differ
            assert b[k] == pytest.approx(a[k], rel=1e-12, abs=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case=_inputs(), c=_POW2, min_abs_z=st.sampled_from([0.5, 1.0, 2.0]))
def test_c2_metamorphic_common_prediction_scaling(case, c: float, min_abs_z: float):
    # oracle: C2 invariance 1: multiplying every predicted move by the same c > 0 leaves all five unchanged
    # (cosine, Spearman ranks and signs are scale-free in the prediction).
    pred, real, scale = case
    base = score_prediction(pred, real, scale, min_abs_z=min_abs_z)
    scaled = score_prediction({k: c * v for k, v in pred.items()}, real, scale, min_abs_z=min_abs_z)
    _same(base, scaled)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case=_inputs(), c=_POW2, pick=st.sampled_from(_NAMES), min_abs_z=st.sampled_from([0.5, 1.0, 2.0]))
def test_c2_metamorphic_one_factor_unit_change(case, c: float, pick: str, min_abs_z: float):
    # oracle: C2 invariance 2: multiplying one factor's predicted move, realised move and scale by the same
    # c > 0 leaves its z unchanged, so all five scores are unchanged.
    pred, real, scale = case
    base = score_prediction(pred, real, scale, min_abs_z=min_abs_z)

    def bump(m: dict[str, float]) -> dict[str, float]:
        return {k: (c * v if k == pick else v) for k, v in m.items()}

    _same(base, score_prediction(bump(pred), bump(real), bump(scale), min_abs_z=min_abs_z))


@pytest.mark.criterion("C2")
@given(case=_inputs())
def test_c2_property_counts_and_bounds(case):
    # oracle: Requirement 2 by hand: n_factors = |{k in all three : scale > 0}|; n_direction counts those with
    # |real / scale| >= 1; cosine and Spearman lie in [-1, 1]; the hit rate in [0, 1].
    pred, real, scale = case
    used = [k for k in pred if k in real and k in scale and scale[k] > 0]
    out = score_prediction(pred, real, scale)
    assert out["n_factors"] == len(used)
    assert out["n_direction"] == sum(1 for k in used if abs(real[k] / scale[k]) >= 1.0)
    for k, lo in (("cosine", -1.0), ("rank_corr_equity", -1.0), ("direction_hit_rate", 0.0)):
        if out[k] is not None:
            # abs 1e-12: rounding at the bounds
            assert lo - 1e-12 <= out[k] <= 1.0 + 1e-12
