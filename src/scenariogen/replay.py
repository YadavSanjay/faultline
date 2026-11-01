"""Historical replay: an episode's observed factor moves as a scenario's authored drivers.

A replay scenario names a window ``(start, end]``; its drivers are the factor moves that actually happened
over it (``episode_moves``), minus any labelled idiosyncratic company moves (``replay_drivers``), and its
horizon is the market factor's trading-day count (``trading_days``). ``vol_matched`` scales a long proxy
series to a target's volatility (the ``credit`` anchor's BAA10Y → IG OAS).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

import numpy as np
import pandas as pd

# Factor families whose daily moves are returns (compound), not level changes (sum).
_COMPOUNDING_PREFIXES = ("eq:", "fx:")


def _calendar_index(series: pd.Series) -> pd.DatetimeIndex:
    """The series' index as normalised calendar dates, whatever its element type."""
    return pd.DatetimeIndex(pd.to_datetime(pd.Index(series.index).astype(str))).normalize()


def _window(series: pd.Series, start: date, end: date) -> pd.Series:
    """The rows of ``series`` dated in ``(start, end]``."""
    idx = _calendar_index(series)
    mask = (idx > pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
    return series[np.asarray(mask)]


def _check_window(start: date, end: date) -> None:
    if start >= end:
        raise ValueError(f"replay window start {start} must be before end {end}")


def episode_moves(
    series: Mapping[str, pd.Series],
    start: date,
    end: date,
    factors: Sequence[str] | None = None,
) -> dict[str, float]:
    """Computes each factor's realised move over an episode window.

    Args:
        series: Factor key to daily-move series in canonical units, indexed by date (sorted ascending).
        start: Window start; rows dated on it are excluded.
        end: Window end; rows dated on it are included.
        factors: Optional restriction of the keys considered.

    Returns:
        Factor key to its move over the rows in ``(start, end]``: compounded for ``eq:``/``fx:``,
        summed otherwise. A factor with no rows in the window, or a missing value in it, is left out.

    Raises:
        ValueError: ``start >= end``.
    """
    _check_window(start, end)
    keys = list(series) if factors is None else [f for f in factors if f in series]
    moves: dict[str, float] = {}
    for key in keys:
        vals = _window(series[key], start, end).to_numpy(dtype=float)
        if vals.size == 0 or np.isnan(vals).any():
            continue
        if key.startswith(_COMPOUNDING_PREFIXES):
            moves[key] = float(np.prod(1.0 + vals) - 1.0)
        else:
            moves[key] = float(np.sum(vals))
    return moves


def trading_days(series: pd.Series, start: date, end: date) -> int:
    """Counts the rows of one series dated in ``(start, end]``.

    Args:
        series: A series indexed by date, sorted ascending.
        start: Window start (exclusive).
        end: Window end (inclusive).

    Returns:
        The number of rows in the window.

    Raises:
        ValueError: ``start >= end``.
    """
    _check_window(start, end)
    return len(_window(series, start, end))


def replay_drivers(
    moves: Mapping[str, float],
    idiosyncratic: Iterable[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Splits episode moves into replay drivers and excluded idiosyncratic moves.

    Args:
        moves: Factor key to observed episode move.
        idiosyncratic: Factor keys labelled idiosyncratic; labels without a move are ignored.

    Returns:
        ``(drivers, excluded)``: the moves of the non-labelled factors, and of the labelled ones.
    """
    labelled = set(idiosyncratic)
    drivers = {k: v for k, v in moves.items() if k not in labelled}
    excluded = {k: v for k, v in moves.items() if k in labelled}
    return drivers, excluded


def vol_matched(
    proxy: pd.Series,
    target: pd.Series,
    *,
    min_overlap: int = 250,
) -> tuple[pd.Series, float]:
    """Scales a proxy daily-move series to the target's volatility.

    Args:
        proxy: Proxy daily moves indexed by date.
        target: Target daily moves indexed by date.
        min_overlap: Fewest shared dates allowed.

    Returns:
        ``(proxy * beta, beta)`` with ``beta = std(target) / std(proxy)`` (ddof 1) over the shared
        dates; the whole proxy series is scaled.

    Raises:
        ValueError: Fewer than ``min_overlap`` shared dates, or a proxy with zero deviation there.
    """
    p = pd.Series(proxy.to_numpy(dtype=float), index=_calendar_index(proxy))
    t = pd.Series(target.to_numpy(dtype=float), index=_calendar_index(target))
    shared = p.index.intersection(t.index)
    if len(shared) < min_overlap:
        raise ValueError(f"vol_matched: {len(shared)} shared dates, fewer than min_overlap={min_overlap}")
    sigma_proxy = float(np.std(p.loc[shared].to_numpy(), ddof=1))
    if not sigma_proxy > 0.0:
        raise ValueError("vol_matched: the proxy has zero deviation over the shared dates")
    beta = float(np.std(t.loc[shared].to_numpy(), ddof=1)) / sigma_proxy
    return proxy * beta, beta
