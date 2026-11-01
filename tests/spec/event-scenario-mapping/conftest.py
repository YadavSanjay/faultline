"""Synthetic events and hand-calculation helpers for the event-scenario-mapping spec tests.

Everything is synthetic (docs/specs/event-scenario-mapping.md, Notes): made-up tickers, accessions and
dates around a fixed ``AS_OF``. The helpers below are the spec's closed forms written out in the test
suite (Requirements 2, 3, 5 and 7), used as oracles by the property tests; none of them calls the
code under test.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from types import SimpleNamespace
from typing import NamedTuple

import pytest
from src.scenariogen.news.mapping import EventRow, NewsMapConfig

AS_OF = date(2026, 6, 30)

# Production news-mapping values (spec Notes).
CFG = NewsMapConfig(beta_event=0.15, beta_tone=0.10, window_days=90, decay_halflife_days=30.0,
                    min_scale=0.8, max_scale=1.5)

# Production-like item weights (spec Notes); EventRow.weight is set to a decoy in every event so a
# result that used it instead of item_weights would be wrong.
WEIGHTS = {"2.02": 0.6, "5.02": 1.0, "7.01": 0.2, "8.01": 0.2, "4.02": 0.8}
DECOY_WEIGHT = 9.0


def ev(filing_date: str, item_codes: str, accession: str) -> EventRow:
    """A synthetic 8-K event; ``weight`` is a decoy (theme weights come from ``item_weights``)."""
    return EventRow(filing_date=filing_date, item_codes=item_codes, accession=accession, weight=DECOY_WEIGHT)


def ev_age(age: int, item_codes: str, accession: str) -> EventRow:
    """An event filed ``age`` days before ``AS_OF`` (negative = after it)."""
    return ev((AS_OF - timedelta(days=age)).isoformat(), item_codes, accession)


class ScenarioTheme(NamedTuple):
    """A theme with the spec's ``ScenarioTheme`` fields, in the spec's order (Interface).

    The stub ``src.scenariogen.events.ScenarioTheme`` has no fields until the implementer adds them, so
    the tests build themes with this structurally identical NamedTuple; the functions under test read
    a theme only through those fields.
    """

    news_items: frozenset[str]
    tone: bool
    crowding: bool
    trigger_items: frozenset[str]
    trigger_names: frozenset[str]


def theme(news_items: Sequence[str] = (), tone: bool = False, crowding: bool = False,
          trigger_items: Sequence[str] = (), trigger_names: Sequence[str] = ()) -> ScenarioTheme:
    return ScenarioTheme(frozenset(news_items), tone, crowding, frozenset(trigger_items), frozenset(trigger_names))


def codes_of(e: EventRow) -> list[str]:
    """Requirement 2: comma-separated codes, blanks dropped, each once (order of first appearance)."""
    out: list[str] = []
    for c in e.item_codes.split(","):
        if c and c not in out:
            out.append(c)
    return out


def age_of(e: EventRow, as_of: date = AS_OF) -> int | None:
    try:
        return (as_of - date.fromisoformat(e.filing_date[:10])).days
    except ValueError:
        return None


def hand_score(events: Sequence[EventRow], th: ScenarioTheme, weights: Mapping[str, float],
               cfg: NewsMapConfig = CFG, as_of: date = AS_OF) -> tuple[float, list[EventRow]]:
    """Requirement 2 closed form: sum of theme weights x 0.5**(age/halflife) over in-window events."""
    score = 0.0
    contributing: list[EventRow] = []
    for e in events:
        age = age_of(e, as_of)
        if age is None or age < 0 or age > cfg.window_days:
            continue
        items = [c for c in codes_of(e) if c in th.news_items]
        if not items:
            continue
        decay = 0.5 ** (age / cfg.decay_halflife_days) if cfg.decay_halflife_days > 0 else 1.0
        score += sum(weights[i] for i in items) * decay
        contributing.append(e)
    return score, contributing


def hand_mult(score: float, tone_z: float | None, th: ScenarioTheme, cfg: NewsMapConfig = CFG) -> float:
    """Requirement 3 closed form: clamp(1 + beta_event*score + t, min_scale, max_scale)."""
    t = cfg.beta_tone * (-tone_z) if th.tone and tone_z is not None else 0.0
    return max(cfg.min_scale, min(cfg.max_scale, 1.0 + cfg.beta_event * score + t))


def hand_equity(drivers: Sequence[str], legs: Sequence[str], news: Mapping[str, float],
                crowd: Mapping[str, float], th: ScenarioTheme, excluded: Sequence[str],
                lead: str | None) -> dict[str, float]:
    """Requirement 5 closed form."""
    has_news = bool(th.news_items) or th.tone
    out: dict[str, float] = {}
    for f in drivers:
        if not f.startswith("eq:") or f in legs or f == lead or f[3:].upper() in excluded:
            continue
        t = f[3:].upper()
        n = news.get(t, 1.0) if has_news else 1.0
        c = crowd.get(t, 1.0) if th.crowding else 1.0
        if n * c != pytest.approx(1.0, rel=0, abs=0):  # spec: drop compounds of *exactly* 1
            out[f] = n * c
    return out


def hand_reliable(dates: Sequence[str], as_of: date, min_obs: int, max_staleness_days: int) -> bool:
    """Requirement 7 closed form."""
    kept = [d for d in (date.fromisoformat(x[:10]) for x in dates) if d <= as_of]
    if not kept:
        return False
    return len(kept) >= min_obs and (as_of - max(kept)).days <= max_staleness_days


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        AS_OF=AS_OF, CFG=CFG, WEIGHTS=WEIGHTS, ev=ev, ev_age=ev_age, theme=theme, codes_of=codes_of,
        hand_score=hand_score, hand_mult=hand_mult, hand_equity=hand_equity,
        hand_reliable=hand_reliable,
    )
