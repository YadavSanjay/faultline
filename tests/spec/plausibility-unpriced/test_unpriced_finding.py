"""plausibility-unpriced C1: a market-implied probability of 0.0 is recorded as ``market_unpriced``.

Oracle: Requirement 1 by hand. When the provider returns ``0.0`` for an ``eq:<TKR>`` move, the checker
records exactly one finding for that factor, ``(key, "market_unpriced", move, 0.0, "flag")``, the status
is ``"flag"`` and the scenario stays promotable.

``observed`` is the scenario move passed through unchanged and ``limit`` is the literal ``0.0``, so both
are compared exactly; no arithmetic happens between input and output.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

MOVES = st.one_of(  # any non-zero move of either sign
    st.floats(min_value=-0.95, max_value=-1e-6, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-6, max_value=0.95, allow_nan=False, allow_infinity=False))
TICKERS = st.sampled_from(["NVDA", "SMH", "XLK"])


@pytest.mark.criterion("C1")
def test_c1_point_downside_zero_prob_is_unpriced(h):
    # oracle: Requirement 1 by hand: p = 0.0 for a -25% NVDA move -> one finding
    #   ("eq:NVDA", "market_unpriced", -0.25, 0.0, "flag"); status "flag"; promotable.
    market = h.FakeMarket({"NVDA": 0.0})
    result = h.checker(market).check(h.scenario({"eq:NVDA": -0.25}))
    assert h.rows(result) == [("eq:NVDA", "market_unpriced", -0.25, 0.0, "flag")]
    assert result.status == "flag"
    assert result.promotable is True


@pytest.mark.criterion("C1")
def test_c1_point_upside_zero_prob_is_unpriced_and_not_implied(h):
    # oracle: Requirement 1 by hand: the upside branch clips to 0.0 -> ("eq:SMH", "market_unpriced",
    #   0.3, 0.0, "flag") and no "market_implied" finding for eq:SMH.
    market = h.FakeMarket({"SMH": 0.0})
    result = h.checker(market).check(h.scenario({"eq:SMH": 0.3}))
    assert h.rows(result) == [("eq:SMH", "market_unpriced", 0.3, 0.0, "flag")]
    assert all(r[1] != "market_implied" for r in h.rows(result))


@pytest.mark.criterion("C1")
@given(move=MOVES, ticker=TICKERS)
def test_c1_property_zero_prob_always_one_unpriced_finding(h, move, ticker):
    # oracle: Requirement 1 by hand, for any non-zero move of either sign and any equity ticker:
    #   exactly one finding, (eq:<TKR>, "market_unpriced", move, 0.0, "flag"); the move is passed
    #   through unchanged, so it is compared exactly.
    key = f"eq:{ticker}"
    result = h.checker(h.FakeMarket({ticker: 0.0})).check(h.scenario({key: move}))
    assert h.rows(result) == [(key, "market_unpriced", move, 0.0, "flag")]
    assert result.status == "flag"
    assert result.promotable is True
