"""Synthetic books and independent checks for the sizing-leverage-cap spec tests.

Every book is built in the test from a seeded numpy P&L matrix (docs/specs/sizing-leverage-cap.md,
Notes): no `data/`, no `TradeRiskModel.build`. Each trade's `standalone_vol` is the ddof=1 sample std
of its P&L column, as `build` sets it. All trades are directional (no hedges), `pairs` is never
passed, and the per-trade notional cap is set far above any size the optimizer can reach, so the
out-of-scope interactions named in the spec cannot decide an outcome.

The checks recompute what the criteria need from `SizingResult.sizes` and the P&L matrix the test
built, not from the optimizer's own summary fields:

* gross leverage = sum |target_notional| / capital_base (spec C1);
* realized risk share of trade j = m_j (Sigma m)_j / (m^T Sigma m), with m_j = target / current
  notional and Sigma the ddof=1 sample covariance of the P&L matrix (spec Notes: Euler share of
  portfolio volatility).

Helpers are exposed through the session-scoped ``sz`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest
from src.pricer.sizing.expected_returns import ExpectedReturn
from src.pricer.sizing.optimizer import SizingResult, solve_profile
from src.pricer.sizing.profiles import RiskProfile
from src.pricer.sizing.trade_risk import TradeRisk, TradeRiskModel

N_DAYS = 250
TRADING_DAYS = 252                # annualisation in the profile's Sharpe definition
SHARE_TOL = 1e-4                  # the optimizer's documented risk-share trim tolerance (spec C1)
SLACK_NOTIONAL_CAP_PCT = 1000.0   # $1tn per trade on a $1bn base: never reached in these books


@dataclass
class Book:
    """A synthetic book: the model handed to the optimizer plus the raw inputs the checks use."""

    model: TradeRiskModel
    pnl: np.ndarray                 # (N_DAYS, n) P&L at current size
    notionals: np.ndarray           # current notional per trade
    ids: list[str]


def build_book(seed: int, daily_vols: Sequence[float], notionals: Sequence[float],
               orthogonal: bool = False) -> Book:
    """Directional book, column j = i.i.d. N(0, 1) * daily_vols[j] (rng seeded with `seed`).

    With `orthogonal=True` the normal draws are demeaned and orthonormalised (QR) and rescaled so each
    column's ddof=1 sample std is exactly daily_vols[j] and every sample correlation is zero: the
    sample covariance is then diagonal, so no chance correlation decides how risk spreads across names.
    """
    n = len(daily_vols)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((N_DAYS, n))
    if orthogonal:
        q, _ = np.linalg.qr(z - z.mean(axis=0))
        z = q * np.sqrt(N_DAYS - 1)
    pnl = z * np.asarray(daily_vols, dtype=float)
    ids = [f"T{j}" for j in range(n)]
    trades = [
        TradeRisk(trade_id=ids[j], asset_class="Equity", description=f"synthetic {ids[j]}", pv=0.0,
                  notional=float(notionals[j]), sensitivities={}, is_hedge=False,
                  standalone_vol=float(pnl[:, j].std(ddof=1)),
                  standalone_var99=float(np.quantile(-pnl[:, j], 0.99)))
        for j in range(n)
    ]
    dates = [f"d{i:03d}" for i in range(N_DAYS)]
    model = TradeRiskModel(trades, pnl, [f"F{j}" for j in range(n)], dates)
    return Book(model=model, pnl=pnl, notionals=np.asarray(notionals, dtype=float), ids=ids)


def make_mu(ids: Sequence[str], mu_annual: Sequence[float]) -> dict[str, ExpectedReturn]:
    return {t: ExpectedReturn(trade_id=t, mu_annual=float(m), method="synthetic")
            for t, m in zip(ids, mu_annual, strict=True)}


def make_profile(var_budget_pct: float, gross_leverage_cap: float, risk_share_cap: float,
                 notional_cap_pct: float = SLACK_NOTIONAL_CAP_PCT) -> RiskProfile:
    """Return-tilt profile as in the spec recipe; only the budget and the caps vary."""
    return RiskProfile(key="spec", label="Spec", var_budget_pct=var_budget_pct,
                       gross_leverage_cap=gross_leverage_cap, concentration_cap=1.0,
                       risk_share_cap=risk_share_cap, notional_cap_pct=notional_cap_pct, hedge_scale=1.0,
                       method="return_tilt", tilt_power=2.0, sharpe_floor=0.01)


def solve(book: Book, mu: dict[str, ExpectedReturn], profile: RiskProfile, capital_base: float) -> SizingResult:
    return solve_profile(book.model, mu, profile, capital_base, pairs=None)


def targets(book: Book, result: SizingResult) -> np.ndarray:
    """Target notionals in book order, read from `result.sizes` by trade id (each id exactly once)."""
    assert isinstance(result, SizingResult), f"expected a SizingResult, got {type(result).__name__}"
    ids = [s.trade_id for s in result.sizes]
    assert sorted(ids) == sorted(book.ids), f"sizes cover {sorted(ids)}, book has {book.ids}"
    by_id = {s.trade_id: s for s in result.sizes}
    out = np.array([float(by_id[t].target_notional) for t in book.ids])
    assert np.all(np.isfinite(out)), f"non-finite target notional: {out}"
    return out


def gross_leverage(book: Book, result: SizingResult, capital_base: float) -> float:
    """Spec C1: sum |target_notional| / capital_base, recomputed from the sizes."""
    return float(np.abs(targets(book, result)).sum() / capital_base)


def realized_shares(book: Book, result: SizingResult) -> np.ndarray:
    """Euler share of portfolio vol per trade, recomputed from target notionals and the P&L matrix."""
    m = targets(book, result) / book.notionals
    cov = np.cov(book.pnl, rowvar=False, ddof=1)
    var = float(m @ cov @ m)
    assert var > 0.0, "the returned book carries no risk (every target notional is zero)"
    return np.asarray(m * (cov @ m) / var, dtype=float)


def reported_shares(book: Book, result: SizingResult) -> np.ndarray:
    by_id = {s.trade_id: s for s in result.sizes}
    return np.array([float(by_id[t].risk_share) for t in book.ids])


def var_upper_bound(book: Book, gross_notional: float) -> float:
    """Upper bound on the 1-day loss of ANY book whose gross notional is at most `gross_notional`.

    Each day's loss is sum_j m_j (-P_tj) <= sum_j |m_j N_j| |P_tj| / |N_j| <= gross * max_tj |P_tj| / |N_j|,
    so every loss quantile, the 99% VaR included, is at most this. When it is below the VaR budget, the
    leverage cap (not the VaR budget) sets the scale of any book that respects the cap.
    """
    per_unit = np.abs(book.pnl) / np.abs(book.notionals)
    return float(gross_notional * per_unit.max())


def intended_top_share(book: Book, mu_annual: Sequence[float], profile: RiskProfile) -> float:
    """Largest intended return-tilt budget share: b_j = max(Sharpe_j, 0)^p + floor, normalised.

    Sharpe_j = mu_j / (standalone_vol_j * sqrt(252)) (profiles.py and the design doc's return_tilt
    budget). With uncorrelated columns this is close to the realized share before any trim, so a value
    far above `risk_share_cap` means the risk-share cap must bind.
    """
    vol = book.pnl.std(axis=0, ddof=1) * np.sqrt(TRADING_DAYS)
    sharpe = np.clip(np.asarray(mu_annual, dtype=float) / vol, 0.0, None)
    b = sharpe ** profile.tilt_power + profile.sharpe_floor
    return float((b / b.sum()).max())


# The spec's reproduction recipe (docs/specs/sizing-leverage-cap.md, Notes).
RECIPE = SimpleNamespace(seed=0, n=4, daily_vol=1e6, notional=50e6, capital_base=1e9,
                         mu=(50e6, 1e6, 1e6, 1e6), var_budget_pct=0.05, gross_leverage_cap=0.5,
                         risk_share_cap=0.30, notional_cap_pct=10.0)


@pytest.fixture(scope="session")
def sz() -> SimpleNamespace:
    """Book builders, the spec recipe and the independent checks."""
    return SimpleNamespace(build_book=build_book, make_mu=make_mu, make_profile=make_profile, solve=solve,
                           targets=targets, gross_leverage=gross_leverage, realized_shares=realized_shares,
                           reported_shares=reported_shares, var_upper_bound=var_upper_bound,
                           intended_top_share=intended_top_share, RECIPE=RECIPE, SHARE_TOL=SHARE_TOL,
                           SLACK_NOTIONAL_CAP_PCT=SLACK_NOTIONAL_CAP_PCT)
