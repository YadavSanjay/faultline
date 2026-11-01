"""FX instruments (trades #26-27).

* `FxForwardTrade` (#27) — a 3M USD/JPY forward: exchange USD vs foreign at a
  contracted rate on one date.
* `CrossCurrencySwapTrade` (#26) — a 5Y USD/TWD cross-currency swap, modelled as a
  fixed-for-fixed swap with notional exchange (USD leg on the SOFR curve, foreign
  leg on a flat foreign curve), combined at the current spot. The non-deliverable /
  MtM-reset and floating-leg details are simplified: spot is the dominant risk and
  no free TAIBOR / xccy-basis curve exists (see docs/design §1.5).

Both declare a USD SOFR discount curve + an `FX` requirement for the foreign ccy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

from ..common.conventions import IndexConvention
from ..common.enums import (
    AssetClass,
    CurvePurpose,
    FxDirection,
    MarketDataKind,
    TradeType,
)
from ..common.market_data import MarketDataRequest, MarketDataRequirement
from ..common.trade import Trade
from ..common.valuation import ValuationContext


def _fx_requirements(currency: str, foreign_ccy: str, index_name: str) -> list[MarketDataRequirement]:
    return [
        MarketDataRequirement(kind=MarketDataKind.OIS_CURVE, currency=currency,
                              purpose=CurvePurpose.DISCOUNT, index_name=index_name),
        MarketDataRequirement(kind=MarketDataKind.FX, currency="USD", index_name=foreign_ccy),
    ]


@dataclass(kw_only=True)
class FxForwardTrade(Trade):
    """USD vs foreign FX forward, settled (or cash/NDF) at `maturity_date`.

    `strike` is foreign units per 1 USD. LONG_USD = receive USD / pay foreign.
    `usd_notional` is the USD leg notional.
    """
    asset_class: ClassVar[AssetClass] = AssetClass.FX
    trade_type: ClassVar[TradeType] = TradeType.FX_FORWARD

    foreign_ccy: str
    usd_notional: float
    strike: float                        # foreign per USD
    maturity_date: date
    direction: FxDirection = FxDirection.LONG_USD
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        for r in _fx_requirements(self.currency, self.foreign_ccy, self.index.name):
            req.add(r)
        return req


@dataclass(kw_only=True)
class CrossCurrencySwapTrade(Trade):
    """Fixed-for-fixed USD/foreign cross-currency swap with notional exchange.

    Notionals are fixed at the contracted FX rate: foreign_notional = usd_notional *
    contract_fx (foreign per USD). LONG_USD = receive USD leg, pay foreign leg.
    """
    asset_class: ClassVar[AssetClass] = AssetClass.FX
    trade_type: ClassVar[TradeType] = TradeType.XCCY_SWAP

    foreign_ccy: str
    usd_notional: float
    contract_fx: float                   # foreign per USD at inception (sets foreign notional)
    usd_fixed_rate: float
    foreign_fixed_rate: float
    start_date: date
    maturity_date: date
    direction: FxDirection = FxDirection.LONG_USD
    frequency_months: int = 12           # annual coupons
    index: IndexConvention = field(default_factory=IndexConvention.usd_sofr)

    @property
    def foreign_notional(self) -> float:
        return self.usd_notional * self.contract_fx

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        for r in _fx_requirements(self.currency, self.foreign_ccy, self.index.name):
            req.add(r)
        return req
