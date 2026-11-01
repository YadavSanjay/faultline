"""options-shock-sizing C4: ``option_tiers``, mild/adverse/severe moves from option quantiles.

Oracle: Requirement 4 by hand: ``{"mild": mild, "adverse": adverse, "severe": adverse * severe_factor}``
with the severe magnitude capped at ``bound`` (sign kept; None = no cap).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.options.sizing import option_tiers


def _check(got, want):
    assert set(got) == {"mild", "adverse", "severe"}
    for key, value in want.items():
        assert got[key] == pytest.approx(value, rel=1e-12), key  # spec tolerance (rel 1e-12)


@pytest.mark.criterion("C4")
def test_c4_point_downside_uncapped():
    # oracle: hand calculation: severe = -0.22 * 1.5 = -0.33; no cap; a loose cap 0.5 > 0.33 changes nothing.
    want = {"mild": -0.14, "adverse": -0.22, "severe": -0.33}
    _check(option_tiers(-0.14, -0.22, severe_factor=1.5, bound=None), want)
    _check(option_tiers(-0.14, -0.22, severe_factor=1.5, bound=0.5), want)


@pytest.mark.criterion("C4")
def test_c4_point_downside_capped():
    # oracle: hand calculation: severe = -0.22 * 1.5 = -0.33, |-0.33| > 0.3 -> capped to -0.3 (sign kept).
    _check(option_tiers(-0.14, -0.22, severe_factor=1.5, bound=0.3), {"mild": -0.14, "adverse": -0.22, "severe": -0.3})


@pytest.mark.criterion("C4")
def test_c4_point_upside_uncapped_and_capped():
    # oracle: hand calculation: severe = 0.1 * 2 = 0.2; with bound 0.15 -> 0.15; with bound 0.25 -> 0.2.
    _check(option_tiers(0.05, 0.1, severe_factor=2.0, bound=None), {"mild": 0.05, "adverse": 0.1, "severe": 0.2})
    _check(option_tiers(0.05, 0.1, severe_factor=2.0, bound=0.15), {"mild": 0.05, "adverse": 0.1, "severe": 0.15})
    _check(option_tiers(0.05, 0.1, severe_factor=2.0, bound=0.25), {"mild": 0.05, "adverse": 0.1, "severe": 0.2})


@pytest.mark.criterion("C4")
def test_c4_point_boundary_values_allowed():
    # oracle: hand calculation: |adverse| == |mild| and severe_factor == 1 are valid (only strict
    #   violations raise): severe = -0.2 * 1 = -0.2.
    _check(option_tiers(-0.2, -0.2, severe_factor=1.0, bound=None), {"mild": -0.2, "adverse": -0.2, "severe": -0.2})


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(
    ("mild", "adverse", "factor", "bound"),
    [
        (0.0, -0.2, 1.5, None),     # mild zero
        (-0.1, 0.0, 1.5, None),     # adverse zero
        (0.0, 0.0, 1.5, None),      # both zero
        (-0.1, 0.2, 1.5, None),     # different signs
        (0.1, -0.2, 1.5, None),
        (-0.3, -0.2, 1.5, None),    # |adverse| < |mild|
        (0.3, 0.2, 1.5, None),
        (-0.1, -0.2, 0.99, None),   # severe_factor < 1
        (-0.1, -0.2, 1.5, 0.0),     # bound <= 0
        (-0.1, -0.2, 1.5, -0.4),
    ],
)
def test_c4_point_invalid_inputs_raise(mild, adverse, factor, bound):
    # oracle: Requirement 4: each listed invalid input raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        option_tiers(mild, adverse, severe_factor=factor, bound=bound)


@st.composite
def _moves(draw):
    m = draw(st.floats(min_value=0.001, max_value=0.5))
    a = m * draw(st.floats(min_value=1.0, max_value=3.0))
    sign = draw(st.sampled_from([-1.0, 1.0]))
    return sign * m, sign * a


@pytest.mark.criterion("C4")
@given(moves=_moves(), factor=st.floats(min_value=1.0, max_value=4.0))
def test_c4_property_uncapped_ordering_and_sign(h, moves, factor):
    # oracle: Requirement 4 closed form (h.hand_tiers); |mild| <= |adverse| <= |severe|, one sign.
    mild, adverse = moves
    got = option_tiers(mild, adverse, severe_factor=factor, bound=None)
    _check(got, h.hand_tiers(mild, adverse, factor, None))
    assert abs(got["mild"]) <= abs(got["adverse"]) <= abs(got["severe"])
    assert len({v > 0 for v in got.values()}) == 1 and all(v != 0 for v in got.values())


@pytest.mark.criterion("C4")
@given(moves=_moves(), factor=st.floats(min_value=1.0, max_value=4.0), bound=st.floats(min_value=0.01, max_value=2.0))
def test_c4_property_capped_within_bound_and_sign(h, moves, factor, bound):
    # oracle: Requirement 4 closed form (h.hand_tiers); |severe| <= bound, one sign across tiers.
    mild, adverse = moves
    got = option_tiers(mild, adverse, severe_factor=factor, bound=bound)
    _check(got, h.hand_tiers(mild, adverse, factor, bound))
    assert abs(got["severe"]) <= bound
    assert len({v > 0 for v in got.values()}) == 1 and all(v != 0 for v in got.values())
