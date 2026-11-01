"""Listed equity-option instruments (trades #7-14).

A single `EquityOptionTrade` composes one or more `OptionLeg`s, which is enough to
express every structure in the book:

  * single vanilla   (#7 LEAPS call, #8 protective put, #12 SPX put)
  * call/put spread  (#9 call spread, #10 put spread, #13 SPX put spread)
  * straddle         (#11 long call + long put, same strike)
  * collar           (#14 long put + short call)

Each leg carries its own exercise style, so American single-stock legs and
European index legs coexist in one book. The pricer values each leg with the
appropriate engine and sums quantity x multiplier x per-share value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

from ..common.conventions import IndexConvention
from ..common.enums import (
    AssetClass,
    CurvePurpose,
    ExerciseStyle,
    MarketDataKind,
    OptionRight,
    TradeType,
)
from ..common.market_data import MarketDataRequest, MarketDataRequirement
from ..common.trade import Trade
from ..common.valuation import ValuationContext


@dataclass
class OptionLeg:
    """One vanilla option line. quantity is signed (long > 0, short < 0), in contracts."""
    right: OptionRight
    strike: float
    expiry_date: date
    exercise_style: ExerciseStyle
    quantity: float = 1.0                 # signed number of contracts


@dataclass(kw_only=True)
class EquityOptionTrade(Trade):
    """Listed equity-option trade composed of one or more vanilla legs."""
    asset_class: ClassVar[AssetClass] = AssetClass.OPTIONS
    trade_type: ClassVar[TradeType] = TradeType.EQUITY_OPTION

    underlier: str
    legs: list[OptionLeg]
    contract_multiplier: float = 100.0    # shares per option contract
    structure: str = "vanilla"            # descriptive label
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        # risk-free discounting on the SOFR curve
        req.add(MarketDataRequirement(
            kind=MarketDataKind.OIS_CURVE, currency=self.currency,
            purpose=CurvePurpose.DISCOUNT, index_name=self.index.name,
        ))
        # spot + dividend yield + vol surface for the underlier
        req.add(MarketDataRequirement(
            kind=MarketDataKind.EQUITY_MARKET, currency=self.currency,
            index_name=self.underlier,
        ))
        return req
