"""P&L attribution / explain (P2).

Decomposes a market move's P&L into first-order Greek contributions and an
unexplained residual — the FRTB P&L-Attribution idea: compare *risk-theoretical* P&L
(sensitivities x factor moves) against the *hypothetical* P&L (a full revaluation).
A small residual means the sensitivities capture the trade's risk; a large one flags
missing factors or material higher-order / cross-gamma terms.

Here the move is a named `Scenario`, so the actual P&L is the scenario full-reval and
the explained P&L uses the base Greeks against the scenario's factor shocks (units
reconciled to each sensitivity's bump unit). The same machinery applies to a realised
day-over-day move once two dated snapshots are available.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from ..ql.factory import get_pricer
from .scenarios import Scenario, ScenarioEngine
from .sensitivities import RiskEngine


@dataclass
class TradePnlExplain:
    trade_id: str
    asset_class: str
    actual: float
    explained: dict[str, float] = field(default_factory=dict)

    @property
    def explained_total(self) -> float:
        return sum(self.explained.values())

    @property
    def residual(self) -> float:
        return self.actual - self.explained_total

    def as_dict(self) -> dict[str, Any]:
        return {"trade_id": self.trade_id, "asset_class": self.asset_class,
                "actual": self.actual, "explained": self.explained,
                "explained_total": self.explained_total, "residual": self.residual}


@dataclass
class PnlExplainReport:
    scenario: str
    valuation_date: date
    trades: list[TradePnlExplain] = field(default_factory=list)

    @property
    def actual_total(self) -> float:
        return sum(t.actual for t in self.trades)

    def explained_by_factor(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for t in self.trades:
            for k, v in t.explained.items():
                agg[k] = agg.get(k, 0.0) + v
        return agg

    @property
    def residual_total(self) -> float:
        return self.actual_total - sum(self.explained_by_factor().values())

    @property
    def coverage(self) -> float:
        """Explained / actual (1.0 = fully explained by first-order Greeks)."""
        a = self.actual_total
        return (a - self.residual_total) / a if a else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "valuation_date": self.valuation_date.isoformat(),
            "actual_total": self.actual_total,
            "explained_by_factor": self.explained_by_factor(),
            "residual_total": self.residual_total,
            "coverage": self.coverage,
            "trades": [t.as_dict() for t in self.trades],
        }


class PnlExplainEngine:
    def __init__(self, risk_engine: RiskEngine, scenario_engine: ScenarioEngine):
        self.risk = risk_engine
        self.scen = scenario_engine

    def explain_scenario(self, scenario: Scenario, trades: list[Trade],
                         ctx: ValuationContext) -> PnlExplainReport:
        actual = {p.trade_id: p.pnl for p in self.scen.run(scenario, trades, ctx)}
        vol_pts = (scenario.equity_vol / self.risk.equity_vol_bump
                   if self.risk.equity_vol_bump else 0.0)
        out: list[TradePnlExplain] = []
        for trade in trades:
            if trade.trade_id not in actual:
                continue
            # Key-rate sensitivities (Tier 3.8): the rates leg is summed across the DGS grid, so a
            # curve-shape shock is attributed by bucket instead of collapsing into the residual.
            s = self.risk.sensitivities(trade, ctx, key_rates=True).by_factor()
            ir = sum(v * scenario.ir_bp for k, v in s.items() if k.startswith("IR_KR:"))
            if ir == 0.0:
                ir = s.get("IR_PARALLEL", 0.0) * scenario.ir_bp   # no key-rate buckets -> parallel
            explained = {
                "ir": ir,
                "credit": s.get("CS01", 0.0) * scenario.credit_bp,
                "rates_vol": s.get("VEGA_RATES", 0.0) * scenario.rates_vol_bp,
                "equity_vol": s.get("VEGA_EQUITY", 0.0) * vol_pts,
                "equity_delta": s.get("EQUITY_DELTA", 0.0) * scenario.equity_pct,
                "equity_gamma": 0.5 * s.get("EQUITY_GAMMA", 0.0) * scenario.equity_pct ** 2,
                "fx": s.get("FX_DELTA", 0.0) * scenario.fx_pct,
            }
            explained = {k: v for k, v in explained.items() if v}
            out.append(TradePnlExplain(trade.trade_id, trade.asset_class.value,
                                       actual[trade.trade_id], explained))
        return PnlExplainReport(scenario.name, ctx.valuation_date, out)

    def carry_theta(self, trades: list[Trade], ctx: ValuationContext,
                    horizon_days: int = 1) -> CarryThetaReport:
        """Predicted carry/theta: the P&L from time passing with the market held flat (Tier 3.8).

        Each trade is repriced ``horizon_days`` forward against the *same* market data resolved
        as-of today, so the difference is pure carry + roll-down (the time-decay leg a full P&L
        attribution needs beyond the market-move Greeks). Priced outside the curve cache because
        the cache assumes a fixed evaluation date within its block.
        """
        ctx_fwd = replace(ctx, valuation_date=ctx.valuation_date + timedelta(days=horizon_days))
        rows: list[TradeCarry] = []
        for trade in trades:
            try:
                mds = self.risk.engine.loader.resolve(trade.market_data_requirements(ctx), ctx)
                pricer = get_pricer(trade)
                pv = float(pricer.price(trade, mds, ctx).pv)
                pv_fwd = float(pricer.price(trade, mds, ctx_fwd).pv)
            except Exception:   # a trade that can't reprice one day out contributes no carry
                continue
            rows.append(TradeCarry(trade.trade_id, trade.asset_class.value, pv, pv_fwd))
        return CarryThetaReport(ctx.valuation_date, horizon_days, rows)


@dataclass
class TradeCarry:
    trade_id: str
    asset_class: str
    pv: float
    pv_forward: float

    @property
    def carry(self) -> float:
        return self.pv_forward - self.pv

    def as_dict(self) -> dict[str, Any]:
        return {"trade_id": self.trade_id, "asset_class": self.asset_class,
                "pv": self.pv, "pv_forward": self.pv_forward, "carry": self.carry}


@dataclass
class CarryThetaReport:
    valuation_date: date
    horizon_days: int
    trades: list[TradeCarry] = field(default_factory=list)

    @property
    def total_carry(self) -> float:
        return sum(t.carry for t in self.trades)

    def by_asset_class(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for t in self.trades:
            agg[t.asset_class] = agg.get(t.asset_class, 0.0) + t.carry
        return agg

    def to_dict(self) -> dict[str, Any]:
        return {
            "valuation_date": self.valuation_date.isoformat(),
            "horizon_days": self.horizon_days,
            "total_carry": self.total_carry,
            "carry_by_asset_class": self.by_asset_class(),
            "trades": [t.as_dict() for t in self.trades],
        }
