"""Per-issuer credit spread factor histories (``docs/specs/issuer-credit-factors.md``).

Pure functions: the daily 5Y-bucket spread from single-name DTCC prints (median per date with an
outlier filter), the daily additive moves on a business-day calendar, and the proportional backfill
of the issuer moves from a proxy before the issuer's history starts. ``FactorHistory`` builds the
``credit:<ISSUER>`` factors with them.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

import pandas as pd

_COLUMNS = ("date", "spread", "tenor_years")


def issuer_daily_spreads(prints: pd.DataFrame, *, min_tenor_years: float, max_tenor_years: float,
                         outlier_ratio: float, outlier_lookback: int) -> pd.Series:
    """Return the daily tenor-bucket spread of one issuer from its prints.

    Keeps prints with ``min_tenor_years <= tenor_years <= max_tenor_years`` and ``spread > 0``, takes
    the median spread per date, then walks the dates in order: a date's median is kept unless at
    least 3 earlier values are already kept and it lies outside ``[ref / outlier_ratio,
    ref * outlier_ratio]``, where ``ref`` is the median of the last ``outlier_lookback`` kept values.
    A dropped date never enters a later ``ref``.

    Args:
        prints: Frame with columns ``date`` (date or ISO string), ``spread`` (decimal) and
            ``tenor_years``.
        min_tenor_years: Lower edge of the tenor bucket (inclusive).
        max_tenor_years: Upper edge of the tenor bucket (inclusive).
        outlier_ratio: Multiplicative band around the reference, greater than 1.
        outlier_lookback: Number of last kept values forming the reference, at least 1.

    Returns:
        The kept daily spreads indexed by ``pd.Timestamp``, ascending; empty when no print qualifies.

    Raises:
        ValueError: For ``outlier_ratio <= 1``, ``outlier_lookback < 1``, ``min_tenor_years >
            max_tenor_years`` or a missing column.
    """
    if not outlier_ratio > 1:
        raise ValueError(f"outlier_ratio must be > 1, got {outlier_ratio}")
    if outlier_lookback < 1:
        raise ValueError(f"outlier_lookback must be >= 1, got {outlier_lookback}")
    if min_tenor_years > max_tenor_years:
        raise ValueError(f"min_tenor_years {min_tenor_years} > max_tenor_years {max_tenor_years}")
    missing = [c for c in _COLUMNS if c not in prints.columns]
    if missing:
        raise ValueError(f"prints missing column(s) {missing}")
    df = prints.loc[:, list(_COLUMNS)].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["spread"] = pd.to_numeric(df["spread"], errors="coerce")
    df["tenor_years"] = pd.to_numeric(df["tenor_years"], errors="coerce")
    df = df[(df["tenor_years"] >= min_tenor_years) & (df["tenor_years"] <= max_tenor_years) & (df["spread"] > 0)]
    if df.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    daily = df.groupby("date")["spread"].median().sort_index()
    kept_dates: list[pd.Timestamp] = []
    kept: list[float] = []
    for d, v in daily.items():
        value = float(v)
        if len(kept) >= 3:
            ref = statistics.median(kept[-outlier_lookback:])
            if value < ref / outlier_ratio or value > ref * outlier_ratio:
                continue
        kept_dates.append(pd.Timestamp(d))
        kept.append(value)
    return pd.Series(kept, index=pd.DatetimeIndex(kept_dates), dtype=float)


def issuer_spread_moves(daily: pd.Series, calendar: Sequence[pd.Timestamp], *,
                        max_gap_days: int) -> pd.Series:
    """Return the daily additive spread change of ``daily`` on a business-day calendar.

    The calendar is restricted to ``[first date of daily, last date of daily]``; each daily value is
    placed on its date (dates not in the calendar are ignored) and forward-filled across at most
    ``max_gap_days`` consecutive calendar dates. The move on a calendar date is its value minus the
    previous calendar date's value where both exist; dates without a move are absent.

    Args:
        daily: Daily spreads indexed by ``pd.Timestamp``.
        calendar: Business dates, ascending.
        max_gap_days: Largest number of consecutive calendar dates a value is carried forward.

    Returns:
        The moves indexed by ``pd.Timestamp``, ascending.

    Raises:
        ValueError: If ``max_gap_days`` is negative.
    """
    if max_gap_days < 0:
        raise ValueError(f"max_gap_days must be >= 0, got {max_gap_days}")
    if daily.empty:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    first, last = daily.index.min(), daily.index.max()
    cal = pd.DatetimeIndex([d for d in calendar if first <= d <= last])
    placed = daily.reindex(cal)
    filled = placed.ffill(limit=max_gap_days) if max_gap_days > 0 else placed
    return filled.diff().dropna()


def proportional_backfill(moves: pd.Series, proxy_moves: pd.Series, *, issuer_level: float,
                          proxy_level: float) -> pd.Series:
    """Extend issuer moves backwards with proxy moves scaled by the level ratio.

    Before the first date of ``moves`` the result holds ``proxy_moves * beta`` with ``beta =
    issuer_level / proxy_level``; from that date on it holds ``moves`` unchanged. Empty ``moves``
    returns ``proxy_moves * beta``.

    Args:
        moves: Issuer moves indexed by ``pd.Timestamp``.
        proxy_moves: Proxy moves indexed by ``pd.Timestamp``.
        issuer_level: Issuer spread level at the splice, positive and finite.
        proxy_level: Proxy spread level at the splice, positive and finite.

    Returns:
        The spliced moves sorted by date.

    Raises:
        ValueError: For a non-positive or non-finite level.
    """
    for name, level in (("issuer_level", issuer_level), ("proxy_level", proxy_level)):
        if not (math.isfinite(level) and level > 0):
            raise ValueError(f"{name} must be positive and finite, got {level}")
    beta = issuer_level / proxy_level
    if moves.empty:
        return (proxy_moves * beta).sort_index()
    pre = proxy_moves[proxy_moves.index < moves.index.min()] * beta
    return pd.concat([pre, moves]).sort_index()
