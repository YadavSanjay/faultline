"""FX pricers (trades #26-27) — discount-factor valuation.

USD legs discount on the SOFR curve; foreign legs on a flat foreign curve
(JPY TONAR / TWD CBC rate). Both produce a USD PV and an FX delta (1% spot bump).

  * FX forward: PV_USD = receive_leg(DF) - pay_leg(DF) converted at the current spot.
    The implied fair forward = spot x DF_usd / DF_foreign (covered interest parity).
  * Cross-currency swap: fixed-for-fixed with notional exchange; only future
    cashflows (+ final notional) are valued; the foreign leg is converted to USD at
    the current spot. Net spot risk drives the MtM.
"""

from __future__ import annotations

import QuantLib as ql

from ...input.common.enums import CurvePurpose, FxDirection
from ...input.common.market_data import MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ...input.fx.instruments import CrossCurrencySwapTrade, FxForwardTrade
from ..common import conventions as cv
from ..common.base_pricer import BasePricer
from ..common.curve_builder import build_ois_curve
from ..common.pricing_result import PricingResult

_DC = ql.Actual360()
_CAL = ql.UnitedStates(ql.UnitedStates.Settlement)


def _foreign_curve(today: ql.Date, rate: float) -> ql.YieldTermStructure:
    return ql.FlatForward(today, rate, ql.Actual365Fixed())


class FxForwardPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, FxForwardTrade)
        usd = build_ois_curve(mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name)).currentLink()
        fx = mds.fx(trade.foreign_ccy)
        today = cv.to_ql_date(ctx.valuation_date)
        mat = cv.to_ql_date(trade.maturity_date)

        foreign_rate = fx.effective_foreign_rate       # C2: incl. xccy basis (0 by default)

        def pv_for_spot(spot: float) -> float:
            return self._forward_pv(trade, usd, today, mat, foreign_rate, spot)

        pv = pv_for_spot(fx.spot)
        fx_delta = pv_for_spot(fx.spot * 1.01) - pv      # PV change for +1% in foreign-per-USD

        df_usd = usd.discount(mat)
        df_for = _foreign_curve(today, foreign_rate).discount(mat)
        fair_forward = fx.spot * df_usd / df_for

        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=pv,
            measures={
                "spot": fx.spot,
                "strike": trade.strike,
                "fair_forward": fair_forward,
                "fx_delta_1pct": fx_delta,
                "usd_notional": trade.usd_notional,
                "foreign_rate": foreign_rate,
                "xccy_basis_bp": fx.xccy_basis_bp,
            },
            diagnostics={
                "engine": "discount-factor FX forward (CIP)",
                "pair": f"USD/{trade.foreign_ccy}",
                "direction": trade.direction.value,
                "maturity": str(mat),
                "fx_source": fx.source,
            },
        )

    @staticmethod
    def _forward_pv(trade: FxForwardTrade, usd: ql.YieldTermStructure, today: ql.Date,
                    mat: ql.Date, foreign_rate: float, spot: float) -> float:
        if mat <= today:
            return 0.0
        df_usd = usd.discount(mat)
        df_for = _foreign_curve(today, foreign_rate).discount(mat)
        usd_per_foreign = 1.0 / spot
        # LONG_USD: receive USD notional, pay (notional * strike) foreign
        recv_usd = trade.usd_notional * df_usd
        pay_foreign_in_usd = trade.usd_notional * trade.strike * df_for * usd_per_foreign
        pv = recv_usd - pay_foreign_in_usd
        return float(pv if trade.direction == FxDirection.LONG_USD else -pv)


class CrossCurrencySwapPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, CrossCurrencySwapTrade)
        usd = build_ois_curve(mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name)).currentLink()
        fx = mds.fx(trade.foreign_ccy)
        today = cv.to_ql_date(ctx.valuation_date)

        schedule = ql.MakeSchedule(
            cv.to_ql_date(trade.start_date), cv.to_ql_date(trade.maturity_date),
            ql.Period(trade.frequency_months, ql.Months), calendar=_CAL,
            convention=ql.ModifiedFollowing, backwards=True,
        )
        dates = list(schedule.dates())

        pv_usd_leg = self._leg_pv(usd, today, dates, trade.usd_notional, trade.usd_fixed_rate)
        foreign = _foreign_curve(today, fx.effective_foreign_rate)   # C2: incl. xccy basis (0 default)
        pv_for_leg_foreign = self._leg_pv(foreign, today, dates, trade.foreign_notional, trade.foreign_fixed_rate)

        def pv_for_spot(spot: float) -> float:
            net = pv_usd_leg - pv_for_leg_foreign / spot     # receive USD, pay foreign (LONG_USD)
            return net if trade.direction == FxDirection.LONG_USD else -net

        pv = pv_for_spot(fx.spot)
        fx_delta = pv_for_spot(fx.spot * 1.01) - pv

        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=pv,
            leg_pvs={"usd_leg": pv_usd_leg, "foreign_leg_usd": pv_for_leg_foreign / fx.spot},
            measures={
                "spot": fx.spot,
                "contract_fx": trade.contract_fx,
                "fx_delta_1pct": fx_delta,
                "usd_notional": trade.usd_notional,
                "foreign_notional": trade.foreign_notional,
                "foreign_rate": fx.effective_foreign_rate,
                "xccy_basis_bp": fx.xccy_basis_bp,
            },
            diagnostics={
                "engine": "fixed-for-fixed CCS, DF valuation + notional exchange",
                "pair": f"USD/{trade.foreign_ccy}",
                "direction": trade.direction.value,
                "maturity": str(cv.to_ql_date(trade.maturity_date)),
                "fx_source": fx.source,
            },
        )

    @staticmethod
    def _leg_pv(curve: ql.YieldTermStructure, today: ql.Date, dates: list,
                notional: float, fixed_rate: float) -> float:
        """PV (in leg ccy) of future fixed coupons + final notional redemption."""
        pv = 0.0
        maturity = dates[-1]
        for i in range(1, len(dates)):
            start, end = dates[i - 1], dates[i]
            if end <= today:
                continue
            tau = _DC.yearFraction(start, end)
            pv += notional * fixed_rate * tau * curve.discount(end)
        pv += notional * curve.discount(maturity)
        return float(pv)
