"""Curve-bootstrap accuracy: a par OIS swap must reprice to par.

This is the single most important correctness guard for the rates book — if the
bootstrap is self-consistent, every downstream PV inherits a correct curve.
"""

from __future__ import annotations

import QuantLib as ql
from conftest import PAR_RATES, PAR_TENORS, VALUATION_DATE
from src.pricer.ql.common import conventions as cv
from src.pricer.ql.common.curve_builder import build_ois_curve


def test_par_swap_reprices_to_par(curve_data):
    """For every bootstrap tenor, a spot-starting OIS struck at the input par
    rate must have ~zero NPV and a fair rate equal to the input."""
    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    handle = build_ois_curve(curve_data)
    index = cv.to_overnight_index(curve_data.index, handle)

    for tenor, par in zip(PAR_TENORS, PAR_RATES, strict=True):
        swap = ql.MakeOIS(
            cv.to_ql_period(tenor), index, par, ql.Period("0D"),
            nominal=10_000_000, discountingTermStructure=handle, paymentLag=2,
        )
        # fair rate matches the input par rate to <0.1bp
        assert abs(swap.fairRate() - par) < 1e-5, f"{tenor}: {swap.fairRate()} vs {par}"
        # NPV is ~0 relative to notional
        assert abs(swap.NPV()) < 1.0, f"{tenor}: NPV={swap.NPV()}"


def test_curve_discount_factors_monotonic(curve_data):
    ql.Settings.instance().evaluationDate = cv.to_ql_date(VALUATION_DATE)
    handle = build_ois_curve(curve_data)
    ts = handle.currentLink()
    prev = 1.0
    for years in range(1, 11):
        d = cv.to_calendar(curve_data.index.calendar).advance(
            cv.to_ql_date(VALUATION_DATE), ql.Period(years, ql.Years))
        df = ts.discount(d)
        assert 0.0 < df < prev, f"DF not monotonic decreasing at {years}Y: {df}"
        prev = df
