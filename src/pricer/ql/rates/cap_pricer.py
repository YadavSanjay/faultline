"""Trade #25 — SOFR cap, priced with a flat normal (Bachelier) vol.

The cap is modelled as a strip of caplets on the SOFR index over `cap_tenor` at
`caplet_frequency`. Overnight caplets are approximated here as forward-rate
caplets (the standard QL CapFloor construction); a compounded-overnight caplet
refinement can replace `IborLeg` later without changing this interface.
"""

from __future__ import annotations

import QuantLib as ql

from ...input.common.market_data import MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ...input.rates.instruments import CapTrade
from ..common import conventions as cv
from ..common.base_pricer import BasePricer
from ..common.pricing_result import PricingResult


class CapPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, CapTrade)
        discount, forecast, _ = self._resolve_ois_curves(mds, trade.currency, trade.index.name)
        vol_data = mds.normal_vol(trade.currency, trade.cap_tenor)

        index = cv.to_overnight_index(trade.index, forecast)
        today = cv.to_ql_date(ctx.valuation_date)
        calendar = cv.to_calendar(trade.index.calendar)
        spot = calendar.advance(today, trade.index.settlement_days, ql.Days)
        end = calendar.advance(spot, cv.to_ql_period(trade.cap_tenor))
        period = cv.to_period(trade.caplet_frequency)

        schedule = ql.MakeSchedule(
            spot, end, period, calendar=calendar,
            convention=cv.to_bdc(trade.leg_schedule.business_day_convention),
        )
        leg = ql.IborLeg([trade.notional], schedule, index)

        # ATM strike = forward par rate of the cap leg
        disc_ts = discount.currentLink()
        if trade.strike is None:
            atm_probe = ql.Cap(leg, [0.03])
            strike = atm_probe.atmRate(disc_ts)
        else:
            strike = trade.strike

        cap = ql.Cap(leg, [strike])
        # C1: normal vol at the cap's expiry (its tenor) from the term structure.
        cap_p = cv.to_ql_period(trade.cap_tenor)
        cap_years = cap_p.length() / 12.0 if cap_p.units() == ql.Months else float(cap_p.length())
        vol_used = vol_data.vol_at(cap_years)
        vol_q = ql.SimpleQuote(vol_used)
        cap.setPricingEngine(ql.BachelierCapFloorEngine(discount, ql.QuoteHandle(vol_q)))

        pv = cap.NPV()
        vol_q.setValue(vol_used + 1e-4)
        vega_1bp = cap.NPV() - pv
        vol_q.setValue(vol_used)

        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=pv,
            measures={
                "atm_or_strike": strike,
                "normal_vol_bp": vol_used * 1e4,
                "vega_1bp": vega_1bp,
                "notional": trade.notional,
                "n_caplets": len(leg),
            },
            diagnostics={
                "engine": "BachelierCapFloorEngine",
                "start_date": str(spot),
                "end_date": str(end),
                "vol_anchor": vol_data.anchor,
                "caplet_note": "overnight caplets approximated as forward-rate caplets",
            },
        )
