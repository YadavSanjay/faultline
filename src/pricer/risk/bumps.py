"""Market-data perturbation engine (P2).

A *transform* is a pure `item -> item` function that perturbs one kind of resolved
market-data object (a frozen dataclass) and leaves everything else untouched.
`bumped(mds, *transforms)` returns a NEW `MarketDataSet` with every item passed through
the transforms — the base set is never mutated, so a thread/worker can fan out bumps
safely.

The transforms cover the portfolio's risk factors:
  shift_ir(d, tenor?)   parallel or single-node shift of OIS par rates + UST par yields
  shift_credit(d)       additive shift of issuer Z-spreads and CDS par spreads
  shift_vol(d)          additive shift of the flat normal vol and every equity IV
  scale_equity(rel)     relative shift of cash-equity and option-underlier spot
  scale_fx(rel)         relative shift of FX spot (foreign per USD)
"""

from __future__ import annotations

import collections.abc
from collections.abc import Callable
from dataclasses import replace

import numpy as np

from ..input.common.market_data import (
    CdsSpreadData,
    CreditSpreadData,
    EquityMarketData,
    EquitySpotData,
    EquityVolSurface,
    FlatNormalVolData,
    FxData,
    MarketDataSet,
    ParOISCurveData,
    TreasuryCurveData,
)

Transform = Callable[[object], object]


def bumped(mds: MarketDataSet, *transforms: Transform) -> MarketDataSet:
    """Return a copy of `mds` with every item passed through `transforms` in order."""
    new_items: dict[str, object] = {}
    for key, item in mds.items.items():
        for tf in transforms:
            item = tf(item)
        new_items[key] = item
    return MarketDataSet(
        valuation_date=mds.valuation_date,
        items=new_items,
        warnings=list(mds.warnings),
        as_of_dates=dict(mds.as_of_dates),
        snapshot_id=mds.snapshot_id,
    )


def _shift_aligned(values: tuple[float, ...], tenors: tuple[str, ...],
                   d: float, tenor: str | None) -> tuple[float, ...]:
    if tenor is None:
        return tuple(v + d for v in values)
    return tuple(v + (d if t == tenor else 0.0) for v, t in zip(values, tenors, strict=True))


def shift_ir(d: float, tenor: str | None = None) -> Transform:
    """Shift OIS par rates and UST par yields by `d` (decimal). `tenor` -> key-rate."""
    def tf(item: object) -> object:
        if isinstance(item, ParOISCurveData):
            return replace(item, par_rates=_shift_aligned(item.par_rates, item.tenors, d, tenor))
        if isinstance(item, TreasuryCurveData):
            return replace(item, par_yields=_shift_aligned(item.par_yields, item.tenors, d, tenor))
        return item
    return tf


def shift_credit(d: float) -> Transform:
    """Shift issuer Z-spreads and CDS par spreads by `d` (decimal).

    For a term-structured CDS (A2) the whole par-spread curve is shifted in parallel so
    the bootstrapped piecewise hazard actually moves — otherwise the pricer would keep
    using the unbumped curve and report zero CS01."""
    def tf(item: object) -> object:
        if isinstance(item, (CreditSpreadData, CdsSpreadData)) and item.curve_points:
            return replace(item, spread=item.spread + d,
                           curve_points=tuple((t, s + d) for t, s in item.curve_points))
        if isinstance(item, (CreditSpreadData, CdsSpreadData)):
            return replace(item, spread=item.spread + d)
        return item
    return tf


def shift_credit_issuers(shifts: collections.abc.Mapping[str, float], default: float) -> Transform:
    """Shift credit spreads per issuer, like `shift_credit` with an issuer-specific change.

    Each `CreditSpreadData` (keyed by `issuer`) and `CdsSpreadData` (keyed by `reference`) gets
    ``shifts.get(name, default)`` (decimal) added to `spread` and to every `curve_points` spread.
    Other items pass through unchanged; ``shift_credit_issuers({}, d)`` behaves as ``shift_credit(d)``.

    Args:
        shifts: Decimal spread change per issuer or CDS reference name.
        default: Decimal spread change for names not in `shifts`.

    Returns:
        The transform.
    """
    def tf(item: object) -> object:
        if isinstance(item, CreditSpreadData):
            d = float(shifts.get(item.issuer, default))
        elif isinstance(item, CdsSpreadData):
            d = float(shifts.get(item.reference, default))
        else:
            return item
        if item.curve_points:
            return replace(item, spread=item.spread + d,
                           curve_points=tuple((t, s + d) for t, s in item.curve_points))
        return replace(item, spread=item.spread + d)
    return tf


def shift_swap_spread(d: float, tenor: str | None = None) -> Transform:
    """Widen the swap spread (Treasury - SOFR OIS) by `d` (decimal), realized as a Treasury-curve
    shift holding the OIS/swap curve fixed (F3, FINDING 3).

    Unlike `shift_ir`, which moves BOTH curves together and so leaves the spread unchanged, this
    moves only `TreasuryCurveData` — so the UST bonds reprice while the SOFR swap (which projects
    and discounts on the OIS curve) does not. That isolates the swap-spread basis: the trades that
    are hedged against each other on the assumption of a stable Treasury-vs-swap spread now show the
    P&L of that spread moving. `tenor` restricts the shift to one key-rate node.
    """
    def tf(item: object) -> object:
        if isinstance(item, TreasuryCurveData):
            return replace(item, par_yields=_shift_aligned(item.par_yields, item.tenors, d, tenor))
        return item
    return tf


