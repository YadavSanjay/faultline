"""CDS pricer (trades #28-30) — ISDA model with a calibrated hazard curve.

QuantLib 1.42 ships no `IsdaCdsHelper`, so we hand-roll a hazard bootstrap that stays
consistent with `IsdaCdsEngine`:

* **Flat hazard** (index CDX, thin single names): a 1-D root-find for the single hazard
  at which a par CDS (running coupon = market par spread) reprices to zero.
* **Piecewise-flat hazard curve** (A2, single names with >=2 clean tenors): a sequential
  secant bootstrap — walking the par spreads by ascending tenor, solve each segment's
  hazard so the par CDS to that tenor's standard maturity reprices to zero under the ISDA
  engine, holding the earlier (shorter) segments fixed. This gives a real term structure
  of default risk instead of assuming one flat hazard across all horizons.

The traded standardised CDS (100bp IG coupon) is priced on the calibrated curve; by
construction its fair spread equals the market par spread at its maturity. Reports NPV,
upfront points, fair spread, coupon/default leg NPVs, the calibrated hazard(s), survival
to maturity, the per-tenor hazard curve, and CS01 (1bp parallel spread bump, re-calibrated).
"""

from __future__ import annotations

import math
from collections.abc import Callable

import QuantLib as ql

from ...input.common.enums import CurvePurpose, ProtectionSide
from ...input.common.market_data import MarketDataSet
from ...input.common.trade import Trade
from ...input.common.valuation import ValuationContext
from ...input.credit.instruments import CreditDefaultSwapTrade
from ..common import conventions as cv
from ..common.base_pricer import BasePricer
from ..common.curve_builder import build_ois_curve
from ..common.pricing_result import PricingResult

_CAL = ql.WeekendsOnly()
_DC = ql.Actual360()
_SIDE = {ProtectionSide.BUY: ql.Protection.Buyer, ProtectionSide.SELL: ql.Protection.Seller}


def _standard_schedule(today: ql.Date, tenor: str) -> ql.Schedule:
    maturity = ql.cdsMaturity(today, ql.Period(tenor), ql.DateGeneration.CDS2015)
    return ql.MakeSchedule(today, maturity, ql.Period(ql.Quarterly), ql.Quarterly,
                           _CAL, ql.Following, ql.Unadjusted, ql.DateGeneration.CDS2015)


def calibrate_flat_hazard(
    spread: float, recovery: float, discount: ql.YieldTermStructureHandle,
    today: ql.Date, schedule: ql.Schedule,
) -> tuple[ql.DefaultProbabilityTermStructureHandle, float]:
    """Flat hazard so a par CDS (coupon = spread) has zero NPV under the ISDA engine.

    Returns (DefaultProbabilityTermStructureHandle, hazard_value).
    """
    quote = ql.SimpleQuote(spread / (1.0 - recovery))     # credit-triangle seed
    hz = ql.FlatHazardRate(today, ql.QuoteHandle(quote), ql.Actual365Fixed())
    prob = ql.DefaultProbabilityTermStructureHandle(hz)
    par = ql.CreditDefaultSwap(ql.Protection.Buyer, 1.0, spread, schedule, ql.Following, _DC)
    par.setPricingEngine(ql.IsdaCdsEngine(prob, recovery, discount))

    x0, x1 = quote.value() * 0.5, quote.value() * 1.5
    quote.setValue(x0)
    f0 = par.NPV()
    quote.setValue(x1)
    f1 = par.NPV()
    for _ in range(60):
        if abs(f1 - f0) < 1e-16:
            break
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        if x2 <= 0:
            x2 = 1e-7
        quote.setValue(x2)
        f2 = par.NPV()
        x0, f0, x1, f1 = x1, f1, x2, f2
        if abs(f2) < 1e-12:
            break
    return prob, quote.value()


def _secant_hazard(npv_at: Callable[[float], float], seed: float) -> float:
    """Root-find the segment hazard `h` with `npv_at(h) == 0` (secant, positivity-clamped)."""
    x0, x1 = seed * 0.5, seed * 1.5
    f0, f1 = npv_at(x0), npv_at(x1)
    xk = x1
    for _ in range(60):
        if abs(f1 - f0) < 1e-16:
            break
        x2 = x1 - f1 * (x1 - x0) / (f1 - f0)
        if x2 <= 0:
            x2 = 1e-7
        f2 = npv_at(x2)
        x0, f0, x1, f1 = x1, f1, x2, f2
        xk = x2
        if abs(f2) < 1e-12:
            break
    return xk


