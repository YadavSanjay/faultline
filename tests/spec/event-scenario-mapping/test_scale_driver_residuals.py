"""event-scenario-mapping C6: ``scale_driver_residuals`` scales only the company-specific part of a driver.

Oracle: Requirement 6 by hand: for ``f`` in both ``drivers`` and ``multipliers``,
``d' = s + (d - s) * clamp(m, 1/cap, cap)`` with ``s = systematic.get(f, 0)``; other drivers unchanged;
``ValueError`` for ``cap < 1`` or a multiplier that is not finite or is <= 0; inputs not mutated.
"""

from __future__ import annotations

import copy
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.events import scale_driver_residuals

DRIVERS = {"eq:A": -0.20, "eq:B": 0.10, "eq:C": -0.05, "eq:D": 0.30, "ir:DGS10": 0.01}
SYSTEMATIC = {"eq:A": -0.12, "eq:B": 0.04, "eq:D": 0.30, "eq:Q": 0.5}
MULTS = {"eq:A": 1.25, "eq:B": 3.0, "eq:C": 0.5, "eq:D": 1.4, "eq:Z": 1.3}


def _approx_dict(got, want):
    assert set(got) == set(want)
    for f, v in want.items():
        # rel 1e-12: spec tolerance; abs 1e-15 for values that cancel towards 0
        assert got[f] == pytest.approx(v, rel=1e-12, abs=1e-15), f


@pytest.mark.criterion("C6")
def test_c6_point_scaled_drivers():
    # oracle: hand calculation, cap 1.5:
    #   A: -0.12 + (-0.20 + 0.12) * 1.25 = -0.12 - 0.10 = -0.22
    #   B: m 3.0 -> 1.5: 0.04 + 0.06 * 1.5 = 0.13
    #   C: s = 0 (absent), m 0.5 -> 1/1.5: -0.05 / 1.5
    #   D: d = s: 0.30 + 0 = 0.30;  ir:DGS10 unchanged;  eq:Z not a driver -> ignored
    drivers, systematic, mults = (copy.deepcopy(x) for x in (DRIVERS, SYSTEMATIC, MULTS))
    got = scale_driver_residuals(drivers, systematic, mults, cap=1.5)
    _approx_dict(got, {"eq:A": -0.22, "eq:B": 0.13, "eq:C": -0.05 / 1.5, "eq:D": 0.30, "ir:DGS10": 0.01})
    assert (drivers, systematic, mults) == (DRIVERS, SYSTEMATIC, MULTS)  # inputs not mutated
    assert got is not drivers


@pytest.mark.criterion("C6")
def test_c6_point_cap_one_is_identity():
    # oracle: hand calculation, cap = 1 (allowed: only cap < 1 raises): clamp(m, 1, 1) = 1, so
    #   d' = s + (d - s) = d for every driver
    got = scale_driver_residuals(DRIVERS, SYSTEMATIC, MULTS, cap=1.0)
    _approx_dict(got, DRIVERS)


@pytest.mark.criterion("C6")
@pytest.mark.parametrize(("mults", "cap"), [
    (MULTS, 0.99),                                  # cap < 1
    ({**MULTS, "eq:A": 0.0}, 1.5),                  # multiplier 0
    ({**MULTS, "eq:B": -1.0}, 1.5),                 # negative multiplier
    ({**MULTS, "eq:C": math.inf}, 1.5),             # not finite
    ({**MULTS, "eq:D": math.nan}, 1.5),             # not finite
], ids=["cap<1", "zero", "negative", "inf", "nan"])
def test_c6_point_value_errors(mults, cap):
    # oracle: Requirement 6's ValueError cases by hand: cap < 1, or a driver's multiplier <= 0 or not finite
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        scale_driver_residuals(DRIVERS, SYSTEMATIC, mults, cap=cap)


_val = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False)
_mult = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False)
_cap = st.floats(min_value=1.0, max_value=5.0, allow_nan=False)
_keys = st.sampled_from(["eq:A", "eq:B", "eq:C", "credit", "ir:DGS2"])


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(drivers=st.dictionaries(_keys, _val, min_size=1), systematic=st.dictionaries(_keys, _val), cap=_cap)
def test_c6_property_unit_multipliers_leave_drivers_unchanged(drivers, systematic, cap):
    # oracle: Requirement 6 by hand: m = 1 -> d' = s + (d - s) = d. The "anchor" driver (0.25) is
    #   always present so the result is never trivially empty.
    drivers = {**drivers, "eq:ANCHOR": 0.25}
    snapshot = copy.deepcopy((drivers, systematic))
    got = scale_driver_residuals(drivers, systematic, dict.fromkeys(drivers, 1.0), cap=cap)
    _approx_dict(got, drivers)
    assert (drivers, systematic) == snapshot


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(d=_val, m=_mult, cap=_cap)
def test_c6_property_no_residual_no_change(d, m, cap):
    # oracle: Requirement 6 by hand: drivers[f] == s -> d' = s + 0 * m' = d for any multiplier
    got = scale_driver_residuals({"eq:A": d, "eq:B": 0.5}, {"eq:A": d}, {"eq:A": m}, cap=cap)
    _approx_dict(got, {"eq:A": d, "eq:B": 0.5})


@pytest.mark.criterion("C6")
@given(d=_val, s=_val, m=_mult, cap=_cap)
def test_c6_property_residual_bounded_by_cap(d, s, m, cap):
    # oracle: Requirement 6 closed form computed in the test and the bound
    #   |d' - s| <= cap * |d - s| (since clamp(m, 1/cap, cap) <= cap)
    want = s + (d - s) * max(1.0 / cap, min(cap, m))
    got = scale_driver_residuals({"eq:A": d, "credit": 0.01}, {"eq:A": s}, {"eq:A": m}, cap=cap)
    assert got["eq:A"] == pytest.approx(want, rel=1e-12, abs=1e-15)  # spec tolerance; abs near 0
    assert abs(got["eq:A"] - s) <= cap * abs(d - s) * (1 + 1e-12) + 1e-15  # same float slack as above
    assert got["credit"] == pytest.approx(0.01, rel=1e-12)  # spec tolerance; an untouched driver
