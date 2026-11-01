"""Rates instruments — the 4 Fixed-Income Derivatives (trades #22-25).

Each class:
  * inherits `Trade` (sets asset_class / trade_type),
  * composes leg / underlying-swap specifications,
  * implements `market_data_requirements` declaring the curves + vol it needs.

Economics are expressed with tenor strings (e.g. swap_tenor="10Y",
option_tenor="5Y"); the QuantLib layer turns these into dated schedules off the
valuation date and the relevant calendar. This keeps portfolio.json compact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from ..common.conventions import (
    SOFR_OIS_FIXED_SCHEDULE,
    SOFR_OIS_FLOAT_SCHEDULE,
    IndexConvention,
    ScheduleConvention,
)
from ..common.enums import (
    AssetClass,
    CurvePurpose,
    ExerciseStyle,
    Frequency,
    MarketDataKind,
    SettlementType,
    SwapDirection,
    TradeType,
    VolType,
)
from ..common.market_data import MarketDataRequest, MarketDataRequirement
from ..common.trade import Trade
from ..common.valuation import ValuationContext


def _ois_curve_requirements(currency: str, index_name: str) -> list[MarketDataRequirement]:
    """DISCOUNT + FORECAST OIS-curve requirements (multi-curve-ready).

    For SOFR OIS both resolve to the same curve object, but the trade declares
    both roles so a basis/XCCY curve can be supplied later without code changes.
    """
    return [
        MarketDataRequirement(
            kind=MarketDataKind.OIS_CURVE, currency=currency,
            purpose=CurvePurpose.DISCOUNT, index_name=index_name,
        ),
        MarketDataRequirement(
            kind=MarketDataKind.OIS_CURVE, currency=currency,
            purpose=CurvePurpose.FORECAST, index_name=index_name,
        ),
    ]


def _normal_vol_requirement(currency: str, tenor: str | None = None) -> MarketDataRequirement:
    return MarketDataRequirement(
        kind=MarketDataKind.NORMAL_VOL, currency=currency, tenor=tenor,
    )


# --------------------------------------------------------------------------- #
# Trade #22 — 10Y SOFR payer IRS
# --------------------------------------------------------------------------- #
@dataclass
class OvernightIndexedSwapTrade(Trade):
    """Single-currency SOFR OIS (fixed vs compounded overnight)."""
    asset_class: ClassVar[AssetClass] = AssetClass.RATES
    trade_type: ClassVar[TradeType] = TradeType.OIS_SWAP

    direction: SwapDirection = SwapDirection.PAYER
    swap_tenor: str = "10Y"
    fixed_rate: float = 0.0
    forward_start: str = "0D"            # spot-starting by default
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)
    fixed_schedule: ScheduleConvention = SOFR_OIS_FIXED_SCHEDULE
    float_schedule: ScheduleConvention = SOFR_OIS_FLOAT_SCHEDULE

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        for r in _ois_curve_requirements(self.currency, self.index.name):
            req.add(r)
        return req


# --------------------------------------------------------------------------- #
# Trade #23 — 5Y x 10Y European payer swaption
# --------------------------------------------------------------------------- #
@dataclass
class EuropeanSwaptionTrade(Trade):
    """European swaption on a SOFR OIS underlying, priced with normal vol."""
    asset_class: ClassVar[AssetClass] = AssetClass.RATES
    trade_type: ClassVar[TradeType] = TradeType.EUROPEAN_SWAPTION
    exercise_style: ClassVar[ExerciseStyle] = ExerciseStyle.EUROPEAN

    direction: SwapDirection = SwapDirection.PAYER   # payer swaption
    option_tenor: str = "5Y"             # time to expiry
    swap_tenor: str = "10Y"              # underlying swap length (from expiry)
    strike: float | None = None          # None => ATM forward
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)
    fixed_schedule: ScheduleConvention = SOFR_OIS_FIXED_SCHEDULE
    float_schedule: ScheduleConvention = SOFR_OIS_FLOAT_SCHEDULE
    settlement: SettlementType = SettlementType.PHYSICAL
    vol_type: VolType = VolType.NORMAL

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        for r in _ois_curve_requirements(self.currency, self.index.name):
            req.add(r)
        req.add(_normal_vol_requirement(self.currency, self.option_tenor))
        return req


# --------------------------------------------------------------------------- #
# Trade #24 — 2Y x 5Y Bermudan receiver swaption
# --------------------------------------------------------------------------- #
@dataclass
class BermudanSwaptionTrade(Trade):
    """Bermudan swaption — Hull-White co-terminal calibration then tree pricing.

    `exercise_frequency` defines the Bermudan exercise schedule between first
    exercise (option_tenor) and final maturity (option_tenor + swap_tenor).
    """
    asset_class: ClassVar[AssetClass] = AssetClass.RATES
    trade_type: ClassVar[TradeType] = TradeType.BERMUDAN_SWAPTION
    exercise_style: ClassVar[ExerciseStyle] = ExerciseStyle.BERMUDAN

    direction: SwapDirection = SwapDirection.RECEIVER  # receiver swaption
    option_tenor: str = "2Y"             # first exercise / lockout
    swap_tenor: str = "5Y"               # underlying swap length from first exercise
    exercise_frequency: Frequency = Frequency.ANNUAL
    strike: float | None = None          # None => ATM forward
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)
    fixed_schedule: ScheduleConvention = SOFR_OIS_FIXED_SCHEDULE
    float_schedule: ScheduleConvention = SOFR_OIS_FLOAT_SCHEDULE
    settlement: SettlementType = SettlementType.PHYSICAL
    vol_type: VolType = VolType.NORMAL

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        for r in _ois_curve_requirements(self.currency, self.index.name):
            req.add(r)
        # vol drives the co-terminal calibration grid
        req.add(_normal_vol_requirement(self.currency, self.option_tenor))
        return req


# --------------------------------------------------------------------------- #
# Trade #25 — 1Y SOFR cap (ATM)
# --------------------------------------------------------------------------- #
@dataclass
class CapTrade(Trade):
    """SOFR cap priced with a flat normal (Bachelier) vol."""
    asset_class: ClassVar[AssetClass] = AssetClass.RATES
    trade_type: ClassVar[TradeType] = TradeType.CAP

    cap_tenor: str = "1Y"
    strike: float | None = None          # None => ATM (forward swap/par rate)
    caplet_frequency: Frequency = Frequency.QUARTERLY
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)
    leg_schedule: ScheduleConvention = field(
        default_factory=lambda: ScheduleConvention(frequency=Frequency.QUARTERLY)
    )
    vol_type: VolType = VolType.NORMAL

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        for r in _ois_curve_requirements(self.currency, self.index.name):
            req.add(r)
        req.add(_normal_vol_requirement(self.currency, self.cap_tenor))
        return req