def calibrate_piecewise_hazard(
    points: list[tuple[float, float]], recovery: float,
    discount: ql.YieldTermStructureHandle, today: ql.Date,
) -> tuple[ql.DefaultProbabilityTermStructureHandle, list[dict]]:
    """Bootstrap a piecewise-flat hazard curve from ascending (tenor_years, spread) pairs.

    Sequential secant, ISDA-consistent: segment k's hazard is solved so a par CDS to the
    k-th tenor's standard maturity reprices to zero, with segments 1..k-1 held fixed. The
    survival curve is a BackwardFlat `HazardRateCurve` (constant forward hazard per bucket,
    extrapolated flat past the last node). Returns (prob_handle, nodes) where each node is
    {tenor, maturity, spread, hazard, survival}.
    """
    dc = ql.Actual365Fixed()
    tenors = [int(t) for t, _ in points]
    spreads = [s for _, s in points]
    mats = [ql.cdsMaturity(today, ql.Period(f"{t}Y"), ql.DateGeneration.CDS2015) for t in tenors]

    def build(hazards: list[float]) -> ql.HazardRateCurve:
        # BackwardFlat: hazard over (mats[i-1], mats[i]] == hazards[i]; dates[0]==ref.
        dates = [today, *mats[: len(hazards)]]
        hvals = [hazards[0], *hazards]
        curve = ql.HazardRateCurve(dates, hvals, dc)
        curve.enableExtrapolation()
        return curve

    hz: list[float] = []
    for k in range(len(tenors)):
        sched = _standard_schedule(today, f"{tenors[k]}Y")
        par = ql.CreditDefaultSwap(ql.Protection.Buyer, 1.0, spreads[k], sched, ql.Following, _DC)

        def npv_at(h: float, _par: ql.CreditDefaultSwap = par, _hz: list[float] = hz) -> float:
            curve = build([*_hz, h])
            _par.setPricingEngine(
                ql.IsdaCdsEngine(ql.DefaultProbabilityTermStructureHandle(curve), recovery, discount))
            return float(_par.NPV())

        hz.append(_secant_hazard(npv_at, spreads[k] / (1.0 - recovery)))

    curve = build(hz)
    prob = ql.DefaultProbabilityTermStructureHandle(curve)
    nodes = [
        {"tenor": f"{tenors[k]}Y", "maturity": str(mats[k]), "spread": spreads[k],
         "hazard": hz[k], "survival": curve.survivalProbability(mats[k])}
        for k in range(len(tenors))
    ]
    return prob, nodes


