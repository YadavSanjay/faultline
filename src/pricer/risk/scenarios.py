"""Scenario / stress engine (P2) — full-revaluation stress testing.

A `Scenario` is a named set of simultaneous shocks to risk factors (parallel rates
move, credit-spread widening, equity sell-off + vol spike, FX move). Each shock is the
same bump transform used for sensitivities, sized for stress rather than 1bp. The book
is **fully re-priced** under the shocked market data (not Greek-approximated), and the
result is the scenario P&L per trade and aggregated by asset class.

Scenarios are config-driven (`config['risk']['scenarios']`), including the design-doc
narratives (Taiwan-geopolitical, AI-capex bust/boom) plus standard regulatory-style
shocks (rates sell-off, credit blow-out). New scenarios need no code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

from ..engine import PricingEngine
from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from ..ql.common.curve_cache import curve_cache
from ..ql.factory import get_pricer
from . import bumps

_BP = 1e-4


@runtime_checkable
class SupportsTransforms(Protocol):
    """Anything the scenario engine can fully reprice: it just needs a list of bump
    transforms. Both the scalar `Scenario` and scenariogen's per-factor
    `MaterializedScenario` satisfy this, so they share one full-revaluation P&L path."""

    def transforms(self) -> list[bumps.Transform]: ...


@dataclass(frozen=True)
class Scenario:
    """A named simultaneous shock. Magnitudes are in natural units (see field docs)."""

    name: str
    description: str = ""
    ir_bp: float = 0.0          # parallel rates shift, basis points
    credit_bp: float = 0.0      # credit-spread widening, basis points
    equity_pct: float = 0.0     # underlier move, percent (e.g. -25 = -25%)
    equity_vol: float = 0.0     # additive equity vol points (e.g. 0.20 = +20 vols)
    rates_vol_bp: float = 0.0   # additive normal-vol shift, basis points
    fx_pct: float = 0.0         # FX spot move (foreign per USD), percent

    def transforms(self) -> list[bumps.Transform]:
        out: list[bumps.Transform] = []
        if self.ir_bp:
            out.append(bumps.shift_ir(self.ir_bp * _BP))
        if self.credit_bp:
            out.append(bumps.shift_credit(self.credit_bp * _BP))
        if self.rates_vol_bp:
            out.append(bumps.shift_rates_vol(self.rates_vol_bp * _BP))
        if self.equity_vol:
            out.append(bumps.shift_equity_vol(self.equity_vol))
        if self.equity_pct:
            out.append(bumps.scale_equity(self.equity_pct / 100.0))
        if self.fx_pct:
            out.append(bumps.scale_fx(self.fx_pct / 100.0))
        return out

    @classmethod
    def from_config(cls, name: str, spec: dict[str, Any]) -> Scenario:
        return cls(
            name=name,
            description=spec.get("description", ""),
            ir_bp=float(spec.get("ir_bp", 0.0)),
            credit_bp=float(spec.get("credit_bp", 0.0)),
            equity_pct=float(spec.get("equity_pct", 0.0)),
            equity_vol=float(spec.get("equity_vol", 0.0)),
            rates_vol_bp=float(spec.get("rates_vol_bp", 0.0)),
            fx_pct=float(spec.get("fx_pct", 0.0)),
        )


@dataclass
class ScenarioPnl:
    trade_id: str
    asset_class: str
    currency: str
    base_pv: float
    scenario_pv: float

    @property
    def pnl(self) -> float:
        return self.scenario_pv - self.base_pv

    def as_dict(self) -> dict[str, Any]:
        return {"trade_id": self.trade_id, "asset_class": self.asset_class,
                "currency": self.currency, "base_pv": self.base_pv,
                "scenario_pv": self.scenario_pv, "pnl": self.pnl}


def load_scenarios(config: dict[str, Any]) -> dict[str, Scenario]:
    specs = config.get("risk", {}).get("scenarios", {})
    return {name: Scenario.from_config(name, spec) for name, spec in specs.items()}


class ScenarioEngine:
    def __init__(self, engine: PricingEngine):
        self.engine = engine

    def run(self, scenario: SupportsTransforms, trades: list[Trade],
            ctx: ValuationContext) -> list[ScenarioPnl]:
        """Fully reprice every trade under the scenario; return per-trade P&L."""
        tfs = scenario.transforms()
        out: list[ScenarioPnl] = []
        with curve_cache():
            for trade in trades:
                base_mds = self.engine.loader.resolve(trade.market_data_requirements(ctx), ctx)
                pricer = get_pricer(trade)
                try:
                    base_pv = float(pricer.price(trade, base_mds, ctx).pv)
                    shocked_pv = float(pricer.price(trade, bumps.bumped(base_mds, *tfs), ctx).pv)
                except Exception:  # a trade that can't price under stress shouldn't kill the run
                    continue
                out.append(ScenarioPnl(trade.trade_id, trade.asset_class.value,
                                       trade.currency, base_pv, shocked_pv))
        return out


@dataclass
class ScenarioReport:
    scenario: str
    description: str
    valuation_date: date
    results: list[ScenarioPnl] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return sum(r.pnl for r in self.results)

    def by_asset_class(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for r in self.results:
            agg[r.asset_class] = agg.get(r.asset_class, 0.0) + r.pnl
        return agg

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "description": self.description,
            "valuation_date": self.valuation_date.isoformat(),
            "total_pnl": self.total_pnl,
            "pnl_by_asset_class": self.by_asset_class(),
            "trades": [r.as_dict() for r in self.results],
        }
