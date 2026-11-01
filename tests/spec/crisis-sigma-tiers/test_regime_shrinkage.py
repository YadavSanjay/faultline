"""crisis-sigma-tiers C2: the regime's linear percentile -> shrinkage map, capped at ``max_shrinkage``.

Oracle: Requirement 2 by hand: ``min(idio - pct * (idio - sys), cap)``. Values are dyadic, so the hand results
are exact.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.implied_corr import regime_shrinkage

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C2")
def test_c2_point_map_and_cap():
    # oracle: hand calculation with sys 0.375, idio 0.625 (spread 0.25):
    #   pct 0.25 -> 0.625 - 0.0625 = 0.5625; cap 0.75 leaves it, cap 0.25 binds -> 0.25;
    #   pct 0 -> 0.625 (idio), pct 1 -> 0.375 (sys), pct 0.5 -> 0.5; cap 0.5 at pct 0.5 -> 0.5 (equal).
    cases = [(0.25, 0.75, 0.5625), (0.25, 0.25, 0.25), (0.0, 1.0, 0.625), (1.0, 1.0, 0.375),
             (0.5, 0.5, 0.5), (0.0, 0.5, 0.5), (1.0, 0.3125, 0.3125)]
    for pct, cap, want in cases:
        got = regime_shrinkage(pct, shrink_systemic=0.375, shrink_idiosyncratic=0.625, max_shrinkage=cap)
        # rel 1e-12: spec tolerance (dyadic, exact)
        assert got == pytest.approx(want, rel=1e-12), (pct, cap)


@pytest.mark.criterion("C2")
def test_c2_point_production_parameters_and_refusals():
    # oracle: hand calculation with the production map (sys 0.4, idio 0.65) and cap 0.3:
    #   pct 0.12 -> 0.65 - 0.12 * 0.25 = 0.62 (the calm 2026-10-06 regime) -> capped to 0.3;
    #   with cap 0.7 the map is unchanged at 0.62. Requirement 2: pct outside [0, 1] raises ValueError.
    # rel 1e-12: spec tolerance (decimal inputs, a few ulps of rounding)
    assert regime_shrinkage(0.12, shrink_systemic=0.4, shrink_idiosyncratic=0.65,
                            max_shrinkage=0.3) == pytest.approx(0.3, rel=1e-12)
    assert regime_shrinkage(0.12, shrink_systemic=0.4, shrink_idiosyncratic=0.65,
                            max_shrinkage=0.7) == pytest.approx(0.62, rel=1e-12)
    for bad in (-0.0625, 1.0625):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            regime_shrinkage(bad, shrink_systemic=0.4, shrink_idiosyncratic=0.65, max_shrinkage=0.3)


# Dyadic shrinkages in [1/64, 1]; percentiles in [0, 1].
level = st.integers(min_value=1, max_value=64).map(lambda i: i / 64.0)
pct_st = st.integers(min_value=0, max_value=64).map(lambda i: i / 64.0)


@pytest.mark.criterion("C2")
@given(a=level, b=level, cap=level, p1=pct_st, p2=pct_st)
def test_c2_property_non_increasing_and_capped(a, b, cap, p1, p2):
    # oracle: property: with sys <= idio the map is non-increasing in pct and never exceeds the cap;
    #   at pct 0 it is min(idio, cap) by hand.
    sys_, idio = min(a, b), max(a, b)
    lo, hi = min(p1, p2), max(p1, p2)
    kw = {"shrink_systemic": sys_, "shrink_idiosyncratic": idio, "max_shrinkage": cap}
    at_lo, at_hi = regime_shrinkage(lo, **kw), regime_shrinkage(hi, **kw)
    assert at_hi <= at_lo
    assert at_lo <= cap
    assert at_hi <= cap
    # rel 1e-12: spec tolerance (dyadic, exact)
    assert regime_shrinkage(0.0, **kw) == pytest.approx(min(idio, cap), rel=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(a=level, b=level, extra=st.integers(min_value=0, max_value=64).map(lambda i: i / 64.0), p=pct_st)
def test_c2_property_cap_at_or_above_idio_leaves_map(a, b, extra, p):
    # oracle: metamorphic: a cap >= idio (with sys <= idio) returns the uncapped linear map, computed by hand.
    sys_, idio = min(a, b), max(a, b)
    got = regime_shrinkage(p, shrink_systemic=sys_, shrink_idiosyncratic=idio, max_shrinkage=idio + extra)
    # rel 1e-12: spec tolerance (dyadic, exact)
    assert got == pytest.approx(idio - p * (idio - sys_), rel=1e-12)
