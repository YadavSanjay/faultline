"""VaR backtesting (item 7) — rolling-window exception counting + coverage tests + traffic light.

Walks a historical-simulation P&L series: for each test day, estimates VaR from the prior
`window` days and records an *exception* when the realized loss exceeds it. Reports the
exception count vs the expected `(1-level)·N`, the Basel traffic-light zone (from the binomial
CDF of the count, so it generalizes beyond the 250-day/99% reference), the Kupiec POF
unconditional-coverage LR, and the Christoffersen independence + conditional-coverage LRs
(Tier 2.6) so exception *clustering* — not just the count — is tested. The same core runs on
both the sensitivity-VaR P&L and the full-revaluation P&L vector. A red zone or a rejected
conditional-coverage test says the VaR model must be reviewed.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np

from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from .var import VarEngine

logger = logging.getLogger("pricer.risk.backtest")


@dataclass
class BacktestResult:
    level: float
    window: int
    n_days: int                 # test days actually evaluated
    n_exceptions: int
    expected_exceptions: float
    exception_rate: float
    zone: str                   # green | amber | red
    kupiec_lr: float            # unconditional coverage (POF), ~chi2_1
    christoffersen_lr: float = 0.0   # independence of exceptions, ~chi2_1
    cc_lr: float = 0.0               # conditional coverage = Kupiec + Christoffersen, ~chi2_2
    method: str = "rolling_historical_simulation"
    # per-test-day timeline for the UI: {date, pnl, var (loss threshold), exception}. The
    # dated exception series so clustering is visible, not just the count.
    timeline: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method, "level": self.level, "window": self.window,
            "n_days": self.n_days, "n_exceptions": self.n_exceptions,
            "expected_exceptions": self.expected_exceptions,
            "exception_rate": self.exception_rate, "zone": self.zone,
            "kupiec_lr": self.kupiec_lr,
            "christoffersen_lr": self.christoffersen_lr,
            "cc_lr": self.cc_lr,
            "timeline": self.timeline,
        }


def _binom_cdf(x: int, n: int, p: float) -> float:
    return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(x + 1))


def _basel_zone(x: int, n: int, p: float) -> str:
    """Basel traffic light from the cumulative probability of <= x exceptions."""
    cdf = _binom_cdf(x, n, p)
    if cdf < 0.95:
        return "green"
    if cdf < 0.9999:
        return "amber"
    return "red"


def _kupiec_lr(n: int, x: int, p: float) -> float:
    """Kupiec proportion-of-failures likelihood-ratio statistic (~chi2_1 under H0)."""
    if n == 0:
        return 0.0
    pi = x / n
    # log-likelihood under H0 (rate p) vs unrestricted (rate pi); guard the log(0) corners.
    ll0 = (n - x) * math.log(1 - p) + (x * math.log(p) if x else 0.0)
    ll1 = ((n - x) * math.log(1 - pi) if x < n else 0.0) + (x * math.log(pi) if x else 0.0)
    return -2.0 * (ll0 - ll1)


def _christoffersen_ind_lr(ind: list[int]) -> float:
    """Christoffersen independence LR (~chi2_1): tests whether an exception on day t depends on
    day t-1 (clustering). Compares a first-order Markov exception chain against an i.i.d. one."""
    n00 = n01 = n10 = n11 = 0
    for a, b in pairwise(ind):
        if a == 0 and b == 0:
            n00 += 1
        elif a == 0 and b == 1:
            n01 += 1
        elif a == 1 and b == 0:
            n10 += 1
        else:
            n11 += 1
    n0, n1 = n00 + n01, n10 + n11
    pi01 = n01 / n0 if n0 else 0.0
    pi11 = n11 / n1 if n1 else 0.0
    pi = (n01 + n11) / (n0 + n1) if (n0 + n1) else 0.0
    if pi <= 0.0 or pi >= 1.0:
        return 0.0        # no (or all) exceptions -> independence undefined, treat as pass
    ll0 = (n00 + n10) * math.log(1 - pi) + (n01 + n11) * math.log(pi)

    def _leg(n_stay: int, n_move: int, p_move: float) -> float:
        v = 0.0
        if n_stay > 0 and p_move < 1.0:
            v += n_stay * math.log(1 - p_move)
        if n_move > 0 and p_move > 0.0:
            v += n_move * math.log(p_move)
        return v

    ll1 = _leg(n00, n01, pi01) + _leg(n10, n11, pi11)
    return max(0.0, -2.0 * (ll0 - ll1))


def backtest_pnl(vals: np.ndarray, level: float, window: int, test_days: int,
                 method: str = "rolling_historical_simulation",
                 dates: list[str] | None = None) -> BacktestResult | None:
    """Rolling-window exception backtest over a P&L vector (sensitivity or full-reval P&L).

    When ``dates`` (one ISO date per P&L entry) is supplied, a per-test-day ``timeline`` is
    recorded — realized P&L, the rolling VaR loss threshold, and the exception flag — so the
    UI can plot the dated exception series (clustering), not just the aggregate count.
    """
    p = 1.0 - level
    n_total = int(vals.size)
    if n_total <= window + 1:
        logger.warning("VaR backtest: not enough history (%d days <= window %d).", n_total, window)
        return None
    test = min(test_days, n_total - window)
    ind: list[int] = []
    timeline: list[dict[str, Any]] = []
    for t in range(n_total - test, n_total):
        var_q = float(np.quantile(-vals[t - window:t], level))
        pnl_t = float(vals[t])
        is_exc = 1 if -pnl_t > var_q else 0
        ind.append(is_exc)
        timeline.append({
            "date": dates[t] if dates is not None and t < len(dates) else str(t),
            "pnl": pnl_t,
            "var": var_q,           # loss threshold (positive $); an exception is loss > var
            "exception": bool(is_exc),
        })
    exceptions = int(sum(ind))
    zone = _basel_zone(exceptions, test, p)
    kupiec = _kupiec_lr(test, exceptions, p)
    christ = _christoffersen_ind_lr(ind)
    result = BacktestResult(
        level=level, window=window, n_days=test, n_exceptions=exceptions,
        expected_exceptions=p * test, exception_rate=exceptions / test,
        zone=zone, kupiec_lr=kupiec, christoffersen_lr=christ, cc_lr=kupiec + christ,
        method=method, timeline=timeline,
    )
    logger.info("VaR backtest [%s] @%.3f: %d/%d exceptions (expected %.1f) -> %s zone, "
                "Kupiec=%.2f Christoffersen=%.2f CC=%.2f", method, level, exceptions, test,
                p * test, zone, kupiec, christ, kupiec + christ)
    return result


class VarBacktester:
    def __init__(self, var_engine: VarEngine, config: dict[str, Any]):
        self.ve = var_engine
        cfg = config.get("risk", {}).get("backtest", {})
        self.level = float(cfg.get("level", 0.99))
        self.window = int(cfg.get("window", 250))
        self.test_days = int(cfg.get("test_days", 250))

    def run(self, trades: list[Trade], ctx: ValuationContext) -> BacktestResult | None:
        pnl = self.ve.pnl_series(trades, ctx, lookback=self.window + self.test_days + 5)
        dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in pnl.index]
        return backtest_pnl(pnl.to_numpy(dtype=float), self.level, self.window, self.test_days,
                            dates=dates)
