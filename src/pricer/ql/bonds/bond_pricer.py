"""Fixed-rate bond pricers (trades #15-21).

Corporate (#15-19): discount on a ZeroSpreadedTermStructure = SOFR curve + issuer
credit spread. Treasury (#20-21): discount on the bootstrapped UST par curve.

Both report clean/dirty price, accrued, YTM, modified duration, convexity and a
currency DV01; corporates additionally report the recovered Z-spread and whether
the credit spread came from CDS data or a configured fallback.
"""

from __future__ import annotations

import abc

import QuantLib as ql

from ...input.bonds.instruments import (
    CorporateBondTrade,
    FixedRateBondTrade,
    TreasuryBondTrade,
)
from ...input.common.enums import CurvePurpose
from ...input.common.market_data import MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ..common import conventions as cv
from ..common.base_pricer import BasePricer
from ..common.curve_builder import (
    build_ois_curve,
    build_treasury_curve,
    build_zero_spreaded_curve,
    build_zero_spreaded_curve_ts,
)
from ..common.pricing_result import PricingResult


def build_fixed_rate_bond(trade: FixedRateBondTrade) -> ql.FixedRateBond:
    """Construct the QL FixedRateBond (face amount = trade notional)."""
    calendar = cv.to_calendar(trade.calendar)
    schedule = ql.MakeSchedule(
        cv.to_ql_date(trade.issue_date),
        cv.to_ql_date(trade.maturity_date),
        cv.to_period(trade.coupon_frequency),
        calendar=calendar,
        convention=cv.to_bdc(trade.business_day_convention),
        terminalDateConvention=cv.to_bdc(trade.business_day_convention),
        backwards=True,
    )
    return ql.FixedRateBond(
        trade.settlement_days,
        trade.notional,                      # face amount -> NPV in currency
        schedule,
        [trade.coupon],
        cv.to_day_count(trade.day_count),
        cv.to_bdc(trade.business_day_convention),
        100.0,                               # redemption (% of face)
        cv.to_ql_date(trade.issue_date),
    )


class _BondPricerBase(BasePricer):
    """Shared bond build + standard analytics; subclasses supply the discount curve."""

    @abc.abstractmethod
    def _discount(self, trade: Trade, mds: MarketDataSet) -> tuple[ql.YieldTermStructureHandle, dict, dict]:
        """Return (discount_handle, extra_measures, extra_diagnostics)."""
        raise NotImplementedError

    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, FixedRateBondTrade)
        discount, extra_measures, extra_diag = self._discount(trade, mds)

        bond = build_fixed_rate_bond(trade)
        bond.setPricingEngine(ql.DiscountingBondEngine(discount))

        extrapolation_warning = None
        maturity_ql = cv.to_ql_date(trade.maturity_date)
        if maturity_ql > discount.maxDate():
            extrapolation_warning = (
                f"{trade.trade_id}: maturity {trade.maturity_date} is beyond the "
                f"discount curve's last node {cv.from_ql_date(discount.maxDate())}; "
                f"long-end cashflows discounted by flat-forward extrapolation."
            )

        dc = cv.to_day_count(trade.day_count)
        comp, freq = ql.Compounded, cv.to_frequency(trade.coupon_frequency)

        clean = bond.cleanPrice()
        dirty = bond.dirtyPrice()
        accrued = bond.accruedAmount()
        pv = bond.NPV()                       # dirty value in currency
        ytm = bond.bondYield(dc, comp, freq)
        mod_dur = ql.BondFunctions.duration(bond, ytm, dc, comp, freq, ql.Duration.Modified)
        convexity = ql.BondFunctions.convexity(bond, ytm, dc, comp, freq)
        # currency DV01 = modified duration * dirty market value * 1bp
        dirty_value = dirty / 100.0 * trade.notional
        dv01 = mod_dur * dirty_value * 1e-4

        measures = {
            "clean_price": clean,
            "dirty_price": dirty,
            "accrued": accrued,
            "ytm": ytm,
            "mod_duration": mod_dur,
            "convexity": convexity,
            "dv01": dv01,
            "notional": trade.notional,
            **extra_measures,
        }
        diagnostics = {
            "engine": "DiscountingBondEngine",
            "coupon": f"{trade.coupon*100:.4f}%",
            "maturity": trade.maturity_date.isoformat(),
            "day_count": trade.day_count.value,
            "cusip": trade.cusip or "-",
            **extra_diag,
        }
        result = PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=pv,
            measures=measures, diagnostics=diagnostics,
        )
        if extrapolation_warning:
            result.warnings.append(extrapolation_warning)
            result.diagnostics["long_end_extrapolation"] = "yes"
        return result


class CorporateBondPricer(_BondPricerBase):
    def _discount(self, trade: Trade, mds: MarketDataSet) -> tuple[ql.YieldTermStructureHandle, dict, dict]:
        assert isinstance(trade, CorporateBondTrade)
        sofr = build_ois_curve(mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name))
        cs = mds.credit_spread(trade.currency, trade.issuer)

        # A3: discount on a Z-spread *term structure* when the issuer CDS feed gives >=2
        # tenors (currently ORCL); otherwise a single flat Z-spread (backward compatible).
        use_curve = len(cs.curve_points) >= 2
        if use_curve:
            ref_date = sofr.currentLink().referenceDate()
            risky = build_zero_spreaded_curve_ts(sofr, cs.curve_points, ref_date)
            discount_desc = f"SpreadedLinearZero(SOFR, {len(cs.curve_points)}-tenor issuer Z-spread)"
        else:
            risky = build_zero_spreaded_curve(sofr, cs.spread)
            discount_desc = "ZeroSpreadedTermStructure(SOFR, issuer_spread)"

        # recover the Z-spread implied by the model clean price vs the SOFR curve
        bond = build_fixed_rate_bond(trade)
        bond.setPricingEngine(ql.DiscountingBondEngine(risky))
        dc = cv.to_day_count(trade.day_count)
        zspr = ql.BondFunctions.zSpread(
            bond, ql.BondPrice(bond.cleanPrice(), ql.BondPrice.Clean),
            sofr.currentLink(), dc, ql.Compounded, cv.to_frequency(trade.coupon_frequency),
        )
        measures = {
            "credit_spread_bp": cs.spread * 1e4,
            "z_spread_bp": zspr * 1e4,          # single equivalent Z-spread to maturity
            "recovery": cs.recovery,
            "credit_curve_tenors": float(len(cs.curve_points)) if use_curve else 1.0,
        }
        diag = {
            "issuer": trade.issuer,
            "discount": discount_desc,
            "credit_spread_source": cs.source,
            "credit_spread_from_cds": str(cs.from_cds),
            "credit_spread_curve": ("term_structure" if use_curve else "flat"),
        }
        return risky, measures, diag


class TreasuryBondPricer(_BondPricerBase):
    def _discount(self, trade: Trade, mds: MarketDataSet) -> tuple[ql.YieldTermStructureHandle, dict, dict]:
        assert isinstance(trade, TreasuryBondTrade)
        tcurve_data = mds.treasury_curve(trade.currency)
        handle = build_treasury_curve(
            tcurve_data, settlement_days=trade.settlement_days, calendar_code=trade.calendar
        )
        return handle, {}, {"discount": "UST par-curve bootstrap", "term": trade.term}


def _disc() -> CurvePurpose:
    return CurvePurpose.DISCOUNT
