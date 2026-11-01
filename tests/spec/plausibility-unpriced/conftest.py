"""Synthetic inputs for the plausibility-unpriced spec tests.

Everything is synthetic (docs/specs/plausibility-unpriced.md, Notes):

* ``FakeMarket`` stands in for the option-signal provider, an I/O boundary (the real one reads option
  chains from disk). It answers ``implied_prob(ticker, move)`` from a fixed ``{ticker: prob}`` table and
  records every call.
* ``covariance`` is a one-factor ``FactorCovariance`` (``eq:NVDA``, daily variance ``1e-4``).
* ``checker`` builds a ``PlausibilityChecker`` with the envelope check set to ``"ignore"`` for every
  tier, ``market_implied.min_prob = 0.01`` and, by default, no bounds, so only the market check can
  record a finding.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from src.scenariogen.covariance import FactorCovariance
from src.scenariogen.factor_vector import MaterializedScenario
from src.scenariogen.plausibility import PlausibilityChecker

MIN_PROB = 0.01


class FakeMarket:
    """Option-signal provider fake: ``implied_prob`` returns a fixed probability per ticker."""

    def __init__(self, probs: dict[str, float | None]):
        self.probs = dict(probs)
        self.calls: list[tuple[str, float]] = []

    def implied_prob(self, ticker: str, move: float) -> float | None:
        self.calls.append((ticker, move))
        return self.probs.get(ticker)


def covariance() -> FactorCovariance:
    return FactorCovariance(
        factors=("eq:NVDA",),
        mean=np.zeros(1),
        cov=np.array([[1e-4]]),
        n_obs=250,
        window="full",
        cov_id="synthetic-plausibility-unpriced",
    )


def config(bounds: dict[str, Any] | None = None, enabled: bool = True) -> dict[str, Any]:
    return {
        "bounds": dict(bounds or {}),
        "envelope": {"n_sigma": 6.0, "severe_action": "ignore", "non_severe_action": "ignore"},
        "market_implied": {"enabled": enabled, "min_prob": MIN_PROB},
    }


def checker(market: Any | None, bounds: dict[str, Any] | None = None,
            enabled: bool = True) -> PlausibilityChecker:
    return PlausibilityChecker(covariance(), config(bounds, enabled), market=market)


def scenario(vector: dict[str, float], severity: str = "mild") -> MaterializedScenario:
    return MaterializedScenario(name="synthetic", vector=dict(vector), severity=severity,
                                provenance={"horizon_days": 10})


def rows(result: Any) -> list[tuple[str, str, float, float, str]]:
    """The findings as ``(factor, check, observed, limit, severity)`` tuples, in recorded order."""
    return [(f.factor, f.check, f.observed, f.limit, f.severity) for f in result.findings]


def market_rows(result: Any, factor: str) -> list[tuple[str, str, float, float, str]]:
    """The ``market_*`` findings for one factor."""
    return [r for r in rows(result) if r[0] == factor and r[1].startswith("market_")]


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        MIN_PROB=MIN_PROB,
        FakeMarket=FakeMarket,
        covariance=covariance,
        config=config,
        checker=checker,
        scenario=scenario,
        rows=rows,
        market_rows=market_rows,
    )