def shift_cds_bond_basis(d: float) -> Transform:
    """Widen the CDS-bond basis (CDS par spread - bond Z-spread) by `d` (decimal), realized as a
    CDS par-spread shift holding the bond Z-spreads fixed (F3, FINDING 3).

    Unlike `shift_credit`, which moves both the bond Z-spreads and the CDS par spreads together
    (so the basis is unchanged), this moves only **single-name** `CdsSpreadData` — so the
    single-name CDS trades reprice while the corporate bonds (priced off their own
    `CreditSpreadData` Z-spread) do not, and the CDX index (a directional index-credit position,
    not part of the single-name-vs-bond hedge) is left alone. That isolates the CDS-bond basis the
    design records as held at zero, making its dislocation a stressable event. Term-structured CDS
    shift the whole par-spread curve so the bootstrapped hazard actually moves (as in `shift_credit`).
    """
    def tf(item: object) -> object:
        if not isinstance(item, CdsSpreadData) or item.n_constituents is not None:
            return item                       # non-CDS, or an index (n_constituents set) -> untouched
        if item.curve_points:
            return replace(item, spread=item.spread + d,
                           curve_points=tuple((t, s + d) for t, s in item.curve_points))
        return replace(item, spread=item.spread + d)
    return tf


def shift_rates_vol(d: float) -> Transform:
    """Parallel shift of the normal (Bachelier) vol by `d` (decimal) — swaptions/caps.
    Shifts both the headline `vol` and every point of the C1 term structure so the shock
    reaches the pricers (which read `vol_at(expiry)` off the term structure)."""
    def tf(item: object) -> object:
        if isinstance(item, FlatNormalVolData):
            ts = tuple((t, max(v + d, 1e-6)) for t, v in item.term_structure)
            return replace(item, vol=max(item.vol + d, 1e-6), term_structure=ts)
        return item
    return tf


def shift_rates_vol_term(d: float, anchor_years: float = 1.0, ref_years: float = 5.0) -> Transform:
    """Term-structure normal-vol tilt (GAP 6): shift the vol by ``d * g(t)`` where
    ``g(t) = (sqrt(t) - sqrt(t_anchor)) / (sqrt(t_ref) - sqrt(t_anchor))`` — anchored at 1Y,
    unit at 5Y — so longer expiries move more (a steepen/flatten of the vol term structure).
    With the C1 term structure this tilts each vol node by its own expiry; each swaption/cap
    then reads its expiry's tilted vol via `vol_at`, capturing the book's term-vega. Falls back
    to the instrument's own `tenor` when no term structure is present."""
    ta, tr = anchor_years ** 0.5, ref_years ** 0.5

    def g(t: float) -> float:
        return float((t ** 0.5 - ta) / (tr - ta))

    def tf(item: object) -> object:
        if isinstance(item, FlatNormalVolData) and item.term_structure:
            ts = tuple((t, max(v + d * g(t), 1e-6)) for t, v in item.term_structure)
            return replace(item, term_structure=ts)
        if isinstance(item, FlatNormalVolData) and item.tenor:
            return replace(item, vol=max(item.vol + d * g(_tenor_years(item.tenor)), 1e-6))
        return item
    return tf


def shift_equity_vol(d: float) -> Transform:
    """Shift every equity implied vol by `d` vol points (decimal) — listed options."""
    def tf(item: object) -> object:
        if isinstance(item, EquityMarketData):
            surf: EquityVolSurface = item.vol_surface
            return replace(item, vol_surface=replace(
                surf, ivs=tuple(max(iv + d, 1e-6) for iv in surf.ivs)))
        return item
    return tf


def shift_equity_vol_term(d: float, anchor_days: float = 30.0, ref_days: float = 90.0) -> Transform:
    """Term-structure vol tilt (GAP 4): shift each IV by ``d * g(t)`` where
    ``g(t) = (sqrt(t) - sqrt(t_anchor)) / (sqrt(t_ref) - sqrt(t_anchor))`` — anchored at the
    30d (VIX) point and unit at the 90d (VIX3M) point. ``d`` is the slope change in decimal
    vol (a +1 vol-pt steeper 30d->90d term structure)."""
    ta, tr = (anchor_days / 365.0) ** 0.5, (ref_days / 365.0) ** 0.5

    def tf(item: object) -> object:
        if isinstance(item, EquityMarketData):
            surf = item.vol_surface
            g = ((np.sqrt(np.asarray(surf.days_to_expiry, dtype=float) / 365.0) - ta) / (tr - ta))
            ivs = tuple(max(iv + d * float(gi), 1e-6) for iv, gi in zip(surf.ivs, g, strict=True))
            return replace(item, vol_surface=replace(surf, ivs=ivs))
        return item
    return tf


