"""event-scenario-mapping C3: ``theme_news_multiplier``, the clamped theme-weighted news leg.

Oracle: Requirement 3 by hand: ``multiplier = clamp(1 + beta_event*score + t, min_scale, max_scale)``
with ``t = beta_tone * (-tone_z)`` only when ``theme.tone`` and ``tone_z`` is not None. Production
config: beta_event 0.15, beta_tone 0.10, window 90, halflife 30, clamp [0.8, 1.5]; AS_OF 2026-06-30.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.events import theme_news_multiplier

ITEMS = ["2.02", "5.02", "7.01", "8.01", "4.02"]


def _events(h):
    return [
        h.ev("2026-06-30", "7.01,5.02", "a1"),   # age 0: theme item 7.01 -> 0.2
        h.ev("2026-07-02", "2.02", "a0"),        # filed after as_of -> out
        h.ev("2026-05-31", "2.02,7.01", "a2"),   # age 30: (0.6 + 0.2) * 0.5 = 0.4
        h.ev("2026-06-01", "8.01", "a3"),        # no theme item -> out
    ]


@pytest.mark.criterion("C3")
def test_c3_point_with_tone(h):
    # oracle: hand calculation, theme {2.02, 7.01}, tone on, tone_z = -0.5:
    #   score = 0.2 + 0.4 = 0.6; t = 0.10 * 0.5 = 0.05; m = 1 + 0.15*0.6 + 0.05 = 1.14
    #   accessions a1, a2; item codes 7.01 then 2.02 (5.02 is not a theme item)
    th = h.theme(news_items=["2.02", "7.01"], tone=True)
    leg = theme_news_multiplier("NVDA", _events(h), -0.5, h.AS_OF, h.CFG, th, h.WEIGHTS)
    assert leg.multiplier == pytest.approx(1.14, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert leg.event_score == pytest.approx(0.6, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert leg.tone_z == pytest.approx(-0.5, rel=1e-12)  # the input, passed through
    assert leg.n_events == 2
    assert list(leg.accessions) == ["a1", "a2"]
    assert list(leg.item_codes) == ["7.01", "2.02"]
    assert leg.applied is True


@pytest.mark.criterion("C3")
def test_c3_point_tone_switched_off(h):
    # oracle: hand calculation, same events, tone off: t = 0 whatever tone_z is;
    #   m = 1 + 0.15*0.6 = 1.09; the leg's tone_z is None
    th = h.theme(news_items=["2.02", "7.01"], tone=False)
    leg = theme_news_multiplier("NVDA", _events(h), -0.5, h.AS_OF, h.CFG, th, h.WEIGHTS)
    assert leg.multiplier == pytest.approx(1.09, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert leg.event_score == pytest.approx(0.6, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert leg.tone_z is None
    assert leg.n_events == 2
    assert leg.applied is True


@pytest.mark.criterion("C3")
def test_c3_point_clamp_top(h):
    # oracle: hand calculation, tone_z = -10: t = 1.0; raw = 1 + 0.09 + 1.0 = 2.09 > 1.5 -> 1.5
    th = h.theme(news_items=["2.02", "7.01"], tone=True)
    leg = theme_news_multiplier("NVDA", _events(h), -10.0, h.AS_OF, h.CFG, th, h.WEIGHTS)
    assert leg.multiplier == pytest.approx(1.5, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert leg.event_score == pytest.approx(0.6, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert leg.applied is True


@pytest.mark.criterion("C3")
def test_c3_point_clamp_bottom(h):
    # oracle: hand calculation, no events, tone_z = +5: t = -0.5; raw = 0.5 < 0.8 -> 0.8
    th = h.theme(news_items=["2.02"], tone=True)
    leg = theme_news_multiplier("AMD", [], 5.0, h.AS_OF, h.CFG, th, h.WEIGHTS)
    assert leg.multiplier == pytest.approx(0.8, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert leg.event_score == pytest.approx(0.0, abs=1e-15)  # no events; abs because the target is 0
    assert leg.tone_z == pytest.approx(5.0, rel=1e-12)  # the input, passed through
    assert leg.n_events == 0
    assert list(leg.accessions) == []
    assert list(leg.item_codes) == []
    assert leg.applied is True


@pytest.mark.criterion("C3")
def test_c3_point_applied_flag(h):
    # oracle: Requirement 3's `applied` rule by hand, with no contributing events in each case:
    #   tone on, tone_z 0.0  -> m = 1.0 exactly, applied (tone on and tone_z given)
    #   tone on, tone_z None -> m = 1.0, not applied
    #   tone off, tone_z 3.0 -> m = 1.0, not applied, leg tone_z None
    #   zero-weight theme item (8.01 weight 0 in this table) -> m = 1.0, applied (a contributing event)
    events = [h.ev("2026-06-20", "8.01", "z1")]
    on = h.theme(news_items=["2.02"], tone=True)
    off = h.theme(news_items=["2.02"], tone=False)
    one = pytest.approx(1.0, rel=0, abs=0)  # 1 + 0 + 0: exactly 1.0, as Requirement 3's `applied` rule tests
    leg = theme_news_multiplier("X", events, 0.0, h.AS_OF, h.CFG, on, h.WEIGHTS)
    assert leg.multiplier == one
    assert (leg.applied, leg.n_events) == (True, 0)
    assert leg.tone_z == pytest.approx(0.0, abs=0)  # the input, passed through
    leg = theme_news_multiplier("X", events, None, h.AS_OF, h.CFG, on, h.WEIGHTS)
    assert leg.multiplier == one
    assert (leg.applied, leg.tone_z, leg.n_events) == (False, None, 0)
    leg = theme_news_multiplier("X", events, 3.0, h.AS_OF, h.CFG, off, h.WEIGHTS)
    assert leg.multiplier == one
    assert (leg.applied, leg.tone_z, leg.n_events) == (False, None, 0)
    weights = {**h.WEIGHTS, "8.01": 0}
    zero = h.theme(news_items=["8.01"])
    leg = theme_news_multiplier("X", events, None, h.AS_OF, h.CFG, zero, weights)
    assert leg.multiplier == one
    assert (leg.applied, leg.n_events, list(leg.accessions)) == (True, 1, ["z1"])
    assert list(leg.item_codes) == ["8.01"]


_event_specs = st.lists(
    st.tuples(st.integers(min_value=-10, max_value=110), st.lists(st.sampled_from(ITEMS), max_size=4)),
    max_size=8,
)
_tone = st.one_of(st.none(), st.floats(min_value=-20, max_value=20, allow_nan=False))
_items = st.sets(st.sampled_from(ITEMS))


def _build(h, specs):
    return [h.ev_age(age, ",".join(codes), f"p{k}") for k, (age, codes) in enumerate(specs)]


@pytest.mark.criterion("C3")
@given(specs=_event_specs, tone_z=_tone, items=_items, tone=st.booleans())
def test_c3_property_within_clamp_and_closed_form(h, specs, tone_z, items, tone):
    # oracle: Requirement 3 closed form computed in the test (conftest hand_score / hand_mult)
    th = h.theme(news_items=sorted(items), tone=tone)
    events = _build(h, specs)
    leg = theme_news_multiplier("X", events, tone_z, h.AS_OF, h.CFG, th, h.WEIGHTS)
    score, contributing = h.hand_score(events, th, h.WEIGHTS)
    assert h.CFG.min_scale <= leg.multiplier <= h.CFG.max_scale
    assert leg.multiplier == pytest.approx(h.hand_mult(score, tone_z, th), rel=1e-12)  # spec tolerance
    assert leg.n_events == len(contributing)
    assert list(leg.accessions) == [e.accession for e in contributing]


@pytest.mark.criterion("C3")
@given(specs=_event_specs, tone_z=_tone, crowding=st.booleans())
def test_c3_property_no_news_no_tone_is_exactly_one(h, specs, tone_z, crowding):
    # oracle: Requirement 3: a theme with no news items and no tone always yields exactly 1.0
    th = h.theme(crowding=crowding, trigger_items=["5.02"])
    leg = theme_news_multiplier("X", _build(h, specs), tone_z, h.AS_OF, h.CFG, th, h.WEIGHTS)
    assert leg.multiplier == pytest.approx(1.0, rel=0, abs=0)  # spec: exactly 1.0
    assert leg.n_events == 0
    assert leg.tone_z is None


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(specs=_event_specs, z1=_tone, z2=_tone, items=_items)
def test_c3_property_tone_off_ignores_tone_z(h, specs, z1, z2, items):
    # oracle: Requirement 3 closed form with t = 0 (tone off) plus the relation m(z1) == m(z2)
    th = h.theme(news_items=sorted(items | {"2.02"}), tone=False)
    events = [h.ev_age(0, "2.02", "anchor"), *_build(h, specs)]  # anchor: m != 1, never trivially 1
    leg1 = theme_news_multiplier("X", events, z1, h.AS_OF, h.CFG, th, h.WEIGHTS)
    leg2 = theme_news_multiplier("X", events, z2, h.AS_OF, h.CFG, th, h.WEIGHTS)
    score, _ = h.hand_score(events, th, h.WEIGHTS)
    assert leg1.multiplier == pytest.approx(h.hand_mult(score, None, th), rel=1e-12)  # spec tolerance
    assert leg1.multiplier == leg2.multiplier
    assert leg1.tone_z is None
    assert leg2.tone_z is None