class CdsPricer(BasePricer):
    def _price(self, trade: Trade, mds: MarketDataSet, ctx: ValuationContext) -> PricingResult:
        assert isinstance(trade, CreditDefaultSwapTrade)
        # IsdaCdsEngine only accepts a flat-forward-interpolated discount curve
        discount = build_ois_curve(
            mds.curve(trade.currency, CurvePurpose.DISCOUNT, trade.index.name),
            interpolation="flatforward",
        )
        cs = mds.cds_spread(trade.currency, trade.reference)
        recovery = cs.recovery if cs.recovery is not None else trade.recovery

        today = cv.to_ql_date(ctx.valuation_date)
        schedule = _standard_schedule(today, trade.tenor)

        # B4: an index (CDX.NA.IG) is priced at the INDEX LEVEL — a flat hazard calibrated to
        # the quoted index spread with the standard fixed coupon, on the OUTSTANDING notional
        # (traded notional x index_factor; the factor drops below 1 as constituents default).
        # Untranched, so there is no constituent-curve aggregation. A2's piecewise curve is
        # single-name-only, so an index always takes the flat-hazard path.
        is_index = trade.kind.value == "index"
        notional = trade.notional * cs.index_factor
        points = list(cs.curve_points)
        use_curve = len(points) >= 2
        if use_curve:
            prob, _nodes = calibrate_piecewise_hazard(points, recovery, discount, today)
            engine_str = f"IsdaCdsEngine + piecewise hazard curve ({len(_nodes)} tenors)"
        elif is_index:
            prob, _flat = calibrate_flat_hazard(cs.spread, recovery, discount, today, schedule)
            engine_str = "IsdaCdsEngine + index-level flat hazard (untranched)"
        else:
            prob, _flat = calibrate_flat_hazard(cs.spread, recovery, discount, today, schedule)
            engine_str = "IsdaCdsEngine + calibrated flat hazard"

        cds = ql.CreditDefaultSwap(_SIDE[trade.protection], notional,
                                   trade.running_coupon, schedule, ql.Following, _DC)
        cds.setPricingEngine(ql.IsdaCdsEngine(prob, recovery, discount))
        pv = cds.NPV()

        # CS01: parallel +1bp on every calibrated par spread, re-bootstrapped, re-priced
        if use_curve:
            prob_b, _ = calibrate_piecewise_hazard([(t, s + 1e-4) for t, s in points],
                                                   recovery, discount, today)
        else:
            prob_b, _ = calibrate_flat_hazard(cs.spread + 1e-4, recovery, discount, today, schedule)
        cds_b = ql.CreditDefaultSwap(_SIDE[trade.protection], notional,
                                     trade.running_coupon, schedule, ql.Following, _DC)
        cds_b.setPricingEngine(ql.IsdaCdsEngine(prob_b, recovery, discount))
        cs01 = cds_b.NPV() - pv

        # rec01 (B5): PV change for +1pt recovery, with the hazard RE-CALIBRATED to the same
        # market spread(s). Holding the spread fixed isolates the recovery assumption's effect
        # (higher recovery lowers loss-given-default but needs a higher hazard to reprice the
        # same par spread — the two partly offset). This is the standard recovery-risk measure.
        rec_bump = min(recovery + 0.01, 0.99)
        if use_curve:
            prob_r, _ = calibrate_piecewise_hazard(points, rec_bump, discount, today)
        else:
            prob_r, _ = calibrate_flat_hazard(cs.spread, rec_bump, discount, today, schedule)
        cds_r = ql.CreditDefaultSwap(_SIDE[trade.protection], notional,
                                     trade.running_coupon, schedule, ql.Following, _DC)
        cds_r.setPricingEngine(ql.IsdaCdsEngine(prob_r, rec_bump, discount))
        rec01 = cds_r.NPV() - pv

        maturity = schedule.dates()[-1]
        # Headline hazard = survival-implied average to maturity (-ln S(T)/T): a single
        # interpretable number that matches the flat hazard in the flat case and summarises
        # the term structure in the piecewise case (the per-segment forward hazards live in
        # the lineage hazard-curve table).
        surv_mat = prob.survivalProbability(maturity)
        tau_mat = ql.Actual365Fixed().yearFraction(today, maturity)
        hazard = -math.log(surv_mat) / tau_mat if surv_mat > 0 and tau_mat > 0 else 0.0
        return PricingResult(
            trade_id=trade.trade_id, trade_type=trade.trade_type.value,
            currency=trade.currency, valuation_date=ctx.valuation_date, pv=pv,
            leg_pvs={"coupon": cds.couponLegNPV(), "default": cds.defaultLegNPV()},
            measures={
                "par_spread_bp": cs.spread * 1e4,
                "fair_spread_bp": cds.fairSpread() * 1e4,
                "running_coupon_bp": trade.running_coupon * 1e4,
                "upfront_pts": pv / notional * 100.0 if notional else 0.0,
                "cs01": cs01,
                "rec01": rec01,          # PV change per +1pt recovery (spread held constant)
                "hazard_bp": hazard * 1e4,
                "survival_to_maturity": surv_mat,
                "recovery": recovery,
                "notional": notional,                        # outstanding = traded x index_factor
                "curve_tenors": float(len(points)) if use_curve else 1.0,
                **({"index_factor": cs.index_factor,
                    "n_constituents": float(cs.n_constituents or 0)} if is_index else {}),
            },
            diagnostics={
                "engine": engine_str,
                "reference": trade.reference,
                "kind": trade.kind.value,
                "protection": trade.protection.value,
                "maturity": str(maturity),
                "spread_source": cs.source,
                "spread_from_dtcc": str(cs.from_dtcc),
                "hazard_curve": ("piecewise" if use_curve else "flat"),
                **({"index_model": "untranched index level; flat hazard from the quoted index "
                    "spread, standard fixed coupon + upfront; no constituent/tranche granularity",
                    "index_factor": f"{cs.index_factor:g}"} if is_index else {}),
            },
        )