def shift_equity_vol_skew(d: float) -> Transform:
    """Skew tilt (GAP 4): shift each IV by ``-d * ln(K / spot)`` so a positive ``d`` raises the
    put wing (K < spot) and lowers the call wing — a steeper put skew. ``d`` is the tilt in
    decimal vol per unit log-moneyness."""
    def tf(item: object) -> object:
        if isinstance(item, EquityMarketData):
            surf = item.vol_surface
            m = np.log(np.asarray(surf.strikes, dtype=float) / item.spot)
            ivs = tuple(max(iv - d * float(mi), 1e-6) for iv, mi in zip(surf.ivs, m, strict=True))
            return replace(item, vol_surface=replace(surf, ivs=ivs))
        return item
    return tf


def scale_equity(rel: float) -> Transform:
    """Relative shift of cash-equity and option-underlier spot (e.g. +0.01 = +1%)."""
    def tf(item: object) -> object:
        if isinstance(item, EquitySpotData):
            return replace(item, spot=item.spot * (1.0 + rel))
        if isinstance(item, EquityMarketData):
            return replace(item, spot=item.spot * (1.0 + rel))
        return item
    return tf


def scale_fx(rel: float) -> Transform:
    """Relative shift of FX spot (foreign per USD)."""
    def tf(item: object) -> object:
        if isinstance(item, FxData):
            return replace(item, spot=item.spot * (1.0 + rel))
        return item
    return tf


def shift_fx_rate(d: float) -> Transform:
    """Additive shift of the foreign discount rate (decimal) — FX rho (GAP 5)."""
    def tf(item: object) -> object:
        if isinstance(item, FxData):
            return replace(item, foreign_rate=item.foreign_rate + d)
        return item
    return tf


# --------------------------------------------------------------------------- #
# Granular transforms for full-revaluation historical VaR (per-tenor / per-name)
# --------------------------------------------------------------------------- #
def _tenor_years(tenor: str) -> float:
    n, unit = float(tenor[:-1]), tenor[-1].upper()
    return {"D": n / 365.0, "W": n / 52.0, "M": n / 12.0, "Y": n}[unit]


def shift_ir_curve(points_years: tuple[float, ...],
                   points_shift: tuple[float, ...]) -> Transform:
    """Curve-shape IR shock: shift each node by the shift interpolated (in tenor-years)
    from the control points. Beyond the control range numpy holds the end value flat
    (so the front end inherits the shortest control tenor's move)."""
    py = np.asarray(points_years, dtype=float)
    ps = np.asarray(points_shift, dtype=float)

    def node_shifts(tenors: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(float(np.interp(_tenor_years(t), py, ps)) for t in tenors)

    def tf(item: object) -> object:
        if isinstance(item, ParOISCurveData):
            sh = node_shifts(item.tenors)
            return replace(item, par_rates=tuple(
                r + d for r, d in zip(item.par_rates, sh, strict=True)))
        if isinstance(item, TreasuryCurveData):
            sh = node_shifts(item.tenors)
            return replace(item, par_yields=tuple(
                y + d for y, d in zip(item.par_yields, sh, strict=True)))
        return item
    return tf


def scale_equity_named(by_name: dict[str, float], default: float = 0.0) -> Transform:
    """Per-underlier relative spot shift; names not in `by_name` get `default`
    (the systematic-equity-proxy return)."""
    def tf(item: object) -> object:
        if isinstance(item, EquitySpotData):
            return replace(item, spot=item.spot * (1.0 + by_name.get(item.ticker, default)))
        if isinstance(item, EquityMarketData):
            return replace(item, spot=item.spot * (1.0 + by_name.get(item.underlier, default)))
        return item
    return tf


def scale_rates_vol(rel: float) -> Transform:
    """Relative shift of the normal vol (MOVE-anchored): vol *= (1 + rel). Scales the C1 term
    structure too so the shock reaches pricers reading `vol_at(expiry)`."""
    def tf(item: object) -> object:
        if isinstance(item, FlatNormalVolData):
            ts = tuple((t, max(v * (1.0 + rel), 1e-6)) for t, v in item.term_structure)
            return replace(item, vol=max(item.vol * (1.0 + rel), 1e-6), term_structure=ts)
        return item
    return tf


def scale_fx_named(by_ccy: dict[str, float], default: float = 0.0) -> Transform:
    """Per-currency relative FX-spot shift (JPY vs TWD move independently)."""
    def tf(item: object) -> object:
        if isinstance(item, FxData):
            return replace(item, spot=item.spot * (1.0 + by_ccy.get(item.foreign_ccy, default)))
        return item
    return tf


def shift_fx_rate_named(by_ccy: dict[str, float], default: float = 0.0) -> Transform:
    """Per-currency additive foreign-rate shift (decimal) — FX rho per pair (GAP 5)."""
    def tf(item: object) -> object:
        if isinstance(item, FxData):
            return replace(item, foreign_rate=item.foreign_rate + by_ccy.get(item.foreign_ccy, default))
        return item
    return tf
