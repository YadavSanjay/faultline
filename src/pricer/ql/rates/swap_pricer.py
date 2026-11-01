"""Trade #22 — SOFR Overnight-Indexed Swap (OIS IRS).

MakeOIS builds the swap with standard SOFR conventions; DiscountingSwapEngine on
the (discount) SOFR curve produces the PV. Forecast and discount handles are kept
distinct (multi-curve-ready) even though they coincide for SOFR today.
"""

from __future__ import annotations

import QuantLib as ql

from ...input.common.enums import SwapDirection
from ...input.common.market_data import MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ...input.rates.instruments import OvernightIndexedSwapTrade
from ..common import conventions as cv
from ..common.base_pricer import BasePricer
from ..common.pricing_result import PricingResult

_SWAP_TYPE = {
    SwapDirection.PAYER: ql.OvernightIndexedSwap.Payer,
    SwapDirection.RECEIVER: ql.OvernightIndexedSwap.Receiver,
}


def build_ois_swap(
    trade: OvernightIndexedSwapTrade,
    discount: ql.YieldTermStructureHandle,
    forecast: ql.YieldTermStructureHandle,
    fixed_rate: float | None = None,
) -> ql.OvernightIndexedSwap:
    """Construct the QL OIS (used by the pricer and by the swaption underlying)."""
    index = cv.to_overnight_index(trade.index, forecast)
    rate = trade.fixed_rate if fixed_rate is None else fixed_rate
    return ql.MakeOIS(
        cv.to_ql_period(trade.swap_tenor),
        index,
        rate,
        cv.to_ql_period(trade.forward_start),
        nominal=trade.notional,
        swapType=_SWAP_TYPE[trade.direction],
        paymentLag=trade.fixed_schedule.payment_lag,
        discountingTermStructure=discount,
    )


class OISSwapPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, OvernightIndexedSwapTrade)
        discount, forecast, _ = self._resolve_ois_curves(
            mds, trade.currency, trade.index.name
        )
        swap = build_ois_swap(trade, discount, forecast)

        pv = swap.NPV()
        fair_rate = swap.fairRate()
        fixed_bps = swap.legBPS(0)          # PV change per 1bp on the fixed leg
        # DV01 (per 1bp), sign-agnostic magnitude of the fixed annuity sensitivity
        dv01 = abs(fixed_bps)

        return PricingResult(
            trade_id=trade.trade_id,
            trade_type=trade.trade_type.value,
            currency=trade.currency,
            valuation_date=ctx.valuation_date,
            pv=pv,
            leg_pvs={"fixed": swap.legNPV(0), "float": swap.legNPV(1)},
            measures={
                "fair_rate": fair_rate,
                "fixed_rate": trade.fixed_rate,
                "dv01": dv01,
                "fixed_leg_bps": fixed_bps,
                "notional": trade.notional,
            },
            diagnostics={
                "engine": "DiscountingSwapEngine",
                "direction": trade.direction.value,
                "start_date": str(swap.startDate()),
                "maturity_date": str(swap.maturityDate()),
            },
        )
