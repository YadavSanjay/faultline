"""Listed equity-option pricer (trades #7-14).

Each leg gets its own Black-Scholes-Merton process with a **skew-aware** flat vol
read from the chain at that leg's (expiry, strike), so spreads/straddles/collars
price off the correct smile points. European legs use `AnalyticEuropeanEngine`;
American legs use `FdBlackScholesVanillaEngine` (accurate early exercise with
dividends). The risk-free term structure is the SOFR curve; the dividend yield is
the chain-implied continuous yield.

Trade value = sum over legs of quantity x contract_multiplier x per-share value.
Greeks: per-leg analytic delta/gamma (summed); vega by a 1-vol-point bump.
"""

from __future__ import annotations

import math

import QuantLib as ql

from ...input.common.enums import CurvePurpose, ExerciseStyle, OptionRight
from ...input.common.market_data import EquityMarketData, MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ...input.equity.instruments import EquityOptionTrade
from ..common import conventions as cv
from ..common.base_pricer import BasePricer
from ..common.curve_builder import build_ois_curve
from ..common.pricing_result import PricingResult

_DC = ql.Actual365Fixed()
_FD_T_GRID = 100
_FD_X_GRID = 100
_RIGHT = {OptionRight.CALL: ql.Option.Call, OptionRight.PUT: ql.Option.Put}


class EquityOptionPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, EquityOptionTrade)
        discount = build_ois_curve(mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name))
        eq = mds.equity_market(trade.currency, trade.underlier)
        today = cv.to_ql_date(ctx.valuation_date)

        total, delta, gamma, leg_pvs, leg_vols, discrete_legs = self._price_legs(trade, eq, discount, today)
        # vega: trade-level +1 vol-point (0.01) bump
        bumped, *_ = self._price_legs(trade, eq, discount, today, vol_shift=0.01)
        vega = bumped - total

        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=total,
            leg_pvs=leg_pvs,
            measures={
                "delta_shares": delta,           # d(PV)/d(spot), in shares
                "delta_cash": delta * eq.spot,   # cash delta
                "gamma": gamma,
                "vega_1volpt": vega,
                "spot": eq.spot,
                "dividend_yield": eq.dividend_yield,
                "discrete_dividend_legs": float(discrete_legs),
                **{f"vol_{k}": v for k, v in leg_vols.items()},
            },
            diagnostics={
                "underlier": trade.underlier,
                "structure": trade.structure,
                "n_legs": str(len(trade.legs)),
                "engines": "AnalyticEuropean (Eur) / FdBlackScholes (Amer)",
                "vol_model": ("per-leg skew- and term-aware IV from the chain surface "
                              "(full surface shown for transparency; local-vol FD not used)"),
                "dividends": ("discrete (forward-preserving) on American legs"
                              if discrete_legs else "continuous chain-implied yield"),
                "spot_div_source": eq.source,
            },
        )

    def _price_legs(self, trade: EquityOptionTrade, eq: EquityMarketData,
                    discount: ql.YieldTermStructureHandle, today: ql.Date,
                    vol_shift: float = 0.0) -> tuple[float, float, float, dict[str, float], dict[str, float], int]:
        cal = ql.UnitedStates(ql.UnitedStates.NYSE)
        cont_div_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, eq.dividend_yield, _DC))
        zero_div_ts = ql.YieldTermStructureHandle(ql.FlatForward(today, 0.0, _DC))
        spot_q = ql.QuoteHandle(ql.SimpleQuote(eq.spot))

        total = delta = gamma = 0.0
        discrete_legs = 0
        leg_pvs: dict[str, float] = {}
        leg_vols: dict[str, float] = {}

        for i, leg in enumerate(trade.legs):
            expiry = cv.to_ql_date(leg.expiry_date)
            t_years = _DC.yearFraction(today, expiry)
            if t_years <= 0:
                continue
            # B2: each leg prices at its OWN (expiry, strike) implied vol read from the
            # skew- and term-aware surface — so a spread/collar/straddle already values each
            # leg on the correct smile point. We build & surface the full BlackVarianceSurface
            # for transparency (UI heatmap), but deliberately do NOT feed it to the FD engine
            # as a local-vol surface: Dupire local vol from the sparse/noisy free chain is
            # unstable and over-prices short-dated options. See the methodology doc.
            vol = max(eq.vol_surface.implied_vol(t_years, leg.strike) + vol_shift, 1e-4)
            vol_ts = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, cal, vol, _DC))
            payoff = ql.PlainVanillaPayoff(_RIGHT[leg.right], leg.strike)

            # B1: American legs on a dividend payer price with a discrete ex-dividend
            # schedule (correct early-exercise timing) whose amounts are scaled so the
            # chain-implied forward is preserved (drift q=0, dividends explicit). Every
            # other leg keeps the chain-implied continuous yield.
            is_european = leg.exercise_style == ExerciseStyle.EUROPEAN
            schedule = (self._discrete_schedule(eq, discount, today, expiry, t_years)
                        if not is_european else None)
            if schedule is not None:
                process = ql.BlackScholesMertonProcess(spot_q, zero_div_ts, discount, vol_ts)
                option = ql.VanillaOption(payoff, ql.AmericanExercise(today, expiry))
                option.setPricingEngine(
                    ql.FdBlackScholesVanillaEngine(process, schedule, _FD_T_GRID, _FD_X_GRID))
                discrete_legs += 1
            else:
                process = ql.BlackScholesMertonProcess(spot_q, cont_div_ts, discount, vol_ts)
                if is_european:
                    option = ql.VanillaOption(payoff, ql.EuropeanExercise(expiry))
                    option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
                else:
                    option = ql.VanillaOption(payoff, ql.AmericanExercise(today, expiry))
                    option.setPricingEngine(ql.FdBlackScholesVanillaEngine(process, _FD_T_GRID, _FD_X_GRID))

            qty = leg.quantity * trade.contract_multiplier
            per_share = option.NPV()
            total += qty * per_share
            delta += qty * option.delta()
            gamma += qty * option.gamma()
            tag = f"leg{i}_{leg.right.value}_{leg.strike:g}"
            leg_pvs[tag] = qty * per_share
            leg_vols[tag] = vol - vol_shift

        return total, delta, gamma, leg_pvs, leg_vols, discrete_legs

    def _discrete_schedule(self, eq: EquityMarketData, discount: ql.YieldTermStructureHandle,
                           today: ql.Date, expiry: ql.Date, t_years: float) -> ql.DividendSchedule | None:
        """Forward-preserving discrete ex-dividend schedule for an American leg, or None
        when the underlier is a non-payer / has no ex-date in the leg's life / q<=0.

        The projected ex-dates are real (from the dividend feed); the amounts are scaled by
        a single factor so their discounted sum equals the chain-implied dividend value to
        expiry, ``S*(1-exp(-q*T))``. This keeps the option's forward (hence its European-
        equivalent value) identical to the continuous-yield model, so only the American
        early-exercise timing around each ex-date changes.
        """
        if not eq.dividends or eq.dividend_yield <= 0.0:
            return None
        in_life = [(cv.to_ql_date(d), amt) for d, amt in eq.dividends
                   if today < cv.to_ql_date(d) <= expiry]
        if not in_life:
            return None
        raw_pv = sum(amt * discount.discount(d) for d, amt in in_life)
        if raw_pv <= 0.0:
            return None
        target_pv = eq.spot * (1.0 - math.exp(-eq.dividend_yield * t_years))   # chain-implied div value to T
        scale = target_pv / raw_pv
        schedule = ql.DividendSchedule()
        for d, amt in in_life:
            schedule.push_back(ql.FixedDividend(amt * scale, d))
        return schedule
