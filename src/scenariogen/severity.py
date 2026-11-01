"""Severity anchors for named scenarios (scenario-severity-anchor).

Each anchored scenario names a lead factor and direction. Its tiers come from history: mild is a
once-in-``mild_return_years`` horizon move, adverse the record, severe a stated multiple of the record
(capped by the plausibility bound). The composed scenario (authored + enrichment layers) is rescaled so
the lead factor sits at its anchor, with the enrichment allowed to move it only within a band. Company
legs get their company-specific seed (no double count) and a cap on their stacked multipliers.
"""

from __future__ import annotations

import collections.abc
import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from src.scenariogen.covariance import FactorCovariance

# Trading days per year: the overlapping horizon windows are one per trading day.
TRADING_DAYS = 252


def horizon_moves(moves: pd.Series, factor: str, horizon_days: int) -> pd.Series:
    """Turns daily moves into overlapping horizon moves.

    Args:
        moves: Daily moves sorted by date, in the factor's canonical units.
        factor: Canonical factor key; ``eq:``/``fx:`` moves compound, every other factor sums.
        horizon_days: Window length in moves.

    Returns:
        One horizon move per end date, from the ``horizon_days``-th move on.

    Raises:
        ValueError: ``horizon_days <= 0`` or fewer moves than ``horizon_days``.
    """
    if horizon_days <= 0:
        raise ValueError(f"horizon_days must be > 0, got {horizon_days}")
    s = pd.to_numeric(moves, errors="coerce").astype(float)
    if len(s) < horizon_days:
        raise ValueError(f"{len(s)} moves < horizon_days {horizon_days} for {factor}")
    if factor.startswith(("eq:", "fx:")):
        out = (1.0 + s).rolling(horizon_days).apply(np.prod, raw=True) - 1.0
    else:
        out = s.rolling(horizon_days).sum()
    return out.iloc[horizon_days - 1:]


def tier_anchors(
    moves: pd.Series,
    factor: str,
    horizon_days: int,
    direction: str,
    *,
    mild_return_years: float = 10.0,
    severe_factor: float = 1.5,
    bound: float | None = None,
) -> dict[str, float]:
    """Computes the mild, adverse and severe anchors of a factor from its history.

    Args:
        moves: Daily moves sorted by date, in the factor's canonical units.
        factor: Canonical factor key.
        horizon_days: Window length in moves.
        direction: ``"down"`` or ``"up"``: which tail is adverse.
        mild_return_years: Return period of the mild move, in years.
        severe_factor: Multiple of the adverse anchor that gives the severe anchor.
        bound: Optional cap on the severe anchor's magnitude.

    Returns:
        ``{"mild", "adverse", "severe"}`` in the factor's units.

    Raises:
        ValueError: A bad direction, ``mild_return_years``, ``severe_factor`` or ``bound``.
    """
    if direction not in ("down", "up"):
        raise ValueError(f"direction must be 'down' or 'up', got {direction!r}")
    if not mild_return_years > 0:
        raise ValueError(f"mild_return_years must be > 0, got {mild_return_years}")
    if not severe_factor >= 1:
        raise ValueError(f"severe_factor must be >= 1, got {severe_factor}")
    if bound is not None and not bound > 0:
        raise ValueError(f"bound must be > 0, got {bound}")
    windows = horizon_moves(moves, factor, horizon_days).to_numpy(dtype=float)
    ordered = np.sort(windows) if direction == "down" else np.sort(windows)[::-1]
    n = len(ordered)
    p = horizon_days / (TRADING_DAYS * mild_return_years)
    k = max(1, math.ceil(n * p))
    mild = float(ordered[k - 1])
    adverse = float(ordered[0])
    severe = adverse * severe_factor
    if bound is not None and abs(severe) > bound:
        severe = math.copysign(bound, severe)
    return {"mild": mild, "adverse": adverse, "severe": float(severe)}


