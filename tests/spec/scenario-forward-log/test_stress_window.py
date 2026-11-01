"""scenario-forward-log C3: whether a realised window was a stress for the book's market factor.

Oracle: Requirement 3 by hand: True iff realised[f] / scale[f] <= threshold_z; None when the factor or its
scale is missing or the scale is not positive. Moves and scales are dyadic so every z is exact.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.forward_log import stress_window


@pytest.mark.criterion("C3")
def test_c3_point_at_below_and_above_default_threshold():
    # oracle: Requirement 3 by hand, default eq:XLK and -2: -0.25 / 0.125 = -2 (at the threshold) -> True;
    # -0.5 / 0.125 = -4 -> True; -0.2421875 / 0.125 = -1.9375 -> False; +0.25 / 0.125 = 2 -> False.
    scale = {"eq:XLK": 0.125, "eq:SPY": 0.125}
    assert stress_window({"eq:XLK": -0.25}, scale) is True
    assert stress_window({"eq:XLK": -0.5, "eq:SPY": 1.0}, scale) is True
    assert stress_window({"eq:XLK": -0.2421875}, scale) is False
    assert stress_window({"eq:XLK": 0.25, "eq:SPY": -1.0}, scale) is False


@pytest.mark.criterion("C3")
def test_c3_point_custom_factor_and_threshold():
    # oracle: Requirement 3 by hand: market_factor eq:SPY, threshold -1.5: -0.75 / 0.5 = -1.5 -> True;
    # -0.5 / 0.5 = -1 -> False (eq:XLK's -4 is ignored); threshold +1: 0.5 / 0.5 = 1 -> True.
    scale = {"eq:XLK": 0.125, "eq:SPY": 0.5}
    assert stress_window({"eq:SPY": -0.75}, scale, market_factor="eq:SPY", threshold_z=-1.5) is True
    assert stress_window({"eq:SPY": -0.5, "eq:XLK": -0.5}, scale, market_factor="eq:SPY", threshold_z=-1.5) is False
    assert stress_window({"eq:SPY": 0.5}, scale, market_factor="eq:SPY", threshold_z=1.0) is True


@pytest.mark.criterion("C3")
def test_c3_none_cases():
    # oracle: Requirement 3 by hand: factor missing from realised; scale missing; scale 0; scale negative.
    # Control: the same factor with a positive scale, -1 / 0.25 = -4 -> True.
    assert stress_window({"eq:XLK": -1.0}, {"eq:XLK": 0.25}) is True
    assert stress_window({"eq:SPY": -1.0}, {"eq:XLK": 0.125, "eq:SPY": 0.125}) is None
    assert stress_window({"eq:XLK": -1.0}, {"eq:SPY": 0.125}) is None
    assert stress_window({"eq:XLK": -1.0}, {"eq:XLK": 0.0}) is None
    assert stress_window({"eq:XLK": -1.0}, {"eq:XLK": -0.125}) is None
    assert stress_window({"eq:SPY": -1.0}, {"eq:SPY": -0.5}, market_factor="eq:SPY") is None


_Z = st.integers(min_value=-48, max_value=48).map(lambda i: i / 8)
_S = st.sampled_from([0.015625, 0.125, 0.25, 0.5, 1.0, 4.0])
_THR = st.integers(min_value=-32, max_value=8).map(lambda i: i / 8)


@pytest.mark.criterion("C3")
@given(z=_Z, s=_S, thr=_THR)
def test_c3_property_threshold_rule(z: float, s: float, thr: float):
    # oracle: Requirement 3 by hand: realised = z * s (exact, dyadic) so realised / scale = z, and the result
    # is exactly (z <= thr) as a bool.
    out = stress_window({"eq:XLK": z * s}, {"eq:XLK": s}, threshold_z=thr)
    assert out is (z <= thr)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(z=_Z, s=_S, k=st.integers(min_value=-5, max_value=5), extra=_Z)
def test_c3_metamorphic_units_and_other_factors(z: float, s: float, k: int, extra: float):
    # oracle: Requirement 3: only the market factor's z matters, so rescaling its move and scale by the same
    # power of two, or adding other factors, leaves the answer unchanged (both equal z <= -2, the default).
    c = 2.0**k
    base = stress_window({"eq:XLK": z * s}, {"eq:XLK": s})
    moved = stress_window({"eq:XLK": c * z * s, "eq:OTHER": extra}, {"eq:XLK": c * s, "eq:OTHER": 1.0})
    assert base is (z <= -2.0)
    assert moved is base
