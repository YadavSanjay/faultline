"""Trades #23 / #24 — European and Bermudan swaptions on SOFR OIS.

European  : forward-starting OIS underlying + BachelierSwaptionEngine (normal vol).
Bermudan  : Hull-White calibrated to a *co-terminal* normal-vol swaption grid
            (JamshidianSwaptionEngine), priced on a TreeSwaptionEngine, with an
            FdHullWhiteSwaptionEngine cross-check recorded as a diagnostic.
"""

from __future__ import annotations

import QuantLib as ql

from ...input.common.conventions import IndexConvention
from ...input.common.enums import Frequency, SwapDirection
from ...input.common.market_data import MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ...input.rates.instruments import (
    BermudanSwaptionTrade,
    EuropeanSwaptionTrade,
    OvernightIndexedSwapTrade,
)
from ..common import conventions as cv
from ..common.base_pricer import BasePricer
from ..common.pricing_result import PricingResult
from .swap_pricer import build_ois_swap

_SWAP_TYPE = {
    SwapDirection.PAYER: ql.OvernightIndexedSwap.Payer,
    SwapDirection.RECEIVER: ql.OvernightIndexedSwap.Receiver,
}

_TREE_STEPS = 100


def _underlying_swap(
    *,
    trade_id: str,
    currency: str,
    notional: float,
    direction: SwapDirection,
    index: IndexConvention,
    swap_tenor: str,
    forward_start: str,
    strike: float | None,
    discount: ql.YieldTermStructureHandle,
    forecast: ql.YieldTermStructureHandle,
) -> tuple[ql.OvernightIndexedSwap, float]:
    """Build the forward-starting underlying OIS; resolve ATM strike if needed.

    Returns (swap_struck_at_used_rate, used_strike).
    """
    spec = OvernightIndexedSwapTrade(
        trade_id=trade_id, description="underlying", currency=currency,
        notional=notional, direction=direction, swap_tenor=swap_tenor,
        fixed_rate=0.0, forward_start=forward_start, index=index,
    )
    if strike is None:
        atm_swap = build_ois_swap(spec, discount, forecast, fixed_rate=0.03)
        strike = atm_swap.fairRate()
    swap = build_ois_swap(spec, discount, forecast, fixed_rate=strike)
    return swap, strike


class EuropeanSwaptionPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, EuropeanSwaptionTrade)
        discount, forecast, _ = self._resolve_ois_curves(mds, trade.currency, trade.index.name)
        vol_data = mds.normal_vol(trade.currency, trade.option_tenor)

        swap, strike = _underlying_swap(
            trade_id=trade.trade_id, currency=trade.currency, notional=trade.notional,
            direction=trade.direction, index=trade.index, swap_tenor=trade.swap_tenor,
            forward_start=trade.option_tenor, strike=trade.strike,
            discount=discount, forecast=forecast,
        )
        exercise = ql.EuropeanExercise(swap.startDate())
        settlement = (ql.Settlement.Physical if trade.settlement.value == "physical"
                      else ql.Settlement.Cash)
        swaption = ql.Swaption(swap, exercise, settlement)
        # C1: use the normal vol at THIS option's expiry from the term structure.
        vol_used = vol_data.vol_at(float(ql.Period(trade.option_tenor).length()))
        vol_q = ql.SimpleQuote(vol_used)
        swaption.setPricingEngine(ql.BachelierSwaptionEngine(discount, ql.QuoteHandle(vol_q)))

        pv = swaption.NPV()
        # vega per 1bp of normal vol (bump-and-revalue)
        vol_q.setValue(vol_used + 1e-4)
        vega_1bp = swaption.NPV() - pv
        vol_q.setValue(vol_used)

        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=pv,
            measures={
                "atm_or_strike": strike,
                "normal_vol_bp": vol_used * 1e4,
                "vega_1bp": vega_1bp,
                "notional": trade.notional,
                "underlying_fair_rate": swap.fairRate(),
            },
            diagnostics={
                "engine": "BachelierSwaptionEngine",
                "direction": trade.direction.value,
                "expiry_date": str(swap.startDate()),
                "underlying_maturity": str(swap.maturityDate()),
                "vol_anchor": vol_data.anchor,
            },
        )


class BermudanSwaptionPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, BermudanSwaptionTrade)
        discount, forecast, _ = self._resolve_ois_curves(mds, trade.currency, trade.index.name)
        vol_data = mds.normal_vol(trade.currency, trade.option_tenor)

        index = cv.to_overnight_index(trade.index, forecast)
        today = cv.to_ql_date(ctx.valuation_date)
        calendar = cv.to_calendar(trade.index.calendar)

        first_ex_y = ql.Period(trade.option_tenor).length()    # e.g. 2
        swap_len_y = ql.Period(trade.swap_tenor).length()      # e.g. 5
        final_y = first_ex_y + swap_len_y                      # co-terminal maturity

        # --- Hull-White co-terminal calibration -------------------------------
        model = ql.HullWhite(discount)
        dc = cv.to_day_count(trade.index.day_count)
        helpers = []
        vol_strip = []
        for exp in range(first_ex_y, final_y):                 # 2,3,4,5,6
            swap_tenor_y = final_y - exp                        # 5,4,3,2,1
            # C1: each co-terminal target carries the normal vol at ITS expiry (from the term
            # structure), so the calibration sees a real vol slope — which is what identifies the
            # Hull-White mean reversion — instead of one flat vol.
            vol_exp = vol_data.vol_at(float(exp))
            vol_strip.append(vol_exp)
            helper = ql.SwaptionHelper(
                ql.Period(exp, ql.Years), ql.Period(swap_tenor_y, ql.Years),
                ql.QuoteHandle(ql.SimpleQuote(vol_exp)), index, ql.Period(1, ql.Years), dc, dc, discount,
                ql.BlackCalibrationHelper.RelativePriceError, ql.nullDouble(), 1.0, ql.Normal,
            )
            helper.setPricingEngine(ql.JamshidianSwaptionEngine(model, discount))
            helpers.append(helper)
        model.calibrate(helpers, ql.LevenbergMarquardt(),
                        ql.EndCriteria(1000, 200, 1e-8, 1e-8, 1e-8))
        a, sigma = model.params()
        calib_err = sum((h.modelValue() - h.marketValue()) ** 2 for h in helpers) ** 0.5

        # --- underlying + Bermudan exercise schedule --------------------------
        swap, strike = _underlying_swap(
            trade_id=trade.trade_id, currency=trade.currency, notional=trade.notional,
            direction=trade.direction, index=trade.index, swap_tenor=trade.swap_tenor,
            forward_start=trade.option_tenor, strike=trade.strike,
            discount=discount, forecast=forecast,
        )
        step = {Frequency.ANNUAL: 1, Frequency.SEMIANNUAL: 0}.get(trade.exercise_frequency, 1)
        ex_dates = [calendar.advance(today, ql.Period(y, ql.Years))
                    for y in range(first_ex_y, final_y, step)]
        bermudan = ql.Swaption(swap, ql.BermudanExercise(ex_dates))

        bermudan.setPricingEngine(ql.TreeSwaptionEngine(model, _TREE_STEPS))
        pv = bermudan.NPV()
        # FD cross-check (accuracy guard)
        bermudan.setPricingEngine(ql.FdHullWhiteSwaptionEngine(model))
        pv_fd = bermudan.NPV()

        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=pv,
            measures={
                "atm_or_strike": strike,
                "normal_vol_bp": (sum(vol_strip) / len(vol_strip)) * 1e4,   # mean of the co-terminal strip
                "notional": trade.notional,
                "hw_mean_reversion": a,
                "hw_sigma": sigma,
                "pv_tree": pv,
                "pv_fd_crosscheck": pv_fd,
                "tree_fd_rel_diff": abs(pv - pv_fd) / pv if pv else 0.0,
                "calibration_rmse": calib_err,
                "n_exercise_dates": len(ex_dates),
            },
            diagnostics={
                "engine": f"TreeSwaptionEngine(steps={_TREE_STEPS}) + HullWhite co-terminal",
                "crosscheck_engine": "FdHullWhiteSwaptionEngine",
                "direction": trade.direction.value,
                "first_exercise": str(ex_dates[0]),
                "final_maturity": str(swap.maturityDate()),
                "vol_anchor": vol_data.anchor,
            },
        )
