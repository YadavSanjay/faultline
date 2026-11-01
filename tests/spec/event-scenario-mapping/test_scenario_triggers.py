"""event-scenario-mapping C4: ``scenario_triggers`` maps 8-K items to the scenarios they make live.

Oracle: Requirement 4 by hand: one ``TriggerHit(item, TICKER, filing_date[:10], accession)`` per
(event, item) with the ticker in ``trigger_names`` (or any ticker when empty), the item in
``trigger_items`` and ``0 <= age <= window_days``; sorted by ``(filing_date, ticker, item)``; ``[]`` for
a scenario with trigger items but no hit; no entry for a scenario without trigger items.
AS_OF is 2026-06-30.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.events import scenario_triggers


def TriggerHit(item, ticker, filing_date, accession):  # noqa: N802 - mirrors the spec's TriggerHit fields
    return (item, ticker, filing_date, accession)


def _themes(h):
    return {
        "ai_deflation": h.theme(news_items=["2.02"], trigger_items=["2.02"], trigger_names=["GOOGL", "META"]),
        "keyman_nvda": h.theme(trigger_items=["5.02", "4.02"], trigger_names=["NVDA"]),
        "any_name": h.theme(trigger_items=["7.01"]),
        "quiet": h.theme(trigger_items=["8.01"], trigger_names=["TSM"]),
        "no_triggers": h.theme(news_items=["2.02", "7.01"], tone=True, crowding=True),
    }


def _events(h):
    ev = h.ev
    return {
        "googl": [ev("2026-06-30T16:05:00", "2.02,7.01", "g1"),   # age 0
                  ev("2026-05-31", "2.02", "g2"),                 # age 30 = window edge
                  ev("2026-05-30", "2.02", "g3")],                # age 31: out of window
        "META": [ev("2026-07-01", "2.02", "m1"),                  # filed after as_of: never counts
                 ev("2026-06-10", "8.01,2.02", "m2")],
        "Nvda": [ev("2026-06-10", "5.02,4.02", "n1"),
                 ev("2026-06-01", "2.02", "n2")],                 # NVDA is not an ai_deflation name
        "AMD": [ev("2026-06-20", "2.02,5.02,7.01", "a1")],       # not a keyman/ai_deflation name
        "aapl": [ev("2026-06-20", "7.01", "p1")],
        "TSM": [ev("2026-04-01", "8.01", "t1")],                  # age 90: out of window
    }


# Hand-listed hits for window_days = 30, sorted by (filing_date, ticker, item).
WANT_30 = {
    "ai_deflation": [TriggerHit("2.02", "GOOGL", "2026-05-31", "g2"),
                     TriggerHit("2.02", "META", "2026-06-10", "m2"),
                     TriggerHit("2.02", "GOOGL", "2026-06-30", "g1")],
    "keyman_nvda": [TriggerHit("4.02", "NVDA", "2026-06-10", "n1"),
                    TriggerHit("5.02", "NVDA", "2026-06-10", "n1")],
    "any_name": [TriggerHit("7.01", "AAPL", "2026-06-20", "p1"),
                 TriggerHit("7.01", "AMD", "2026-06-20", "a1"),
                 TriggerHit("7.01", "GOOGL", "2026-06-30", "g1")],
    "quiet": [],
}


def _as_tuples(result):
    return {k: [tuple(x) for x in v] for k, v in result.items()}


@pytest.mark.criterion("C4")
def test_c4_point_hits_window_30(h):
    # oracle: Requirement 4 by hand (WANT_30 and the per-event comments above); no_triggers is absent
    got = scenario_triggers(_events(h), h.AS_OF, 30, _themes(h))
    assert _as_tuples(got) == _as_tuples(WANT_30)
    assert "no_triggers" not in got
    hit = got["keyman_nvda"][0]  # TriggerHit's named fields (spec Interface)
    assert (hit.item, hit.ticker, hit.filing_date, hit.accession) == ("4.02", "NVDA", "2026-06-10", "n1")


@pytest.mark.criterion("C4")
def test_c4_point_window_zero(h):
    # oracle: Requirement 4 by hand, window_days = 0: only g1 (filed on as_of) is in [0, 0]
    got = scenario_triggers(_events(h), h.AS_OF, 0, _themes(h))
    want = {
        "ai_deflation": [TriggerHit("2.02", "GOOGL", "2026-06-30", "g1")],
        "keyman_nvda": [],
        "any_name": [TriggerHit("7.01", "GOOGL", "2026-06-30", "g1")],
        "quiet": [],
    }
    assert _as_tuples(got) == _as_tuples(want)


@pytest.mark.criterion("C4")
def test_c4_point_window_90_reaches_edge(h):
    # oracle: Requirement 4 by hand, window_days = 90: t1 (age 90) now counts for "quiet"; g3 (age 31)
    #   joins ai_deflation first by date
    got = scenario_triggers(_events(h), h.AS_OF, 90, _themes(h))
    assert [tuple(x) for x in got["quiet"]] == [("8.01", "TSM", "2026-04-01", "t1")]
    assert [tuple(x) for x in got["ai_deflation"]] == [
        ("2.02", "GOOGL", "2026-05-30", "g3"), ("2.02", "GOOGL", "2026-05-31", "g2"),
        ("2.02", "META", "2026-06-10", "m2"), ("2.02", "GOOGL", "2026-06-30", "g1")]
    assert "no_triggers" not in got


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(rng=st.randoms(use_true_random=False))
def test_c4_property_input_order_irrelevant(h, rng):
    # oracle: Requirement 4 by hand (WANT_30) plus the relation: shuffling tickers and events within a
    #   ticker leaves the sorted result unchanged.
    items = list(_events(h).items())
    rng.shuffle(items)
    shuffled = {}
    for tk, evs in items:
        evs = list(evs)
        rng.shuffle(evs)
        shuffled[tk] = evs
    got = scenario_triggers(shuffled, h.AS_OF, 30, _themes(h))
    assert _as_tuples(got) == _as_tuples(WANT_30)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(after=st.integers(min_value=1, max_value=400), before=st.integers(min_value=31, max_value=400),
       ticker=st.sampled_from(["googl", "META", "Nvda", "AMD", "aapl", "TSM", "NEW"]),
       codes=st.lists(st.sampled_from(["2.02", "5.02", "4.02", "7.01", "8.01"]), min_size=1, max_size=5))
def test_c4_property_future_or_old_events_change_nothing(h, after, before, ticker, codes):
    # oracle: Requirement 4 by hand (WANT_30) plus the relation: events filed after as_of, or more than
    #   window_days (30) before it, add no hit.
    events = _events(h)
    item_codes = ",".join(codes)
    events.setdefault(ticker, [])
    events[ticker] = [*events[ticker], h.ev_age(-after, item_codes, "future"), h.ev_age(before, item_codes, "old")]
    got = scenario_triggers(events, h.AS_OF, 30, _themes(h))
    assert _as_tuples(got) == _as_tuples(WANT_30)
