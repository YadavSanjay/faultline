"""Build QuantLib yield curves from resolved domain market data.

`build_ois_curve` bootstraps a single-currency OIS curve from par-OIS quotes using
`OISRateHelper` — the dealer-standard construction. The helper conventions are
aligned with the SOFR OIS conventions used when pricing the swaps so the bootstrap
is self-consistent (a par swap reprices to ~par; see tests/test_curve_builder.py).
"""

from __future__ import annotations

import QuantLib as ql

from ...input.common.enums import CalendarCode
from ...input.common.market_data import ParOISCurveData, TreasuryCurveData
from . import conventions as cv
from .curve_cache import cached


def _ois_helpers(curve_data: ParOISCurveData) -> list:
    """Build OISRateHelper instruments shared by the curve bootstraps."""
    index_conv = curve_data.index
    calendar = cv.to_calendar(index_conv.calendar)
    overnight_index = cv.to_overnight_index(index_conv)
    helpers: list[ql.RateHelper] = []
    for tenor, rate in zip(curve_data.tenors, curve_data.par_rates, strict=True):
        quote = ql.QuoteHandle(ql.SimpleQuote(float(rate)))
        helpers.append(ql.OISRateHelper(
            index_conv.settlement_days, cv.to_ql_period(tenor), quote, overnight_index,
            ql.YieldTermStructureHandle(), True, 2, ql.ModifiedFollowing, ql.Annual, calendar,
        ))
    return helpers


def build_ois_curve(curve_data: ParOISCurveData,
                    interpolation: str = "logcubic") -> ql.YieldTermStructureHandle:
    """Bootstrap a SOFR OIS discount/forecast curve from par-OIS quotes.

    interpolation:
      "logcubic"    -> PiecewiseLogCubicDiscount (smooth; default for swaps/bonds/equity)
      "flatforward" -> PiecewiseFlatForward (required by IsdaCdsEngine, which only
                       accepts a flat-forward-interpolated discount curve)

    Memoized per run (P1): identical (curve_data, interpolation) is bootstrapped once.
    """
    return cached(
        ("ois", curve_data, interpolation),
        lambda: _build_ois_curve(curve_data, interpolation),
    )


def _build_ois_curve(curve_data: ParOISCurveData, interpolation: str) -> ql.YieldTermStructureHandle:
    ref_date = cv.to_ql_date(curve_data.as_of_date)
    helpers = _ois_helpers(curve_data)
    if interpolation == "flatforward":
        curve = ql.PiecewiseFlatForward(ref_date, helpers, ql.Actual365Fixed())
    else:
        curve = ql.PiecewiseLogCubicDiscount(ref_date, helpers, ql.Actual365Fixed())
    curve.enableExtrapolation()
    return ql.YieldTermStructureHandle(curve)


def build_treasury_curve(
    curve_data: TreasuryCurveData,
    settlement_days: int = 1,
    calendar_code: CalendarCode = CalendarCode.US_GOVERNMENT_BOND,
) -> ql.YieldTermStructureHandle:
    """Bootstrap a UST discount curve from par yields via par-bond helpers.

    Each par yield is the coupon of a par bond at that maturity, so par-bond
    helpers (priced at 100) reproduce the par curve exactly (validated: on-the-run
    par bonds reprice to ~100). ActualActual(Bond), semiannual, unadjusted — the
    standard Treasury conventions. Memoized per run (P1).
    """
    return cached(
        ("treasury", curve_data, settlement_days, calendar_code),
        lambda: _build_treasury_curve(curve_data, settlement_days, calendar_code),
    )


def _build_treasury_curve(
    curve_data: TreasuryCurveData,
    settlement_days: int,
    calendar_code: CalendarCode,
) -> ql.YieldTermStructureHandle:
    calendar = cv.to_calendar(calendar_code)
    dc = ql.ActualActual(ql.ActualActual.Bond)
    today = cv.to_ql_date(curve_data.as_of_date)
    settle = calendar.advance(today, settlement_days, ql.Days)

    helpers: list[ql.RateHelper] = []
    for tenor, yld in zip(curve_data.tenors, curve_data.par_yields, strict=True):
        schedule = ql.MakeSchedule(
            settle, calendar.advance(settle, cv.to_ql_period(tenor)),
            ql.Period(ql.Semiannual), calendar=calendar,
            convention=ql.Unadjusted, backwards=True,
        )
        helper = ql.FixedRateBondHelper(
            ql.QuoteHandle(ql.SimpleQuote(100.0)), settlement_days, 100.0,
            schedule, [float(yld)], dc, ql.Unadjusted,
        )
        helpers.append(helper)

    curve = ql.PiecewiseLogCubicDiscount(settle, helpers, dc)
    curve.enableExtrapolation()
    return ql.YieldTermStructureHandle(curve)


def build_zero_spreaded_curve(
    base: ql.YieldTermStructureHandle, spread: float
) -> ql.YieldTermStructureHandle:
    """Apply a flat continuous zero-spread over a base curve (issuer credit spread).

    Memoized per run (P1) on the base-curve identity + spread; the base handle comes
    from the cached `build_ois_curve`, so the same (issuer-spread, SOFR curve) pair is
    built once.
    """
    return cached(
        ("zspread", id(base), spread),
        lambda: ql.YieldTermStructureHandle(
            ql.ZeroSpreadedTermStructure(base, ql.QuoteHandle(ql.SimpleQuote(float(spread))))
        ),
    )


def build_zero_spreaded_curve_ts(
    base: ql.YieldTermStructureHandle,
    points: tuple[tuple[float, float], ...],
    ref_date: ql.Date,
) -> ql.YieldTermStructureHandle:
    """Apply a *term structure* of continuous zero-spreads over a base curve (A3).

    `points` is ascending ``(tenor_years, spread_decimal)``. The spread is linear in the
    zero rate between the quoted tenors and **flat-extrapolated** outside them: a today
    node (first spread) pins the front, and a far 50Y node (last spread) holds the long
    end flat past the last quoted tenor. Used for the corporate-bond issuer credit curve
    where the CDS feed gives >=2 tenors. Memoized per run on (base identity, points).
    """
    def _build() -> ql.YieldTermStructureHandle:
        dates = [ref_date]
        spreads = [points[0][1]]
        for ty, sp in points:
            dates.append(ref_date + ql.Period(round(ty * 12), ql.Months))
            spreads.append(sp)
        dates.append(ref_date + ql.Period(50, ql.Years))
        spreads.append(points[-1][1])
        quotes = [ql.QuoteHandle(ql.SimpleQuote(float(s))) for s in spreads]
        ts = ql.SpreadedLinearZeroInterpolatedTermStructure(base, quotes, dates)
        ts.enableExtrapolation()
        return ql.YieldTermStructureHandle(ts)

    return cached(("zspread_ts", id(base), points), _build)
