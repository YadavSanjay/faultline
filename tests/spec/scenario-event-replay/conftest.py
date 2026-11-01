"""Synthetic configs, scores and vectors for the scenario-event-replay spec tests.

Everything is synthetic (docs/specs/scenario-event-replay.md, Notes). The expected values are
Requirements 1-5 written out by hand; nothing here calls the code under test.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

import calendar
import copy
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest


class Episode(NamedTuple):
    """The spec's ``Episode`` fields, in the spec's order (Interface).

    The stub ``src.scenariogen.event_replay.Episode`` has no fields until the implementer adds them,
    so expected episodes are built with this structurally identical NamedTuple and compared by tuple.
    """

    name: str
    as_of: date
    horizon_days: int


# A valid document in the shape of Requirement 6's config (file order is deliberately not sorted).
_RAW: dict[str, Any] = {
    "description": "synthetic event replay config",
    "_comment": "ignored",
    "episodes": [
        {"name": "yen_carry", "as_of": "2024-07-10", "horizon_days": 18},
        {"name": "deepseek", "as_of": "2025-01-24", "horizon_days": 1},
        {"name": "tariff", "as_of": "2025-04-02", "horizon_days": 4},
    ],
    "min_abs_z": 1.5,
    "sleeves": {"eq:NVDA": "semis", "eq:MSFT": "platforms", "ir:USD_SOFR_10Y": "rates",
                "credit:HY": "credit"},
}

# Hand-listed from Requirement 1 for _RAW.
WANT_EPISODES = [
    Episode("yen_carry", date(2024, 7, 10), 18),
    Episode("deepseek", date(2025, 1, 24), 1),
    Episode("tariff", date(2025, 4, 2), 4),
]
WANT_SETTINGS = (1.5, {"eq:NVDA": "semis", "eq:MSFT": "platforms", "ir:USD_SOFR_10Y": "rates",
                       "credit:HY": "credit"})


def raw() -> dict[str, Any]:
    """A fresh deep copy of the valid document (tests mutate it into invalid ones)."""
    return copy.deepcopy(_RAW)


def minimal() -> dict[str, Any]:
    """A valid document with only the required ``episodes`` (optional keys absent)."""
    return {"episodes": [{"name": "deepseek", "as_of": "2025-01-24", "horizon_days": 1}]}


def score(cosine: float | None) -> dict[str, Any]:
    """A ``score_prediction``-shaped result dict carrying the given cosine."""
    return {"n_factors": 5, "cosine": cosine, "rank_corr_equity": None, "direction_hit_rate": 0.5,
            "n_direction": 2}


def fye_end(year: int, month: int, day: int) -> date:
    """Fiscal year-end in ``year``: (year, month, day) with the day clamped to the month's last day."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def filed(year: int, month: int, day: int, as_of: date, lag: int) -> bool:
    """Requirement 3's filed rule: end + lag <= as_of."""
    return fye_end(year, month, day) + timedelta(days=lag) <= as_of


# C5 synthetic case (min_abs_z 1.0). Per factor: (predicted, realised, scale, sleeve); a missing
# entry is marked by ``None`` and left out of that mapping.
C5_ROWS: dict[str, tuple[float | None, float | None, float | None, str | None]] = {
    "eq:NVDA": (-0.10, -0.20, 0.05, "semis"),           # z -4: same sign -> hit
    "eq:AMD": (0.05, -0.12, 0.04, "semis"),             # z -3: opposite sign -> miss
    "eq:INTC": (0.0, -0.06, 0.03, "semis"),             # z -2: zero prediction -> miss
    "eq:MU": (-0.02, -0.01, 0.04, "semis"),             # z -0.25 < 1: excluded
    "eq:AVGO": (None, -0.10, 0.05, "semis"),            # not predicted: excluded
    "eq:MSFT": (-0.03, -0.05, 0.025, "platforms"),      # z -2: hit
    "eq:GOOGL": (-0.04, -0.03, 0.0, "platforms"),       # scale 0: excluded
    "eq:ORCL": (-0.04, -0.06, -0.05, "platforms"),      # scale < 0: excluded
    "eq:META": (-0.04, -0.06, None, "platforms"),       # no scale: excluded
    "ir:USD_SOFR_10Y": (-0.004, -0.003, 0.003, "rates"),  # z exactly -1 (>= 1 inclusive): hit
    "ir:USD_SOFR_2Y": (0.002, 0.005, 0.0025, "rates"),  # z 2: hit
    "credit:HY": (0.01, -0.02, 0.01, "credit"),         # z -2: miss
    "eq:SPX": (-0.05, -0.004, 0.02, "index"),           # z -0.2 < 1: excluded -> index absent
    "fx:USDJPY": (-1.0, None, 2.0, "fx"),               # no realised: excluded -> fx absent
    "vol:VIX": (5.0, 8.0, 4.0, None),                   # no sleeve: excluded
}


def c5_inputs() -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, str]]:
    """``(predicted, realised, scale, sleeves)`` built from ``C5_ROWS``."""
    predicted: dict[str, float] = {}
    realised: dict[str, float] = {}
    scale: dict[str, float] = {}
    sleeves: dict[str, str] = {}
    for key, (p, r, s, sl) in C5_ROWS.items():
        if p is not None:
            predicted[key] = p
        if r is not None:
            realised[key] = r
        if s is not None:
            scale[key] = s
        if sl is not None:
            sleeves[key] = sl
    return predicted, realised, scale, sleeves


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(Episode=Episode, raw=raw, minimal=minimal, WANT_EPISODES=WANT_EPISODES,
                           WANT_SETTINGS=WANT_SETTINGS, score=score, fye_end=fye_end, filed=filed,
                           c5_inputs=c5_inputs)
