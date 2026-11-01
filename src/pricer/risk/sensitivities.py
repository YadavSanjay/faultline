"""Sensitivities / Greeks via central bump-and-revalue (P2).

One consistent methodology across the whole book: resolve the base market data once,
then for each risk factor reprice at +bump and -bump and take the central difference.
Which sensitivities apply to a trade is inferred from the resolved data it actually
uses, so a swap gets IR DV01 + key-rate buckets, a corporate bond adds CS01, an option
adds vega + delta/gamma (+ IR rho from its discount curve), FX adds spot delta, etc.

All sensitivities are reported as **PV change per one bump unit** (central difference):
  IR_PARALLEL / IR_KR:<tenor>  per +1bp parallel / key-rate shift
  CS01                          per +1bp credit-spread widening
  VEGA_RATES / VEGA_RATES_TERM  per +1bp normal vol / +1bp normal-vol term slope (GAP 6)
  VEGA_EQUITY                   per +1 equity vol point
  VEGA_EQ_TERM / VEGA_EQ_SKEW   per +1 vol-pt term slope / +1 unit put-skew tilt (GAP 4)
  EQUITY_DELTA / EQUITY_GAMMA   per +1% underlier move (gamma is the 2nd difference)
  FX_DELTA / FX_RATE            per +1% FX-spot move / per +1bp foreign-rate move (rho)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..engine import PricingEngine
from ..input.common.market_data import (
    CdsSpreadData,
    CreditSpreadData,
    EquityMarketData,
    EquitySpotData,
    FlatNormalVolData,
    FxData,
    MarketDataSet,
    ParOISCurveData,
    TreasuryCurveData,
)
from ..input.common.trade import Trade
from ..input.common.valuation import ValuationContext
from ..ql.common.curve_cache import curve_cache
from ..ql.factory import get_pricer
from . import bumps

_BP = 1e-4


@dataclass(frozen=True)
class Sensitivity:
    factor: str          # e.g. "IR_PARALLEL", "IR_KR:5Y", "CS01", "VEGA_EQUITY"
    value: float         # PV change per one bump unit (central difference)
    bump_unit: str       # human label for the bump unit
    method: str = "central_bump_revalue"

    def as_dict(self) -> dict[str, Any]:
        return {"factor": self.factor, "value": self.value,
                "bump_unit": self.bump_unit, "method": self.method}


@dataclass
class RiskResult:
    trade_id: str
    asset_class: str
    currency: str
    valuation_date: date
    pv: float
    sensitivities: list[Sensitivity] = field(default_factory=list)

    def by_factor(self) -> dict[str, float]:
        return {s.factor: s.value for s in self.sensitivities}

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "asset_class": self.asset_class,
            "currency": self.currency,
            "valuation_date": self.valuation_date.isoformat(),
            "pv": self.pv,
            "sensitivities": [s.as_dict() for s in self.sensitivities],
        }


class RiskEngine:
    """Sensitivities over a `PricingEngine`. Bump sizes are configurable (config['risk'])."""

    def __init__(self, engine: PricingEngine, config: dict | None = None):
        self.engine = engine
        rc = (config or engine.config).get("risk", {})
        self.ir_bump = float(rc.get("ir_bump_bp", 1.0)) * _BP
        self.credit_bump = float(rc.get("credit_bump_bp", 1.0)) * _BP
        self.rates_vol_bump = float(rc.get("rates_vol_bump_bp", 1.0)) * _BP
        self.equity_vol_bump = float(rc.get("equity_vol_bump", 0.01))
        self.equity_vol_term_bump = float(rc.get("equity_vol_term_bump", 0.01))
        self.equity_vol_skew_bump = float(rc.get("equity_vol_skew_bump", 0.01))
        self.spot_bump = float(rc.get("spot_bump_pct", 1.0)) / 100.0
        self.fx_bump = float(rc.get("fx_bump_pct", 1.0)) / 100.0

    # -- public -------------------------------------------------------------- #
    def sensitivities(self, trade: Trade, ctx: ValuationContext,
                      key_rates: bool = True) -> RiskResult:
        with curve_cache():
            mds = self.engine.loader.resolve(trade.market_data_requirements(ctx), ctx)
            base_pv = self._pv(trade, mds, ctx)
            present = {type(v) for v in mds.items.values()}
            sens: list[Sensitivity] = []

            if present & {ParOISCurveData, TreasuryCurveData}:
                sens.extend(self._ir(trade, mds, ctx, key_rates))
            if present & {CreditSpreadData, CdsSpreadData}:
                sens.append(self._cs01(trade, mds, ctx))
            if FlatNormalVolData in present:
                sens.append(self._vega(trade, mds, ctx, self.rates_vol_bump,
                                       "VEGA_RATES", "+1bp normal vol", bumps.shift_rates_vol))
                sens.append(self._vega(trade, mds, ctx, self.rates_vol_bump,
                                       "VEGA_RATES_TERM", "+1bp normal-vol term slope",
                                       bumps.shift_rates_vol_term))
            elif EquityMarketData in present:
                sens.append(self._vega(trade, mds, ctx, self.equity_vol_bump,
                                       "VEGA_EQUITY", "+1 vol point", bumps.shift_equity_vol))
                sens.append(self._vega(trade, mds, ctx, self.equity_vol_term_bump,
                                       "VEGA_EQ_TERM", "+1 vol-pt term slope",
                                       bumps.shift_equity_vol_term))
                sens.append(self._vega(trade, mds, ctx, self.equity_vol_skew_bump,
                                       "VEGA_EQ_SKEW", "+1 unit put-skew tilt",
                                       bumps.shift_equity_vol_skew))
            if present & {EquitySpotData, EquityMarketData}:
                sens.extend(self._equity_delta_gamma(trade, mds, ctx, base_pv))
            if FxData in present:
                sens.append(self._fx_delta(trade, mds, ctx))
                sens.append(self._fx_rate(trade, mds, ctx))

        return RiskResult(trade.trade_id, trade.asset_class.value, trade.currency,
                          ctx.valuation_date, base_pv, sens)

    # -- helpers ------------------------------------------------------------- #
    def _pv(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> float:
        return float(get_pricer(trade).price(trade, mds, ctx).pv)

    def _central(self, trade: Trade, ctx: ValuationContext, mds: MarketDataSet,
                 up: bumps.Transform, dn: bumps.Transform) -> float:
        pv_up = self._pv(trade, bumps.bumped(mds, up), ctx)
        pv_dn = self._pv(trade, bumps.bumped(mds, dn), ctx)
        return (pv_up - pv_dn) / 2.0

    def _ir(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext,
            key_rates: bool) -> list[Sensitivity]:
        b = self.ir_bump
        out = [Sensitivity("IR_PARALLEL",
                           self._central(trade, ctx, mds, bumps.shift_ir(b), bumps.shift_ir(-b)),
                           "+1bp parallel")]
        if key_rates:
            out.extend(
                Sensitivity(
                    f"IR_KR:{tenor}",
                    self._central(trade, ctx, mds, bumps.shift_ir(b, tenor), bumps.shift_ir(-b, tenor)),
                    "+1bp key-rate")
                for tenor in _curve_tenors(mds))
        return out

    def _cs01(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> Sensitivity:
        b = self.credit_bump
        return Sensitivity("CS01",
                           self._central(trade, ctx, mds, bumps.shift_credit(b), bumps.shift_credit(-b)),
                           "+1bp credit spread")

    def _vega(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext, b: float,
              factor: str, unit: str,
              tf: Callable[[float], bumps.Transform]) -> Sensitivity:
        return Sensitivity(factor, self._central(trade, ctx, mds, tf(b), tf(-b)), unit)

    def _equity_delta_gamma(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext,
                            base_pv: float) -> list[Sensitivity]:
        r = self.spot_bump
        pv_up = self._pv(trade, bumps.bumped(mds, bumps.scale_equity(r)), ctx)
        pv_dn = self._pv(trade, bumps.bumped(mds, bumps.scale_equity(-r)), ctx)
        delta = (pv_up - pv_dn) / 2.0
        gamma = pv_up - 2.0 * base_pv + pv_dn
        return [Sensitivity("EQUITY_DELTA", delta, "+1% underlier"),
                Sensitivity("EQUITY_GAMMA", gamma, "per (1% underlier)^2")]

    def _fx_delta(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> Sensitivity:
        r = self.fx_bump
        return Sensitivity("FX_DELTA",
                           self._central(trade, ctx, mds, bumps.scale_fx(r), bumps.scale_fx(-r)),
                           "+1% FX spot")

    def _fx_rate(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> Sensitivity:
        b = self.ir_bump   # foreign-rate rho, per +1bp (GAP 5)
        return Sensitivity("FX_RATE",
                           self._central(trade, ctx, mds, bumps.shift_fx_rate(b), bumps.shift_fx_rate(-b)),
                           "+1bp foreign rate")


def _curve_tenors(mds: MarketDataSet) -> tuple[str, ...]:
    """Tenor grid of the first OIS / Treasury curve in the set (for key-rate buckets)."""
    for item in mds.items.values():
        if isinstance(item, (ParOISCurveData, TreasuryCurveData)):
            return item.tenors
    return ()
