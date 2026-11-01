"""options-shock-sizing C5: ``exceeds_option_tail``, the option-tail severity check.

Oracle: Requirement 5 by hand: true iff both moves are non-zero, share a sign and
``|option_move| > |tier_move|``; false otherwise, including ``option_move is None``.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.options.sizing import exceeds_option_tail

CASES = [
    # (tier_move, option_move, verdict)  -- hand-checked against Requirement 5
    (-0.10, -0.20, True),    # option more extreme, downside
    (0.10, 0.25, True),      # option more extreme, upside
    (-0.20, -0.20, False),   # equal
    (-0.20, -0.10, False),   # option less extreme
    (0.30, 0.10, False),
    (-0.10, 0.20, False),    # opposite sign
    (0.10, -0.20, False),
    (0.0, -0.10, False),     # zero tier
    (-0.10, 0.0, False),     # zero option
    (-0.10, None, False),    # no option data
]


@pytest.mark.criterion("C5")
def test_c5_point_hand_checked_verdicts():
    # oracle: hand calculation, the verdict column of CASES (one list so a constant answer cannot pass).
    got = [exceeds_option_tail(t, o) for t, o, _ in CASES]
    assert got == [v for _, _, v in CASES]
    assert all(isinstance(g, bool) for g in got)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(("tier", "option", "verdict"), CASES)
def test_c5_point_each_case(tier, option, verdict):
    # oracle: hand calculation (CASES); paired with a case of the opposite verdict so a constant fails.
    assert exceeds_option_tail(tier, option) is verdict
    assert exceeds_option_tail(-0.05, -0.5 if not verdict else 0.5) is (not verdict)


_move = st.one_of(st.just(0.0), st.floats(min_value=-0.99, max_value=0.99, allow_nan=False))


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(a=_move, b=_move)
def test_c5_property_never_true_both_ways(h, a, b):
    # oracle: Requirement 5 closed form (h.hand_exceeds); swapping the arguments can't make both true
    #   because |b| > |a| and |a| > |b| can't both hold.
    ab = exceeds_option_tail(a, b)
    ba = exceeds_option_tail(b, a)
    assert ab is h.hand_exceeds(a, b)
    assert ba is h.hand_exceeds(b, a)
    assert not (ab and ba)


@pytest.mark.criterion("C5")
@given(a=st.floats(min_value=0.001, max_value=0.99), k=st.floats(min_value=1.001, max_value=50.0),
       sign=st.sampled_from([-1.0, 1.0]))
def test_c5_property_scaled_beyond_tier_exceeds(a, k, sign):
    # oracle: Requirement 5: an option move k > 1 times the tier move (same sign) is more extreme;
    #   the reverse pair (tier more extreme than option) is not.
    tier = sign * a
    option = tier * k
    assert exceeds_option_tail(tier, option) is True
    assert exceeds_option_tail(option, tier) is False
