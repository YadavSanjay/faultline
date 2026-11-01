"""Cash-equity mark-to-market pricer (trades #1-6).

Value = quantity x spot (QuantLib's `Stock`/`SimpleQuote` is the natural wrapper,
but the mark is a single multiply). Reports cash delta (= market value), gross
notional, and the trailing dividend yield as a carry diagnostic. Delta in shares
equals the position size; gamma/vega are zero for linear cash equity.
"""

from __future__ import annotations

import QuantLib as ql

from ...input.cash_equity.instruments import CashEquityTrade
from ...input.common.market_data import MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ..common.base_pricer import BasePricer
from ..common.pricing_result import PricingResult


class CashEquityPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, CashEquityTrade)
        spot_data = mds.equity_spot(trade.currency, trade.ticker)

        spot_quote = ql.SimpleQuote(spot_data.spot)        # QL Stock-style quote
        market_value = trade.quantity * spot_quote.value()

        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date,
            pv=market_value,
            measures={
                "spot": spot_data.spot,
                "quantity": trade.quantity,
                "delta_shares": trade.quantity,            # linear: delta == position
                "cash_delta": market_value,
                "gross_notional": abs(trade.quantity) * spot_data.spot,
                "dividend_yield": spot_data.dividend_yield,
            },
            diagnostics={
                "ticker": trade.ticker,
                "direction": "long" if trade.quantity >= 0 else "short",
                "spot_as_of": spot_data.as_of_date.isoformat(),
                "spot_source": spot_data.source,
            },
        )
