"""event-scenario-mapping C5: ``equity_leg_multipliers`` for single-name equity drivers that are not legs.

Oracle: Requirement 5 by hand: ``f`` qualifies when it is a driver, an ``eq:<T>`` factor, not a leg,
not the lead, and ``T.upper()`` not excluded; its compound is ``n * c`` with ``n = news_mult.get(T, 1)``
only when the theme has news (news items or tone) and ``c = crowding_mult.get(T, 1)`` only when the
theme has crowding; compounds of exactly 1 are dropped.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.events import equity_leg_multipliers

DRIVERS = ["eq:NVDA", "eq:AMD", "eq:TSM", "eq:xlk", "eq:AVGO", "eq:MU", "ir:DGS10", "credit",
           "eq:INTC", "eq:CEG", "eq:LLY", "style:MOM"]
LEGS = {"eq:TSM"}
LEAD = "eq:NVDA"
EXCLUDED = {"XLK", "SMH"}
NEWS = {"NVDA": 1.4, "AMD": 1.2, "AVGO": 0.9, "MU": 1.0, "TSM": 1.3, "XLK": 1.1, "INTC": 2.0, "DGS10": 1.3}
CROWD = {"NVDA": 1.2, "AMD": 1.1, "AVGO": 1.0, "MU": 1.0, "TSM": 0.9, "XLK": 1.2, "INTC": 0.5, "LLY": 1.5}


def _approx_dict(got, want):
    assert set(got) == set(want)
    for f, v in want.items():
        assert got[f] == pytest.approx(v, rel=1e-12), f  # spec tolerance (rel 1e-12)


@pytest.mark.criterion("C5")
def test_c5_point_news_and_crowding(h):
    # oracle: hand calculation, theme with news items and crowding:
    #   NVDA lead, TSM leg, xlk excluded (upper-cased XLK), ir/credit/style not equity -> out
    #   AMD 1.2*1.1 = 1.32; AVGO 0.9*1.0 = 0.9; MU 1*1 -> out; INTC 2.0*0.5 = 1 exactly -> out;
    #   CEG neither map -> 1 -> out; LLY 1*1.5 = 1.5
    th = h.theme(news_items=["2.02"], crowding=True)
    got = equity_leg_multipliers(DRIVERS, LEGS, NEWS, CROWD, th, EXCLUDED, LEAD)
    _approx_dict(got, {"eq:AMD": 1.32, "eq:AVGO": 0.9, "eq:LLY": 1.5})


@pytest.mark.criterion("C5")
def test_c5_point_news_switch(h):
    # oracle: hand calculation per theme switch:
    #   tone only (no news items, no crowding): n only -> AMD 1.2, AVGO 0.9, INTC 2.0
    #   crowding only: c only -> AMD 1.1, INTC 0.5, LLY 1.5 (AVGO 1.0 -> out)
    #   neither: every compound is 1 -> {}
    tone_only = h.theme(tone=True)
    _approx_dict(equity_leg_multipliers(DRIVERS, LEGS, NEWS, CROWD, tone_only, EXCLUDED, LEAD),
                 {"eq:AMD": 1.2, "eq:AVGO": 0.9, "eq:INTC": 2.0})
    crowd_only = h.theme(crowding=True, trigger_items=["5.02"])
    _approx_dict(equity_leg_multipliers(DRIVERS, LEGS, NEWS, CROWD, crowd_only, EXCLUDED, LEAD),
                 {"eq:AMD": 1.1, "eq:INTC": 0.5, "eq:LLY": 1.5})
    assert equity_leg_multipliers(DRIVERS, LEGS, NEWS, CROWD, h.theme(), EXCLUDED, LEAD) == {}


@pytest.mark.criterion("C5")
def test_c5_point_no_lead(h):
    # oracle: hand calculation, lead None: NVDA now qualifies, 1.4*1.2 = 1.68; the rest as in the
    #   first point test
    th = h.theme(news_items=["7.01"], tone=True, crowding=True)
    got = equity_leg_multipliers(iter(DRIVERS), LEGS, NEWS, CROWD, th, EXCLUDED, None)
    _approx_dict(got, {"eq:NVDA": 1.68, "eq:AMD": 1.32, "eq:AVGO": 0.9, "eq:LLY": 1.5})


_POOL = ["eq:AAA", "eq:BBB", "eq:CCC", "eq:DDD", "eq:EEE", "ir:DGS2", "credit", "fx:TWD", "eqvol"]
_NAMES = ["AAA", "BBB", "CCC", "DDD", "EEE", "DGS2", "TWD"]
_mult = st.sampled_from([0.5, 0.8, 1.0, 1.25, 2.0])


@pytest.mark.criterion("C5")
@given(drivers=st.lists(st.sampled_from(_POOL), unique=True), legs=st.sets(st.sampled_from(_POOL)),
       excluded=st.sets(st.sampled_from(_NAMES)), lead=st.one_of(st.none(), st.sampled_from(_POOL)),
       news=st.dictionaries(st.sampled_from(_NAMES), _mult), crowd=st.dictionaries(st.sampled_from(_NAMES), _mult),
       items=st.sets(st.sampled_from(["2.02", "7.01"])), tone=st.booleans(), crowding=st.booleans())
def test_c5_property_returned_factors_qualify(h, drivers, legs, excluded, lead, news, crowd, items, tone,
                                              crowding):
    # oracle: Requirement 5 closed form computed in the test (conftest hand_equity) plus the invariant
    #   that every returned factor is an eq: driver, not a leg, not the lead, ticker not excluded.
    #   "eq:ZZZ" with news 1.5 under a news-on theme always qualifies, so the result is never empty.
    th = h.theme(news_items=sorted(items | {"2.02"}), tone=tone, crowding=crowding)
    drivers = [*drivers, "eq:ZZZ"]
    news = {**news, "ZZZ": 1.5}
    got = equity_leg_multipliers(drivers, legs, news, crowd, th, excluded, lead)
    for f in got:
        assert f.startswith("eq:") and f in drivers and f not in legs and f != lead
        assert f[3:].upper() not in excluded
        assert got[f] != pytest.approx(1.0, rel=0, abs=0)  # spec: compounds of exactly 1 are dropped
    _approx_dict(got, h.hand_equity(drivers, sorted(legs), news, crowd, th, sorted(excluded), lead))
