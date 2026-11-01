"""Full-revaluation historical-simulation VaR / ES (P2).

The gold-standard market-risk measure: replay each historical day's *actual* factor
co-moves onto today's positions and **fully reprice the book** (no Greek approximation),
giving an empirical P&L distribution that captures gamma, vega convexity, and large-move
non-linearity exactly. VaR is the loss quantile; ES is the average loss beyond it.

This is the heavy measure (n_days x n_trades revaluations). The run-scoped curve cache
makes it tractable — each historical day's shocked curve is bootstrapped once and reused
across every trade that needs it that day.
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..engine import PricingEngine
from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from ..ql.common.curve_cache import curve_cache
from ..ql.factory import get_pricer
from . import bumps
from .factor_history import FactorHistory

logger = logging.getLogger("pricer.risk.hvar")


def _even_chunks(items: list[str], n: int) -> list[list[str]]:
    """Split `items` into <= n near-equal contiguous chunks (drops empties)."""
    n = max(1, min(n, len(items)))
    k, m = divmod(len(items), n)
    out = [items[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]
    return [c for c in out if c]


# --- parallel workers (full-reval VaR fans out over trade chunks) ----------- #
# Each worker builds its own engine + day-shock transforms once, then reprices its chunk
# of trades across all historical days under a process-local curve cache. State lives in a
# module global (the picklable-free way to share it across ProcessPoolExecutor tasks).
_HVAR_WORKER: dict[str, Any] = {}


def _hvar_worker_init(config: dict, root: Path, ctx: ValuationContext, lookback: int) -> None:
    history = FactorHistory(root, config)
    shocks = history.day_shocks(lookback)
    _HVAR_WORKER.update(
        engine=PricingEngine(config, root),
        ctx=ctx,
        day_tfs=[s.transforms() for s in shocks],
        n=len(shocks),
    )


def _hvar_worker_chunk(trade_ids: list[str]) -> tuple[list[float], float, int]:
    from ..portfolio.portfolio_loader import load_portfolio

    w = _HVAR_WORKER
    engine, ctx, day_tfs, n = w["engine"], w["ctx"], w["day_tfs"], w["n"]
    by_id = {t.trade_id: t for t in load_portfolio()}
    trades = [by_id[tid] for tid in trade_ids if tid in by_id]
    return _price_trades(engine, ctx, trades, day_tfs, n, base_extra=[])


def _hvar_worker_rows(trade_ids: list[str]) -> dict[str, list[float]]:
    from ..portfolio.portfolio_loader import load_portfolio

    w = _HVAR_WORKER
    by_id = {t.trade_id: t for t in load_portfolio()}
    trades = [by_id[tid] for tid in trade_ids if tid in by_id]
    rows, _ = _trade_pnl_rows(w["engine"], w["ctx"], trades, w["day_tfs"], w["n"], base_extra=[])
    return {tid: row.tolist() for tid, row in rows.items()}


@dataclass
class TradePnlHistory:
    """Per-trade daily full-revaluation P&L: ``matrix[d, j]`` is trade ``trade_ids[j]`` on ``dates[d]``.

    Scaled to the VaR horizon like ``FullRevalVarResult.pnl_vector``, so the row sums are the book's
    historical P&L vector. Trades that won't base-price are left out.
    """

    dates: list[str]
    trade_ids: list[str]
    matrix: np.ndarray
    lookback_days: int
    horizon_days: int


@dataclass
class FullRevalVarResult:
    valuation_date: str
    lookback_days: int
    horizon_days: int
    n_scenarios: int
    base_pv: float
    var: dict[str, float] = field(default_factory=dict)
    es: dict[str, float] = field(default_factory=dict)
    worst_day: str = ""
    worst_pnl: float = 0.0
    pnl_vector: list[float] = field(default_factory=list)   # per-day full-reval P&L (backtest input)
    dates: list[str] = field(default_factory=list)          # ISO date per pnl_vector entry (backtest timeline)
    # factor -> zero moves carried across short gaps in the replayed window (factor_history.carry_short_gaps)
    panel_carried_cells: dict[str, int] = field(default_factory=dict)
    method: str = "full_revaluation_historical_simulation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "valuation_date": self.valuation_date,
            "lookback_days": self.lookback_days,
            "horizon_days": self.horizon_days,
            "n_scenarios": self.n_scenarios,
            "base_pv": self.base_pv,
            "var": self.var,
            "es": self.es,
            "worst_day": self.worst_day,
            "worst_pnl": self.worst_pnl,
            "pnl_vector": self.pnl_vector,
            "dates": self.dates,
            "panel_carried_cells": self.panel_carried_cells,
        }


def _trade_pnl_rows(engine: PricingEngine, ctx: ValuationContext, trades: list[Trade],
                    day_tfs: list[list[bumps.Transform]], n: int,
                    base_extra: list[bumps.Transform]) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    """Reprice `trades` across all day-shocks; return each priced trade's per-day pnl and base PV.

    A trade that won't base-price is left out; a single trade/day that won't reprice contributes 0.
    """
    rows: dict[str, np.ndarray] = {}
    base_pvs: dict[str, float] = {}
    with curve_cache():
        for trade in trades:
            base_mds = engine.loader.resolve(trade.market_data_requirements(ctx), ctx)
            if base_extra:
                base_mds = bumps.bumped(base_mds, *base_extra)
            pricer = get_pricer(trade)
            try:
                base_pv = float(pricer.price(trade, base_mds, ctx).pv)
            except Exception:  # a trade that won't even base-price is skipped
                continue
            row = np.zeros(n)
            for i, tfs in enumerate(day_tfs):
                try:
                    pv = float(pricer.price(trade, bumps.bumped(base_mds, *tfs), ctx).pv)
                    row[i] = pv - base_pv
                except Exception:  # a single trade/day that won't reprice -> 0 contribution
                    continue
            rows[trade.trade_id] = row
            base_pvs[trade.trade_id] = base_pv
    return rows, base_pvs


def _price_trades(engine: PricingEngine, ctx: ValuationContext, trades: list[Trade],
                  day_tfs: list[list[bumps.Transform]], n: int,
                  base_extra: list[bumps.Transform]) -> tuple[list[float], float, int]:
    """Reprice `trades` across all day-shocks; return (per-day pnl, base_total, n_priced).

    The per-day pnl vector (length `n`) is summed across the trades handled here, so a
    parallel worker's partial result merges by element-wise addition.
    """
    rows, base_pvs = _trade_pnl_rows(engine, ctx, trades, day_tfs, n, base_extra)
    pnl = np.zeros(n)
    for row in rows.values():                  # trade order, as the per-day accumulation was
        pnl += row
    return pnl.tolist(), float(sum(base_pvs.values())), len(rows)


class FullRevalVarEngine:
    def __init__(self, engine: PricingEngine, config: dict[str, Any], project_root: Path):
        self.engine = engine
        self.cfg = config.get("risk", {}).get("hist_var", {})
        self.levels = [float(x) for x in self.cfg.get("confidence_levels", [0.95, 0.975, 0.99])]
        self.history = FactorHistory(project_root, config)

    def historical_var(self, trades: list[Trade], ctx: ValuationContext,
                       lookback: int | None = None,
                       base_transforms: list[bumps.Transform] | None = None,
                       workers: int = 1,
                       ) -> FullRevalVarResult:
        """Full-reval historical VaR/ES.

        ``base_transforms`` (optional) are applied to each trade's base market data
        *before* the historical day-shocks, so the P&L distribution is measured around a
        **shocked** state rather than today's. This yields "ES under the shock" — the
        forward-looking capital measure for a stress scenario (design §8.4).

        ``workers`` > 1 fans the (n_days x n_trades) repricing out over processes, split by
        trade chunk (each worker keeps its own curve cache). The parallel path is used only
        when there is no base-state shock (its transforms are closures not worth pickling).
        """
        lookback = lookback or int(self.cfg.get("lookback_days", 250))
        horizon = int(self.cfg.get("horizon_days", 1))
        shocks = self.history.day_shocks(lookback)
        day_tfs = [s.transforms() for s in shocks]          # built once, reused across trades
        dates = [s.date for s in shocks]
        base_extra = list(base_transforms or [])

        result = FullRevalVarResult(ctx.valuation_date.isoformat(), lookback, horizon,
                                    len(shocks), 0.0,
                                    panel_carried_cells=dict(self.history.panel_carried_cells))
        if base_extra:
            result.method += "@shocked"
        if not shocks:
            logger.warning("Full-reval VaR: no aligned factor history available.")
            return result

        parallel = workers > 1 and not base_extra and len(trades) > 1
        if parallel:
            pnl_list, base_total, n_priced = self._price_parallel(trades, ctx, lookback,
                                                                  len(shocks), workers)
        else:
            pnl_list, base_total, n_priced = _price_trades(self.engine, ctx, trades, day_tfs,
                                                           len(shocks), base_extra)
        pnl = np.asarray(pnl_list, dtype=float)

        result.base_pv = base_total
        logger.info("Full-reval VaR: %d trade(s) x %d day(s) repriced (%s)", n_priced,
                    len(shocks), f"{workers} workers" if parallel else "sequential")

        pnl = pnl * np.sqrt(horizon)
        result.pnl_vector = [float(x) for x in pnl]   # per-day P&L for the full-reval backtest
        result.dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in dates]
        loss = -pnl
        worst = int(np.argmax(loss))
        result.worst_day, result.worst_pnl = dates[worst], float(pnl[worst])
        for lvl in self.levels:
            var_q = float(np.quantile(loss, lvl))
            tail = loss[loss >= var_q]
            result.var[f"{lvl}"] = var_q
            result.es[f"{lvl}"] = float(tail.mean()) if tail.size else var_q
        return result

    def trade_pnl_history(self, trades: list[Trade], ctx: ValuationContext,
                          lookback: int | None = None, workers: int = 1) -> TradePnlHistory:
        """Each trade's daily full-reval P&L over the lookback (columns sum to the book's ``pnl_vector``).

        Same day-shocks, repricing and horizon scaling as ``historical_var``, kept per trade instead
        of summed. ``workers`` > 1 reprices trade chunks in parallel processes (default book).
        """
        lookback = lookback or int(self.cfg.get("lookback_days", 250))
        horizon = int(self.cfg.get("horizon_days", 1))
        shocks = self.history.day_shocks(lookback)
        dates = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in (s.date for s in shocks)]
        n = len(shocks)
        rows: dict[str, list[float]] = {}
        if n and workers > 1 and len(trades) > 1:
            chunks = _even_chunks(sorted(t.trade_id for t in trades), workers)
            with ProcessPoolExecutor(
                max_workers=workers, initializer=_hvar_worker_init,
                initargs=(self.engine.config, self.engine.root, ctx, lookback),
            ) as ex:
                for part in ex.map(_hvar_worker_rows, chunks):
                    rows.update(part)
        elif n:
            day_tfs = [s.transforms() for s in shocks]
            priced, _ = _trade_pnl_rows(self.engine, ctx, trades, day_tfs, n, base_extra=[])
            rows = {tid: row.tolist() for tid, row in priced.items()}
        ids = [t.trade_id for t in trades if t.trade_id in rows]
        matrix = (np.column_stack([rows[tid] for tid in ids]) if ids else np.zeros((n, 0))) * np.sqrt(horizon)
        return TradePnlHistory(dates=dates, trade_ids=ids, matrix=matrix, lookback_days=lookback,
                               horizon_days=horizon)

    def _price_parallel(self, trades: list[Trade], ctx: ValuationContext, lookback: int,
                        n: int, workers: int) -> tuple[list[float], float, int]:
        """Reprice the book across `workers` processes, split by trade chunk."""
        ordered = sorted(trades, key=lambda t: (t.asset_class.value, t.trade_id))
        chunks = _even_chunks([t.trade_id for t in ordered], workers)
        logger.info("Full-reval VaR: %d worker(s), %d chunk(s)", workers, len(chunks))
        pnl = np.zeros(n)
        base_total = 0.0
        n_priced = 0
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_hvar_worker_init,
            initargs=(self.engine.config, self.engine.root, ctx, lookback),
        ) as ex:
            for part_pnl, part_base, part_n in ex.map(_hvar_worker_chunk, chunks):
                pnl += np.asarray(part_pnl, dtype=float)
                base_total += part_base
                n_priced += part_n
        return pnl.tolist(), base_total, n_priced
