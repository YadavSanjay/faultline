"""Fixed-rate bond instruments (trades #15-21).

`FixedRateBondTrade` holds the common bullet-bond economics; `CorporateBondTrade`
and `TreasuryBondTrade` differ only in conventions and in the market data they
declare:

  * Corporate (#15-19): SOFR discount curve + issuer credit spread
    (priced via a ZeroSpreadedTermStructure over SOFR).
  * Treasury  (#20-21): a bootstrapped UST par-yield discount curve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

from ..common.conventions import IndexConvention
from ..common.enums import (
    AssetClass,
    BusinessDayConvention,
    CalendarCode,
    CurvePurpose,
    DayCount,
    Frequency,
    MarketDataKind,
    TradeType,
)
from ..common.market_data import MarketDataRequest, MarketDataRequirement
from ..common.trade import Trade
from ..common.valuation import ValuationContext


@dataclass(kw_only=True)
class FixedRateBondTrade(Trade):
    """Common fixed-rate bullet-bond economics (notional = face amount)."""
    asset_class: ClassVar[AssetClass] = AssetClass.BONDS

    coupon: float
    issue_date: date
    maturity_date: date
    coupon_frequency: Frequency = Frequency.SEMIANNUAL
    day_count: DayCount = DayCount.THIRTY_360
    calendar: CalendarCode = CalendarCode.US_GOVERNMENT_BOND
    business_day_convention: BusinessDayConvention = BusinessDayConvention.UNADJUSTED
    settlement_days: int = 2
    cusip: str | None = None


@dataclass(kw_only=True)
class CorporateBondTrade(FixedRateBondTrade):
    """Senior-unsecured fixed-rate corporate bullet (30/360, semiannual, T+2)."""
    trade_type: ClassVar[TradeType] = TradeType.CORP_BOND

    issuer: str = ""
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)
    day_count: DayCount = DayCount.THIRTY_360
    settlement_days: int = 2

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        # discount on the SOFR curve (no projection leg — coupons are fixed)
        req.add(MarketDataRequirement(
            kind=MarketDataKind.OIS_CURVE, currency=self.currency,
            purpose=CurvePurpose.DISCOUNT, index_name=self.index.name,
        ))
        # issuer credit spread, applied over the SOFR curve
        req.add(MarketDataRequirement(
            kind=MarketDataKind.CREDIT_SPREAD, currency=self.currency,
            index_name=self.issuer,
        ))
        return req


@dataclass(kw_only=True)
class TreasuryBondTrade(FixedRateBondTrade):
    """On-the-run US Treasury note/bond (ActualActual(Bond), semiannual, T+1)."""
    trade_type: ClassVar[TradeType] = TradeType.UST_BOND

    term: str = ""                       # e.g. "10Y", "2Y" (descriptive)
    day_count: DayCount = DayCount.ACT_ACT_BOND
    settlement_days: int = 1

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        req.add(MarketDataRequirement(
            kind=MarketDataKind.TREASURY_CURVE, currency=self.currency,
            purpose=CurvePurpose.DISCOUNT,
        ))
        return req