def cap_leg_inputs(
    seed_legs: dict[str, float],
    calibrated_legs: dict[str, float],
    news_mult: dict[str, float],
    crowding_mult: dict[str, float],
    *,
    cap: float = 1.5,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Bounds each company leg's compound multiplier to ``[1/cap, cap]``.

    Args:
        seed_legs: Leg factor -> seed move.
        calibrated_legs: Leg factor -> calibrated move (same keys as ``seed_legs``).
        news_mult: Leg factor -> news multiplier (absent means 1).
        crowding_mult: Leg factor -> crowding multiplier (absent means 1).
        cap: Upper bound of the compound multiplier; its reciprocal is the lower bound.

    Returns:
        ``(calibrated_legs, news_mult, crowding_mult)`` after capping, with the input keys.

    Raises:
        ValueError: ``cap < 1`` or mismatched leg keys.
    """
    if not cap >= 1:
        raise ValueError(f"cap must be >= 1, got {cap}")
    if set(seed_legs) != set(calibrated_legs):
        raise ValueError(f"seed_legs and calibrated_legs differ: {sorted(set(seed_legs) ^ set(calibrated_legs))}")
    cal, news, crowd = dict(calibrated_legs), dict(news_mult), dict(crowding_mult)
    lo, hi = 1.0 / cap, cap
    for f, seed in seed_legs.items():
        a = calibrated_legs[f] / seed if seed != 0 else 1.0
        n = news_mult.get(f, 1.0)
        c = crowding_mult.get(f, 1.0)
        m = a * n * c
        if m <= 0 or lo <= m <= hi:
            continue
        lam = math.log(hi if m > hi else lo) / math.log(m)
        cal[f] = seed * a ** lam
        if f in news:
            news[f] = n ** lam
        if f in crowd:
            crowd[f] = c ** lam
    return cal, news, crowd


def residual_seeds(
    seed_legs: dict[str, float],
    calibrated_legs: dict[str, float],
    authored_drivers: Mapping[str, float],
    systematic: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Reseeds legs that are also authored drivers with their company-specific part.

    Args:
        seed_legs: Leg factor -> seed move.
        calibrated_legs: Leg factor -> calibrated move (same keys as ``seed_legs``).
        authored_drivers: Factor -> authored total move.
        systematic: Factor -> co-move from the other drivers (absent means 0).

    Returns:
        ``(seed_legs, calibrated_legs)`` with the reseeded legs.

    Raises:
        ValueError: Mismatched leg keys.
    """
    if set(seed_legs) != set(calibrated_legs):
        raise ValueError(f"seed_legs and calibrated_legs differ: {sorted(set(seed_legs) ^ set(calibrated_legs))}")
    seeds, cal = dict(seed_legs), dict(calibrated_legs)
    for f, seed in seed_legs.items():
        if f not in authored_drivers:
            continue
        residual = float(authored_drivers[f]) - float(systematic.get(f, 0.0))
        cal[f] = residual * calibrated_legs[f] / seed if seed != 0 else residual
        seeds[f] = residual
    return seeds, cal


def anchor_scale(lead_composed: float, lead_authored: float, anchor: float, *, band: float = 0.25) -> float:
    """Returns the factor that rescales a composed scenario onto its anchor within a band.

    Args:
        lead_composed: The composed scenario's lead-factor move.
        lead_authored: The authored-only scenario's lead-factor move.
        anchor: The tier's anchor for the lead factor.
        band: Half-width of the band around 1 for the enrichment's relative effect.

    Returns:
        ``k`` with ``k * lead_composed = anchor * clamp(lead_composed / lead_authored, 1 - band, 1 + band)``.

    Raises:
        ValueError: ``lead_composed == 0``, ``band`` outside ``[0, 1)`` or a non-finite input.
    """
    for name, v in (("lead_composed", lead_composed), ("lead_authored", lead_authored), ("anchor", anchor),
                    ("band", band)):
        if not math.isfinite(v):
            raise ValueError(f"{name} must be finite, got {v}")
    if not 0 <= band < 1:
        raise ValueError(f"band must be in [0, 1), got {band}")
    if lead_composed == 0:
        raise ValueError("lead_composed is 0: the scenario cannot be rescaled onto its anchor")
    r = lead_composed / lead_authored if lead_authored != 0 else 1.0
    return anchor * min(max(r, 1.0 - band), 1.0 + band) / lead_composed


def bound_scale_cap(moves: Mapping[str, float], bounds: Mapping[str, float],
                    overlays: Mapping[str, float] | None = None) -> float:
    """Returns the largest tier scale that keeps every bounded factor inside its hard bound.

    Args:
        moves: The part of a scenario vector that scales with the tier (drivers and their propagation).
        bounds: Factor -> hard bound (positive, canonical units); factors missing from it are unconstrained.
        overlays: The part of the scenario vector that doesn't scale with the tier.

    Returns:
        The largest ``k >= 0`` with ``|k * moves[f] + overlays.get(f, 0)| <= bounds[f]`` for every bounded
        factor ``f``; ``math.inf`` when every bounded factor has a zero move.

    Raises:
        ValueError: An overlay that alone exceeds its factor's bound, or a non-positive bound.
    """
    over = overlays or {}
    cap = math.inf
    for f, b in bounds.items():
        b = float(b)
        o = float(over.get(f, 0.0))
        if not b > 0.0:
            raise ValueError(f"hard bound for {f} must be positive, got {b}")
        if abs(o) > b:
            raise ValueError(f"overlay {o} on {f} alone exceeds its bound {b}")
        m = float(moves.get(f, 0.0))
        if abs(m) > 0.0:
            # |k m + o| <= b with k >= 0: the outward side binds, k <= (b - sign(m) o) / |m|
            cap = min(cap, (b - math.copysign(1.0, m) * o) / abs(m))
    return cap


def affine_scale_cap(at_zero: Mapping[str, float], at_one: Mapping[str, float],
                     bounds: Mapping[str, float], *, margin: float = 1e-9) -> float:
    """Returns the largest tier scale that keeps a vector affine in the scale inside its hard bounds.

    The vector is ``v(k) = c + k * (at_one - c)`` with intercept ``c = at_zero``; a factor missing from
    either mapping counts as 0. The raw cap is ``bound_scale_cap`` on the slopes with the intercepts as
    overlays, then shrunk by the relative ``margin`` so the capped vector clears a strict bound check.

    Args:
        at_zero: The scenario vector at scale 0 (the intercept).
        at_one: The scenario vector at scale 1.
        bounds: Factor -> hard bound (positive, canonical units); factors missing from it are unconstrained.
        margin: Relative shrink applied to a finite raw cap, in ``[0, 1)``.

    Returns:
        ``raw * (1 - margin)`` where ``raw`` is the largest ``k >= 0`` with ``|c_f + k * s_f| <= bounds[f]``
        for every bounded factor ``f``; ``math.inf`` when every bounded slope is zero.

    Raises:
        ValueError: ``margin`` outside ``[0, 1)``, a non-positive bound, or an intercept that alone exceeds
            its factor's bound.
    """
    if not 0.0 <= margin < 1.0:                     # also rejects NaN
        raise ValueError(f"margin must be in [0, 1), got {margin}")
    intercepts = {f: float(at_zero.get(f, 0.0)) for f in bounds}
    slopes = {f: float(at_one.get(f, 0.0)) - intercepts[f] for f in bounds}
    raw = bound_scale_cap(slopes, bounds, overlays=intercepts)
    return raw * (1.0 - margin) if math.isfinite(raw) else math.inf


def lead_systematic(cov: FactorCovariance, lead: str, lead_move: float,
                    targets: collections.abc.Iterable[str]) -> dict[str, float]:
    """Returns each target's one-factor systematic move given the scenario's lead factor alone.

    For a target ``t``: ``mean[t] + cov[t, lead] / cov[lead, lead] * (lead_move - mean[lead])``, with
    ``mean`` and ``cov`` from ``cov`` indexed by ``cov.factors``.

    Args:
        cov: The factor covariance (means and covariance matrix, canonical units).
        lead: The scenario's lead factor (``severity.lead``), e.g. ``eq:XLK``.
        lead_move: The lead factor's move, canonical units.
        targets: Factors to project on the lead. Targets not in ``cov.factors`` and the lead itself are
            left out; duplicates appear once.

    Returns:
        Target -> systematic move, keyed in the targets' first-appearance order.

    Raises:
        ValueError: ``lead`` not in ``cov.factors``, ``cov[lead, lead] <= 0``, or ``lead_move`` NaN or
            infinite.
    """
    if lead not in cov.factors:
        raise ValueError(f"lead {lead!r} is not a covariance factor")
    if not math.isfinite(lead_move):
        raise ValueError(f"lead move must be finite, got {lead_move}")
    li = cov.factors.index(lead)
    var = float(cov.cov[li, li])
    if not var > 0.0:
        raise ValueError(f"lead {lead!r} has non-positive variance {var}")
    delta = float(lead_move) - float(cov.mean[li])
    out: dict[str, float] = {}
    for t in targets:                                # one pass: targets may be a one-shot iterator
        if t == lead or t in out or t not in cov.factors:
            continue
        ti = cov.factors.index(t)
        out[t] = float(cov.mean[ti]) + float(cov.cov[ti, li]) / var * delta
    return out
