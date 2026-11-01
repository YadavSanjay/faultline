"""scenario-severity-anchor C4: reseeding legs that are authored drivers with their company-specific part.

Oracle: Requirement 4 by hand: for a leg among the authored drivers, ``seed' = authored[f] - systematic[f]``
(``systematic`` 0 when absent) and ``calibrated' = seed' * calibrated / seed`` (``seed'`` when the seed is 0);
other legs are unchanged. Values are dyadic, so the hand results are exact.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.severity import residual_seeds

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

SEED = {"eq:NVDA": -0.25, "eq:TSM": -0.125, "eq:AMD": 0.0, "eq:INTC": -0.5}
CAL = {"eq:NVDA": -0.375, "eq:TSM": -0.0625, "eq:AMD": 0.0, "eq:INTC": -0.75}
AUTHORED = {"eq:NVDA": -0.5, "eq:TSM": -0.25, "eq:AMD": -0.375, "eq:SOXX": -0.25, "ir:DGS10": 0.0078125}
SYSTEMATIC = {"eq:NVDA": -0.125, "eq:AMD": -0.25, "eq:INTC": -0.5, "eq:SOXX": -0.25}


@pytest.mark.criterion("C4")
def test_c4_point_reseeds_authored_legs():
    # oracle: Requirement 4 by hand:
    #   NVDA: seed' = -0.5 - (-0.125) = -0.375; cal' = -0.375 * (-0.375 / -0.25) = -0.5625
    #   TSM (no systematic -> 0): seed' = -0.25; cal' = -0.25 * (-0.0625 / -0.125) = -0.125
    #   AMD (seed 0): seed' = -0.375 - (-0.25) = -0.125; cal' = seed' = -0.125
    #   INTC (not authored): unchanged -0.5 / -0.75
    inputs = copy.deepcopy((SEED, CAL, AUTHORED, SYSTEMATIC))
    seed, cal = residual_seeds(*inputs)
    assert set(seed) == set(SEED)
    assert set(cal) == set(CAL)
    want_seed = {"eq:NVDA": -0.375, "eq:TSM": -0.25, "eq:AMD": -0.125, "eq:INTC": -0.5}
    want_cal = {"eq:NVDA": -0.5625, "eq:TSM": -0.125, "eq:AMD": -0.125, "eq:INTC": -0.75}
    for f in SEED:
        # rel 1e-12: spec tolerance (dyadic, exact)
        assert seed[f] == pytest.approx(want_seed[f], rel=1e-12), f
        assert cal[f] == pytest.approx(want_cal[f], rel=1e-12), f


@pytest.mark.criterion("C4")
def test_c4_point_final_move_is_the_authored_move():
    # oracle: Requirement 4 by hand: with all multipliers at 1, final = systematic + seed'; for NVDA
    # -0.125 + (-0.375) = -0.5 = authored (not authored + systematic = -0.625).
    seed, _ = residual_seeds(dict(SEED), dict(CAL), dict(AUTHORED), dict(SYSTEMATIC))
    # rel 1e-12: spec tolerance
    assert SYSTEMATIC["eq:NVDA"] + seed["eq:NVDA"] == pytest.approx(-0.5, rel=1e-12)
    assert SYSTEMATIC["eq:AMD"] + seed["eq:AMD"] == pytest.approx(-0.375, rel=1e-12)


@pytest.mark.criterion("C4")
def test_c4_refusals():
    # oracle: Requirement 4: seed and calibrated legs with different keys raise ValueError.
    short = {k: v for k, v in CAL.items() if k != "eq:INTC"}
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        residual_seeds(dict(SEED), short, dict(AUTHORED), dict(SYSTEMATIC))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        residual_seeds(dict(SEED), {**CAL, "eq:MU": -0.25}, dict(AUTHORED), dict(SYSTEMATIC))


_V = st.integers(min_value=-64, max_value=64).map(lambda i: i / 64)
_NAMES = [f"eq:N{i}" for i in range(8)]


@st.composite
def _case(draw: st.DrawFn):
    legs = draw(st.lists(st.sampled_from(_NAMES), min_size=1, max_size=8, unique=True))
    seed = {f: draw(_V) for f in legs}
    cal = {f: seed[f] * draw(st.sampled_from([0.5, 1.0, 1.5, 2.0])) for f in legs}
    authored = {f: draw(_V) for f in draw(st.lists(st.sampled_from(_NAMES), max_size=8, unique=True))}
    systematic = {f: draw(_V) for f in draw(st.lists(st.sampled_from(_NAMES), max_size=8, unique=True))}
    return seed, cal, authored, systematic


@pytest.mark.criterion("C4")
@given(case=_case())
def test_c4_property_identity_and_fact_ratio(case):
    # oracle: C4 identity: systematic[f] + seed'[f] = authored[f] for every authored leg; the fact ratio
    # calibrated'/seed' = calibrated/seed is kept (cross-multiplied); other legs are unchanged.
    seed_in, cal_in, authored, systematic = case
    before = copy.deepcopy(case)
    seed, cal = residual_seeds(seed_in, cal_in, authored, systematic)
    assert (seed_in, cal_in, authored, systematic) == before
    assert set(seed) == set(seed_in)
    assert set(cal) == set(cal_in)
    for f in seed_in:
        if f in authored:
            # abs 1e-12: spec tolerance (dyadic, exact)
            assert systematic.get(f, 0.0) + seed[f] == pytest.approx(authored[f], abs=1e-12), f
            if not seed_in[f]:  # a zero seed (inputs are exact dyadics)
                assert cal[f] == pytest.approx(seed[f], abs=1e-12), f
            else:
                assert cal[f] * seed_in[f] == pytest.approx(seed[f] * cal_in[f], abs=1e-12), f
        else:
            assert seed[f] == seed_in[f], f
            assert cal[f] == cal_in[f], f
