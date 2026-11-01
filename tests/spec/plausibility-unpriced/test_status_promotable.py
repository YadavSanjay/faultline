"""plausibility-unpriced C3: ``market_unpriced`` is flag-only; a bound breach still fails the scenario.

Oracle: the existing status rule (fail > flag > pass) and Requirement 4 by hand. A scenario whose only
finding is ``market_unpriced`` is ``"flag"`` and promotable; adding an equity bound breach on another
factor (bound 30%, move -50% -> ``("eq:AMD", "bound", -50.0, 30.0, "fail")``) makes it ``"fail"`` and not
promotable, whatever the order of the factors in the vector.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

BOUNDS = {"equity": {"max_abs_pct": 30}}
FACTORS = {"eq:NVDA": -0.25, "eq:AMD": -0.5, "eq:SMH": -0.2}
PROBS = {"NVDA": 0.0, "AMD": None, "SMH": 0.003}


def _expected(h):
    # Hand-built: NVDA p = 0.0 -> unpriced; AMD p = None but -50% > 30% cap -> bound fail (observed in
    # pct, -0.5 * 100 = -50.0); SMH p = 0.003 < 0.01 -> market_implied.
    return {
        ("eq:NVDA", "market_unpriced", -0.25, 0.0, "flag"),
        ("eq:AMD", "bound", -50.0, 30.0, "fail"),
        ("eq:SMH", "market_implied", 0.003, h.MIN_PROB, "flag"),
    }


@pytest.mark.criterion("C3")
def test_c3_point_only_unpriced_is_flag_and_promotable(h):
    # oracle: Requirement 4 by hand: the single market_unpriced finding is a flag -> status "flag",
    #   promotable True.
    result = h.checker(h.FakeMarket({"NVDA": 0.0}), BOUNDS).check(h.scenario({"eq:NVDA": -0.25}))
    assert h.rows(result) == [("eq:NVDA", "market_unpriced", -0.25, 0.0, "flag")]
    assert result.status == "flag"
    assert result.promotable is True
    assert result.to_dict()["promotable"] is True


@pytest.mark.criterion("C3")
def test_c3_point_bound_breach_with_unpriced_fails(h):
    # oracle: status rule fail > flag: an unpriced flag on eq:NVDA and a bound fail on eq:AMD -> "fail",
    #   not promotable; both findings recorded.
    market = h.FakeMarket({"NVDA": 0.0, "AMD": None})
    result = h.checker(market, BOUNDS).check(h.scenario({"eq:NVDA": -0.25, "eq:AMD": -0.5}))
    found = h.rows(result)
    assert ("eq:NVDA", "market_unpriced", -0.25, 0.0, "flag") in found
    bound = [r for r in found if r[1] == "bound"]
    assert len(bound) == 1
    assert bound[0][0] == "eq:AMD"
    assert bound[0][2] == pytest.approx(-50.0, rel=1e-12)  # decimal -> pct is one multiply by 100
    assert bound[0][3:] == (30.0, "fail")
    assert len(found) == 2
    assert result.status == "fail"
    assert result.promotable is False


@pytest.mark.criterion("C3")
@given(order=st.permutations(list(FACTORS)))
def test_c3_property_fail_regardless_of_order(h, order):
    # oracle: status rule fail > flag > pass is order-free: any ordering of the same three factors
    #   gives the same finding set, status "fail" and promotable False.
    vector = {k: FACTORS[k] for k in order}
    result = h.checker(h.FakeMarket(PROBS), BOUNDS).check(h.scenario(vector))
    found = h.rows(result)
    assert len(found) == 3
    rounded = {(f, c, round(o, 9), lim, s) for f, c, o, lim, s in found}  # -50.0 from x100; 9 dp is exact enough
    assert rounded == _expected(h)
    assert [r[0] for r in found] == list(order)
    assert result.status == "fail"
    assert result.promotable is False
