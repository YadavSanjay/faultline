"""plausibility-unpriced C2: which market finding (if any) a probability ``p`` produces.

Oracle: Requirements 1-3 by hand, with ``min_prob = 0.01``:

* ``p <= 0.0``            -> ``(key, "market_unpriced", move, 0.0, "flag")``
* ``0.0 < p < min_prob``  -> ``(key, "market_implied", p, min_prob, "flag")`` (unchanged from today)
* ``p >= min_prob``, ``None``, a zero move, a non-equity factor, a disabled check or no provider
  -> no market finding.

Probabilities and moves are passed through unchanged and ``min_prob`` is the configured literal, so
findings are compared exactly.
"""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

PROBS = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
MOVES = st.one_of(  # any non-zero move of either sign
    st.floats(min_value=-0.95, max_value=-1e-6, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-6, max_value=0.95, allow_nan=False, allow_infinity=False))


@pytest.mark.criterion("C2")
def test_c2_point_small_prob_implied_next_to_zero_prob_unpriced(h):
    # oracle: Requirement 2 by hand for eq:NVDA (p = 0.005 < 0.01 -> market_implied, observed = p,
    #   limit = min_prob) and Requirement 1 for eq:SMH (p = 0.0 -> market_unpriced, observed = move);
    #   findings in vector order.
    market = h.FakeMarket({"NVDA": 0.005, "SMH": 0.0})
    result = h.checker(market).check(h.scenario({"eq:NVDA": -0.2, "eq:SMH": -0.4}))
    assert h.rows(result) == [
        ("eq:NVDA", "market_implied", 0.005, 0.01, "flag"),
        ("eq:SMH", "market_unpriced", -0.4, 0.0, "flag"),
    ]
    assert result.status == "flag"


@pytest.mark.criterion("C2")
def test_c2_point_no_finding_cases_beside_unpriced(h):
    # oracle: Requirement 3 by hand: p = None (NVDA), p = min_prob exactly (XLK), p = 0.5 (AMD), a zero
    #   move (MU, provider would say 0.0) and a non-equity factor record nothing; only eq:SMH
    #   (p = 0.0, move -0.35) records ("eq:SMH", "market_unpriced", -0.35, 0.0, "flag").
    market = h.FakeMarket({"NVDA": None, "XLK": 0.01, "AMD": 0.5, "MU": 0.0, "SMH": 0.0})
    vector = {"eq:NVDA": -0.6, "eq:XLK": -0.3, "eq:AMD": 0.2, "eq:MU": 0.0,
              "fx:EURUSD": -0.05, "eq:SMH": -0.35}
    result = h.checker(market).check(h.scenario(vector))
    assert h.rows(result) == [("eq:SMH", "market_unpriced", -0.35, 0.0, "flag")]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_c2_metamorphic_disabled_or_no_provider_records_nothing(h):
    # oracle: Requirement 3 by hand: the same p = 0.0 scenario gives one market_unpriced finding
    #   with the check enabled, and no finding when the check is disabled or there is no provider.
    vector = {"eq:NVDA": -0.25}
    enabled = h.checker(h.FakeMarket({"NVDA": 0.0})).check(h.scenario(vector))
    disabled = h.checker(h.FakeMarket({"NVDA": 0.0}), enabled=False).check(h.scenario(vector))
    no_provider = h.checker(None).check(h.scenario(vector))
    assert h.rows(enabled) == [("eq:NVDA", "market_unpriced", -0.25, 0.0, "flag")]
    assert h.rows(disabled) == []
    assert h.rows(no_provider) == []
    assert disabled.status == "pass"
    assert no_provider.status == "pass"


@pytest.mark.criterion("C2")
@given(p=PROBS, move=MOVES)
@example(p=0.0, move=-0.2)
@example(p=0.005, move=0.1)
@example(p=0.01, move=-0.5)
def test_c2_property_market_finding_kind_by_prob(h, p, move):
    # oracle: the C2 property by hand: unpriced exactly when p <= 0.0, implied exactly when
    #   0 < p < min_prob, absent otherwise; never more than one market finding for the factor.
    result = h.checker(h.FakeMarket({"NVDA": p})).check(h.scenario({"eq:NVDA": move}))
    found = h.market_rows(result, "eq:NVDA")
    assert len(found) <= 1
    if p <= 0.0:
        expected = [("eq:NVDA", "market_unpriced", move, 0.0, "flag")]
    elif p < h.MIN_PROB:
        expected = [("eq:NVDA", "market_implied", p, h.MIN_PROB, "flag")]
    else:
        expected = []
    assert found == expected
