"""event-scenario-mapping C2: ``theme_event_score``, the age-decayed sum of theme weights.

Oracle: Requirement 2 by hand: an event in ``[0, window_days]`` adds the sum of ``item_weights`` over
its distinct item codes in ``theme.news_items``, times ``0.5 ** (age / halflife)`` (1 for halflife <= 0).
``AS_OF`` is 2026-06-30; ``CFG`` is the production NewsMapConfig (window 90, halflife 30).
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.events import theme_event_score

ITEMS = ["2.02", "5.02", "7.01", "8.01", "4.02"]


def _events(h):
    ev = h.ev
    return [
        ev("2026-07-01", "2.02", "e5"),               # age -1: filed after as_of -> out
        ev("2026-06-30", "2.02", "e1"),               # age 0: 0.6 * 1
        ev("not-a-date", "2.02", "e7"),               # unparsable -> skipped
        ev("2026-05-31", "7.01,8.01", "e2"),          # age 30: 0.2 * 0.5 (8.01 not a theme item)
        ev("2026-06-15", "5.02,8.01", "e6"),          # age 15: no theme item -> out
        ev("2026-04-01", "2.02,7.01,2.02", "e3"),     # age 90 (window edge): (0.6 + 0.2) * 0.125, 2.02 once
        ev("2026-03-31", "2.02", "e4"),               # age 91 -> out
        ev("2026-06-20", ",,7.01,", "e8"),            # age 10: blanks dropped, 0.2 * 0.5**(1/3)
    ]


@pytest.mark.criterion("C2")
def test_c2_point_decayed_sum(h):
    # oracle: hand calculation (see the per-event comments above):
    #   score = 0.6 + 0.2*0.5 + 0.8*0.125 + 0.2*2**(-1/3) = 0.8 + 0.2*2**(-1/3)
    #   contributing = e1, e2, e3, e8 in input order
    th = h.theme(news_items=["2.02", "7.01"])
    score, contributing = theme_event_score(_events(h), th, h.WEIGHTS, h.AS_OF, h.CFG)
    want = 0.6 + 0.2 * 0.5 + 0.8 * 0.125 + 0.2 * 2 ** (-1 / 3)
    assert score == pytest.approx(want, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert [e.accession for e in contributing] == ["e1", "e2", "e3", "e8"]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("halflife", [0.0, -5.0])
def test_c2_point_no_decay_when_halflife_not_positive(h, halflife):
    # oracle: hand calculation, decay = 1 when halflife <= 0: 0.6 + 0.2 + 0.8 + 0.2 = 1.8
    cfg = dataclasses.replace(h.CFG, decay_halflife_days=halflife)
    th = h.theme(news_items=["2.02", "7.01"])
    score, contributing = theme_event_score(_events(h), th, h.WEIGHTS, h.AS_OF, cfg)
    assert score == pytest.approx(1.8, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert [e.accession for e in contributing] == ["e1", "e2", "e3", "e8"]


@pytest.mark.criterion("C2")
def test_c2_point_window_zero_counts_only_same_day(h):
    # oracle: hand calculation, window_days = 0: only e1 (age 0) is in [0, 0]: 0.6 * 1 = 0.6
    cfg = dataclasses.replace(h.CFG, window_days=0)
    th = h.theme(news_items=["2.02", "7.01"])
    score, contributing = theme_event_score(_events(h), th, h.WEIGHTS, h.AS_OF, cfg)
    assert score == pytest.approx(0.6, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert [e.accession for e in contributing] == ["e1"]


@pytest.mark.criterion("C2")
def test_c2_point_other_theme(h):
    # oracle: hand calculation for news_items {5.02, 8.01}: e2 age 30 -> 0.2*0.5 = 0.1;
    #   e6 age 15 -> (1.0 + 0.2) * 0.5**0.5; e1, e3, e8 have no theme item; e4/e5/e7 out
    th = h.theme(news_items=["5.02", "8.01"])
    score, contributing = theme_event_score(_events(h), th, h.WEIGHTS, h.AS_OF, h.CFG)
    assert score == pytest.approx(0.1 + 1.2 * 0.5**0.5, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert [e.accession for e in contributing] == ["e2", "e6"]


_event_specs = st.lists(
    st.tuples(st.integers(min_value=-10, max_value=110), st.lists(st.sampled_from(ITEMS), max_size=4)),
    min_size=0, max_size=8,
)


def _build(h, specs, prefix):
    return [h.ev_age(age, ",".join(codes), f"{prefix}{k}") for k, (age, codes) in enumerate(specs)]


@pytest.mark.criterion("C2")
@given(specs=_event_specs)
def test_c2_property_empty_news_items_scores_zero(h, specs):
    # oracle: Requirement 2 by hand: with no theme items no event has a theme item, so the score is 0
    #   and nothing contributes.
    events = _build(h, specs, "a")
    score, contributing = theme_event_score(events, h.theme(tone=True, crowding=True), h.WEIGHTS, h.AS_OF, h.CFG)
    assert score == pytest.approx(0.0, abs=1e-15)  # nothing is summed; abs because the target is 0
    assert contributing == []


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(specs=_event_specs, age=st.integers(min_value=-10, max_value=110),
       codes=st.lists(st.sampled_from(["5.02", "8.01", "4.02"]), max_size=3), pos=st.integers(min_value=0))
def test_c2_property_non_theme_event_changes_nothing(h, specs, age, codes, pos):
    # oracle: Requirement 2 by hand (conftest hand_score) plus the metamorphic relation: an event with no
    #   theme item (theme = {2.02, 7.01}) adds nothing and does not contribute.
    th = h.theme(news_items=["2.02", "7.01"])
    anchor = h.ev_age(5, "2.02", "anchor")  # always contributes, so the result is never trivially empty
    events = [anchor, *_build(h, specs, "a")]
    extra = h.ev_age(age, ",".join(codes), "extra")
    bigger = list(events)
    bigger.insert(pos % (len(events) + 1), extra)
    base_score, base_contrib = theme_event_score(events, th, h.WEIGHTS, h.AS_OF, h.CFG)
    score, contrib = theme_event_score(bigger, th, h.WEIGHTS, h.AS_OF, h.CFG)
    want_score, want_contrib = h.hand_score(events, th, h.WEIGHTS)
    assert base_score == pytest.approx(want_score, rel=1e-12)  # spec tolerance (rel 1e-12)
    assert score == pytest.approx(base_score, rel=1e-12)  # same terms, possibly summed in another order
    assert [e.accession for e in base_contrib] == [e.accession for e in want_contrib]
    assert [e.accession for e in contrib] == [e.accession for e in base_contrib]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(specs=_event_specs, mask=st.lists(st.booleans(), min_size=8, max_size=8))
def test_c2_property_additive_over_disjoint_split(h, specs, mask):
    # oracle: Requirement 2 by hand (conftest hand_score) plus additivity: score(A + B) = score(A) + score(B).
    th = h.theme(news_items=["2.02", "7.01", "5.02"])
    anchor = h.ev_age(3, "5.02", "anchor")
    events = [anchor, *_build(h, specs, "a")]
    part_a = [e for k, e in enumerate(events) if mask[k % 8]]
    part_b = [e for k, e in enumerate(events) if not mask[k % 8]]
    total, contrib = theme_event_score(events, th, h.WEIGHTS, h.AS_OF, h.CFG)
    score_a, contrib_a = theme_event_score(part_a, th, h.WEIGHTS, h.AS_OF, h.CFG)
    score_b, contrib_b = theme_event_score(part_b, th, h.WEIGHTS, h.AS_OF, h.CFG)
    want, want_contrib = h.hand_score(events, th, h.WEIGHTS)
    # rel 1e-12 + abs 1e-15: spec tolerance; summation order differs between the split and the whole
    assert total == pytest.approx(want, rel=1e-12, abs=1e-15)
    assert total == pytest.approx(score_a + score_b, rel=1e-12, abs=1e-15)
    assert [e.accession for e in contrib] == [e.accession for e in want_contrib]
    assert {e.accession for e in contrib} == {e.accession for e in contrib_a + contrib_b}
