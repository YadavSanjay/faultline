"""Trade-level risk model for position sizing.

Builds, for every trade, a hypothetical daily P&L series by projecting the trade's
first-order sensitivities onto the same aligned historical factor-move panel the
sensitivity-based VaR engine uses (:class:`src.pricer.risk.var.VarEngine`). Stacking those
series gives a ``(n_days, n_trades)`` P&L matrix ``P`` at the book's *current* position
sizes. Because first-order P&L is linear in size, resizing trade ``j`` by a multiplier
``m_j`` simply scales column ``j``; the resized book's P&L vector is ``P @ m`` and its
VaR/ES are quantiles of ``-(P @ m)``. The sample covariance ``cov(P)`` is the empirically
diversified trade-level Sigma the optimizer sizes against.

This reconstruction sums to the VaR engine's book P&L when ``m = 1`` (see
``scripts``/tests), so the sizing model is consistent with the desk's reported VaR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from ..risk.sensitivities import RiskEngine
from ..risk.var import VarEngine, _maps_to

logger = logging.getLogger("pricer.sizing.trade_risk")


@dataclass
class TradeRisk:
    """Per-trade risk record at current position size."""

    trade_id: str
    asset_class: str
    description: str
    pv: float
    notional: float
    sensitivities: dict[str, float]      # re-keyed (EQUITY_DELTA:NVDA, FX_DELTA:JPY, ...)
    is_hedge: bool = False
    standalone_vol: float = 0.0          # std of standalone daily P&L ($/day)
    standalone_var99: float = 0.0        # 99% 1-day VaR standalone ($)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "asset_class": self.asset_class,
            "description": self.description,
            "pv": self.pv,
            "notional": self.notional,
            "is_hedge": self.is_hedge,
            "standalone_vol": self.standalone_vol,
            "standalone_var99": self.standalone_var99,
            "sensitivities": self.sensitivities,
        }


class TradeRiskModel:
    """Trade-level P&L matrix, covariance and portfolio risk under resizing.

    ``multipliers`` throughout are per-trade scale factors on the *current* signed
    position (``1.0`` = keep current size, ``2.0`` = double, ``0.0`` = flatten). Signs are
    never flipped, so a long stays long and a hedge stays a hedge — only magnitude moves.
    """

    def __init__(self, trades: list[TradeRisk], pnl_matrix: np.ndarray,
                 factor_columns: list[str], dates: list[str]):
        self.trades = trades
        self.trade_ids = [t.trade_id for t in trades]
        self.P = pnl_matrix                      # (n_days, n_trades) at current sizes
        self.factor_columns = factor_columns
        self.dates = dates
        self.n_days, self.n_trades = pnl_matrix.shape
        # Trade-level covariance (daily $). ddof=1 sample covariance.
        self.cov = np.cov(pnl_matrix, rowvar=False, ddof=1)
        if self.cov.ndim == 0:                   # single-trade edge case
            self.cov = self.cov.reshape(1, 1)

    # -- construction -------------------------------------------------------- #
    @classmethod
    def build(cls, trades: list[Trade], ctx: ValuationContext, risk: RiskEngine,
              config: dict[str, Any], root: Path, hedge_ids: set[str],
              lookback: int = 250) -> TradeRiskModel:
        """Compute per-trade sensitivities and the P&L matrix over the lookback window."""
        var_engine = VarEngine(risk, config, root)

        # 1. Per-trade re-keyed sensitivities (equity delta/vega per underlier, FX per ccy),
        #    mirroring VarEngine._book_sensitivities so factor moves line up.
        recs: list[TradeRisk] = []
        sens_by_trade: list[dict[str, float]] = []
        for trade in trades:
            try:
                rr = risk.sensitivities(trade, ctx, key_rates=True)
            except Exception as exc:
                logger.warning("sizing: sensitivities failed for %s: %s", trade.trade_id, exc)
                continue
            eq_key, fx_key = var_engine._exposure_keys(trade, ctx)
            sens = _rekey(rr.by_factor(), eq_key, fx_key, var_engine._credit_key(trade))
            sens_by_trade.append(sens)
            recs.append(TradeRisk(
                trade_id=trade.trade_id,
                asset_class=str(getattr(trade.asset_class, "value", trade.asset_class)),
                description=getattr(trade, "description", "") or "",
                pv=float(rr.pv),
                notional=float(getattr(trade, "notional", 0.0) or 0.0),
                sensitivities=sens,
                is_hedge=trade.trade_id in hedge_ids,
            ))

        # 2. Per-factor daily move series (P&L per unit sensitivity), loaded once.
        moves: dict[str, pd.Series] = {}
        maps_to: dict[str, list[str]] = {}
        for fname, fcfg in var_engine.cfg.get("factors", {}).items():
            keys = _maps_to(fcfg)   # a factor may drive several keys that share its move series
            # Keep a factor only if some trade actually has exposure to one of its target keys.
            if not any(abs(s.get(k, 0.0)) > 0.0 for s in sens_by_trade for k in keys):
                continue
            series = var_engine._factor_moves(fname, fcfg)
            if series is not None and not series.empty:
                moves[fname] = series
                maps_to[fname] = keys

        if not moves:
            raise ValueError("sizing: no risk factors mapped to any trade sensitivity")

        # 3. Align all used factors on the VaR engine's window (the full-reval panel's last `lookback`
        #    dates, short gaps carried as zero moves) so the trade matrix reconciles to the book VaR.
        panel = var_engine._panel(moves, lookback)
        used = list(panel.columns)
        dates = [str(pd.Timestamp(d).date()) for d in panel.index]

        # 4. Trade P&L matrix: column j = sum_f move_f * sensitivity_j[maps_to(f)].
        n_days = len(panel)
        P = np.zeros((n_days, len(recs)))
        for j, sens in enumerate(sens_by_trade):
            col = np.zeros(n_days)
            for fname in used:
                s = sum(sens.get(k, 0.0) for k in maps_to[fname])
                if s != 0.0:
                    col += panel[fname].to_numpy() * s
            P[:, j] = col

        model = cls(recs, P, used, dates)
        # Fill standalone risk on each record.
        for j, rec in enumerate(recs):
            rec.standalone_vol = float(P[:, j].std(ddof=1))
            rec.standalone_var99 = float(np.quantile(-P[:, j], 0.99))
        return model

    # -- portfolio risk under resizing --------------------------------------- #
    def portfolio_pnl(self, multipliers: np.ndarray) -> np.ndarray:
        return np.asarray(self.P @ multipliers, dtype=float)

    def var_es(self, multipliers: np.ndarray,
               levels: tuple[float, ...] = (0.95, 0.975, 0.99)) -> dict[str, dict[str, float]]:
        pnl = self.portfolio_pnl(multipliers)
        loss = -pnl
        var: dict[str, float] = {}
        es: dict[str, float] = {}
        for lvl in levels:
            q = float(np.quantile(loss, lvl))
            tail = loss[loss >= q]
            var[f"{lvl}"] = q
            es[f"{lvl}"] = float(tail.mean()) if tail.size else q
        return {"var": var, "es": es}

    def var99(self, multipliers: np.ndarray) -> float:
        return float(np.quantile(-(self.P @ multipliers), 0.99))

    def portfolio_vol(self, multipliers: np.ndarray) -> float:
        return float(np.sqrt(multipliers @ self.cov @ multipliers))

    def risk_contributions(self, multipliers: np.ndarray) -> np.ndarray:
        """Euler / component risk contribution of each trade to portfolio volatility ($).

        RC_j = m_j * (Sigma m)_j / sqrt(mT Sigma m); sums to the portfolio volatility.
        """
        vol = self.portfolio_vol(multipliers)
        if vol <= 0.0:
            return np.zeros(self.n_trades)
        return np.asarray(multipliers * (self.cov @ multipliers) / vol, dtype=float)

    def scale_to_var_budget(self, multipliers: np.ndarray, target_var99: float) -> np.ndarray:
        """Uniformly scale a weight vector so its 1-day 99% VaR equals ``target_var99``."""
        cur = self.var99(multipliers)
        if cur <= 0.0:
            return multipliers
        return multipliers * (target_var99 / cur)


def _rekey(by_factor: dict[str, float], eq_key: str | None,
           fx_key: str | None, credit_key: str | None = None) -> dict[str, float]:
    """Re-key generic equity/FX/credit factors to per-underlier / per-currency / per-rating-bucket,
    exactly as ``VarEngine._book_sensitivities`` does, so the trade matrix reconciles to the book."""
    out: dict[str, float] = {}
    for f, v in by_factor.items():
        if f in ("EQUITY_DELTA", "VEGA_EQUITY") and eq_key:
            key = f"{f}:{eq_key}"
        elif f in ("FX_DELTA", "FX_RATE") and fx_key:
            key = f"{f}:{fx_key}"
        elif f == "CS01" and credit_key:
            key = f"CS01:{credit_key}"
        else:
            key = f
        out[key] = out.get(key, 0.0) + v
    return out
