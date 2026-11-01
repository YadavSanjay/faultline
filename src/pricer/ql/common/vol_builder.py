"""Build QuantLib volatility inputs from resolved domain market data.

Rates: a flat normal (Bachelier) vol level. Equity: a `BlackVarianceSurface`
(strike x expiry) built from the chain's implied-vol grid (B2), so the American
FD engine sees the local skew across the early-exercise region rather than a flat
per-leg vol. Kept behind small builder functions so callers stay engine-agnostic.
"""

from __future__ import annotations

import numpy as np
import QuantLib as ql

from ...input.common.market_data import EquityVolSurface, FlatNormalVolData


def flat_normal_simplequote(vol_data: FlatNormalVolData) -> ql.SimpleQuote:
    """Return the mutable SimpleQuote (so callers can bump it for vega)."""
    return ql.SimpleQuote(float(vol_data.vol))


def flat_normal_quote(vol_data: FlatNormalVolData) -> ql.QuoteHandle:
    """Return a QuoteHandle holding the flat normal vol (for Bachelier engines)."""
    return ql.QuoteHandle(flat_normal_simplequote(vol_data))


_MIN_IV = 1e-4


_MIN_EXPIRY_DAYS = 10        # drop the ultra-short, noisy front of the free chain
_MIN_EXPIRY_GAP = 20         # thin expiry nodes to ~monthly so local vol stays stable


def _expiry_grid(surface: EquityVolSurface) -> list[int]:
    """A sparse, smooth expiry-day grid for the vol surface.

    `FdBlackScholesVanillaEngine` derives a *local* volatility from the surface, so densely
    spaced, noisy short-dated chain expiries produce large forward-variance jumps that blow
    up the local vol and over-price short options. Every option structure in the book is
    single-expiry, so a fine term structure adds noise with no benefit: we drop expiries
    under ``_MIN_EXPIRY_DAYS`` and thin the rest to ~monthly spacing.
    """
    grid: list[int] = []
    for d in sorted({int(x) for x in surface.days_to_expiry if int(x) >= _MIN_EXPIRY_DAYS}):
        if not grid or d - grid[-1] >= _MIN_EXPIRY_GAP:
            grid.append(d)
    return grid


def build_equity_black_variance_surface(
    surface: EquityVolSurface, spot: float, today: ql.Date, calendar: ql.Calendar,
    dc: ql.DayCounter, *, extra_strikes: tuple[float, ...] = (), vol_shift: float = 0.0,
) -> ql.BlackVarianceSurface | None:
    """A rectangular `ql.BlackVarianceSurface` (strike x expiry) for an equity underlier.

    The chain grid is scattered (not every strike at every expiry), so we build a regular
    strike axis spanning ~[0.5, 1.5]*spot (plus the trade's own leg strikes as exact nodes)
    and a sparse, smooth expiry grid (see ``_expiry_grid``), and fill each cell from the
    skew/term-aware `EquityVolSurface.implied_vol`. `vol_shift` is added to every cell (a
    parallel vega bump). Returns None when the grid is too small to form a surface (caller
    falls back to a flat per-leg vol). Bilinear interpolation, flat-extrapolated off the grid.
    """
    days = _expiry_grid(surface)
    if len(days) < 2:
        return None
    obs = [float(k) for k in surface.strikes]
    lo = min([spot * 0.5, *obs, *extra_strikes])
    hi = max([spot * 1.5, *obs, *extra_strikes])
    grid = [*np.linspace(lo, hi, 21), *extra_strikes]
    strikes = sorted({round(float(k), 4) for k in grid if k > 0})
    if len(strikes) < 2:
        return None

    expiry_dates = [today + ql.Period(d, ql.Days) for d in days]
    t = np.array([d / 365.0 for d in days], dtype=float)
    vols = np.empty((len(strikes), len(days)), dtype=float)
    for i, k in enumerate(strikes):
        for j, d in enumerate(days):
            vols[i][j] = max(surface.implied_vol(d / 365.0, k) + vol_shift, _MIN_IV)
    # `BlackVarianceSurface` forbids calendar arbitrage: total variance sigma^2*t must be
    # non-decreasing in time at each strike. The delayed free chain occasionally violates
    # this (rich front-month vol), so clean it — lift any later variance up to the prior.
    var = vols ** 2 * t
    var = np.maximum.accumulate(var, axis=1)
    vols = np.sqrt(var / t)

    matrix = ql.Matrix(len(strikes), len(expiry_dates))
    for i in range(len(strikes)):
        for j in range(len(days)):
            matrix[i][j] = float(vols[i][j])

    vs = ql.BlackVarianceSurface(today, calendar, expiry_dates, strikes, matrix, dc)
    vs.setInterpolation("bilinear")
    vs.enableExtrapolation()
    return vs
