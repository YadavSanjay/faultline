"""Hand-calculation helpers for the options-shock-sizing spec tests.

Everything is synthetic (docs/specs/options-shock-sizing.md, Notes). The helpers below are the spec's
closed forms (Requirements 1-5) written out in the test suite and used as oracles by the property
tests; none of them calls the code under test. ``Phi`` is ``math.erf`` based; ``Phi^-1`` comes from
``statistics.NormalDist``.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from statistics import NormalDist
from types import SimpleNamespace

import pytest

AS_OF = date(2026, 10, 6)

# Spec Notes grid: strikes 80..120, CDF 0.0, 0.02, 0.4, 0.9, 1.0, spot 100.
STRIKES = [80.0, 90.0, 100.0, 110.0, 120.0]
CDF = [0.0, 0.02, 0.4, 0.9, 1.0]
SPOT = 100.0


def hand_select(dates: Sequence[str], as_of: date, max_staleness_days: int) -> str | None:
    """Requirement 1: latest date d (first 10 chars) with 0 <= (as_of - d).days <= max_staleness_days."""
    kept = [d[:10] for d in dates
            if 0 <= (as_of - date.fromisoformat(d[:10])).days <= max_staleness_days]
    return max(kept) if kept else None


def hand_quantile(strikes: Sequence[float], cdf: Sequence[float], spot: float, q: float) -> float | None:
    """Requirement 2: linear interpolation of the strike where the CDF equals q; first strike on ties."""
    if q < cdf[0] or q > cdf[-1]:
        return None
    i = next(j for j, c in enumerate(cdf) if c >= q)
    if cdf[i] == q:  # exact hit (incl. the first point of a flat segment)
        k = strikes[i]
    else:
        k = strikes[i - 1] + (strikes[i] - strikes[i - 1]) * (q - cdf[i - 1]) / (cdf[i] - cdf[i - 1])
    return k / spot - 1.0


def phi(x: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def hand_lognormal_quantile(q: float, vol: float, t: float) -> float:
    """Requirement 3: exp(-vol^2 t / 2 + vol sqrt(t) PhiInv(q)) - 1."""
    return math.exp(-vol * vol * t / 2.0 + vol * math.sqrt(t) * NormalDist().inv_cdf(q)) - 1.0


def hand_tail_prob(move: float, vol: float, t: float) -> float:
    """Requirement 3: P(R <= move) for move < 0, P(R >= move) for move > 0, ln(1+R) ~ N(-v^2t/2, v^2t)."""
    z = (math.log1p(move) + vol * vol * t / 2.0) / (vol * math.sqrt(t))
    return phi(z) if move < 0 else 1.0 - phi(z)


def hand_tiers(mild: float, adverse: float, severe_factor: float, bound: float | None) -> dict[str, float]:
    """Requirement 4: severe = adverse * severe_factor, magnitude capped at bound, sign kept."""
    s = adverse * severe_factor
    if bound is not None and abs(s) > bound:
        s = math.copysign(bound, s)
    return {"mild": mild, "adverse": adverse, "severe": s}


def hand_exceeds(tier: float, option: float | None) -> bool:
    """Requirement 5: both non-zero, same sign, |option| > |tier|."""
    if option is None or tier == 0 or option == 0:
        return False
    return (tier > 0) == (option > 0) and abs(option) > abs(tier)


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        AS_OF=AS_OF, STRIKES=STRIKES, CDF=CDF, SPOT=SPOT, hand_select=hand_select,
        hand_quantile=hand_quantile, phi=phi, hand_lognormal_quantile=hand_lognormal_quantile,
        hand_tail_prob=hand_tail_prob, hand_tiers=hand_tiers, hand_exceeds=hand_exceeds,
    )
