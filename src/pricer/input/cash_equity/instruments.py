"""Cash-equity / ETF positions (trades #1-6).

Mark-to-market instruments: a signed share quantity in a listed name. The only
market data needed is the spot price; value = quantity x spot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..common.enums import AssetClass, MarketDataKind, TradeType
from ..common.market_data import MarketDataRequest, MarketDataRequirement
from ..common.trade import Trade
from ..common.valuation import ValuationContext


@dataclass(kw_only=True)
class CashEquityTrade(Trade):
    """A long (quantity > 0) or short (quantity < 0) cash-equity / ETF position."""
    asset_class: ClassVar[AssetClass] = AssetClass.EQUITY
    trade_type: ClassVar[TradeType] = TradeType.STOCK

    ticker: str
    quantity: float                      # signed number of shares

    def market_data_requirements(self, ctx: ValuationContext) -> MarketDataRequest:
        req = MarketDataRequest(self.trade_id, ctx.valuation_date)
        req.add(MarketDataRequirement(
            kind=MarketDataKind.EQUITY_SPOT, currency=self.currency, index_name=self.ticker,
        ))
        return req
