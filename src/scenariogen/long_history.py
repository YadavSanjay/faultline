"""Long move history per scenario factor, for the severity anchors.

Turns the long-history files (``equity/long_history_prices.csv`` from Yahoo and
``stress/fred_long_history.csv`` from FRED) into daily moves per canonical factor key, in the units of
``factor_keys.py``. A young name (AVGO, META, CEG, …) gets a proxy's moves (``^SOX``, ``^IXIC``, ``XLU``)
spliced in front of its own history, volatility-matched over the shared dates
(``beta = std_own / std_proxy``), so an adverse anchor can reach back to the dot-com bust and 2008. The
mapping lives in ``configs/scenariogen/long_history.json``; ``info`` records exactly where each series
came from so a reviewer can see every proxy and beta.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import storage

from .replay import vol_matched


def level_moves(levels: pd.Series, factor: str) -> pd.Series:
    """Turns a level series into the factor's daily moves in canonical units.

    Args:
        levels: Levels sorted by index (prices, yields in percent, index points, FX rates).
        factor: Canonical factor key.

    Returns:
        Simple returns for ``eq:``/``fx:`` factors; otherwise the percent-point change / 100. The
        first point, with no predecessor, is dropped.
    """
    s = pd.to_numeric(levels, errors="coerce").astype(float)
    moves = s.pct_change() if factor.startswith(("eq:", "fx:")) else s.diff() / 100.0
    return moves.iloc[1:]


def _ticker_levels(prices: pd.DataFrame, ticker: str) -> pd.Series | None:
    sub = prices[prices["ticker"] == ticker]
    if sub.empty:
        return None
    s = pd.Series(pd.to_numeric(sub["adj_close"], errors="coerce").to_numpy(dtype=float),
                  index=pd.to_datetime(sub["date"]).dt.strftime("%Y-%m-%d").to_numpy())
    s = s[~s.index.duplicated(keep="last")].dropna().sort_index()
    return s if len(s) >= 2 else None


def _fred_levels(fred: pd.DataFrame, column: str) -> pd.Series | None:
    if column not in fred.columns:
        return None
    s = pd.Series(pd.to_numeric(fred[column], errors="coerce").to_numpy(dtype=float),
                  index=pd.to_datetime(fred["date"]).dt.strftime("%Y-%m-%d").to_numpy())
    s = s.dropna().sort_index()
    return s if len(s) >= 2 else None


def build_long_history(
    prices: pd.DataFrame,
    fred: pd.DataFrame,
    mapping: Mapping[str, Mapping[str, Any]],
    *,
    min_overlap: int = 250,
) -> tuple[dict[str, pd.Series], dict[str, dict[str, Any]]]:
    """Builds each mapped factor's long daily-move series, splicing proxies before young histories.

    Args:
        prices: ``date, ticker, adj_close`` rows.
        fred: A wide frame with ``date`` plus one column per FRED series.
        mapping: Factor key -> ``{"ticker"}``, ``{"ticker", "proxy"}``, ``{"fred"}`` or
            ``{"fred", "match"}`` (the long series volatility-matched to the ``match`` column).
        min_overlap: Minimum shared move dates to estimate the splice or match beta.

    Returns:
        ``(series, info)``: factor -> moves, and factor -> provenance (``source``, ``proxy``,
        ``beta``, ``spliced``, ``own_start``, ``start``, ``missing``, and ``matched`` for a match).

    Raises:
        ValueError: A ``match`` target missing from ``fred``, or too short to estimate its beta.
    """
    series: dict[str, pd.Series] = {}
    info: dict[str, dict[str, Any]] = {}
    for factor, spec in mapping.items():
        if "fred" in spec:
            source = str(spec["fred"])
            own_levels = _fred_levels(fred, source)
        else:
            source = str(spec["ticker"])
            own_levels = _ticker_levels(prices, source)
        proxy = str(spec["proxy"]) if spec.get("proxy") else None
        own = level_moves(own_levels, factor) if own_levels is not None else None
        prx_levels = _ticker_levels(prices, proxy) if proxy is not None else None
        prx = level_moves(prx_levels, factor) if prx_levels is not None else None

        beta: float | None = None
        spliced = False
        if own is not None and prx is not None:
            shared = own.index.intersection(prx.index)
            if len(shared) >= min_overlap:
                sd_p = float(prx.loc[shared].std(ddof=1))
                if sd_p > 0.0:
                    beta = float(own.loc[shared].std(ddof=1)) / sd_p
                    pre = prx[prx.index < own.index[0]] * beta
                    spliced = not pre.empty
                    out = pd.concat([pre, own]) if spliced else own
                else:
                    out = own
            else:
                out = own
        elif own is not None:
            out = own
        elif prx is not None:                                   # own ticker missing: proxy, beta = 1
            beta, spliced, out = 1.0, True, prx
        else:
            info[factor] = {"source": source, "proxy": proxy, "beta": None, "spliced": False,
                            "own_start": None, "start": None, "missing": True}
            continue
        if not spliced and own is not None:
            beta = None
        # A "match" series is the factor's own definition (e.g. IG OAS for credit) with a shorter history:
        # the long series is scaled to its volatility over the shared dates, never used raw.
        match = str(spec["match"]) if spec.get("match") else None
        if match is not None:
            target_levels = _fred_levels(fred, match)
            if target_levels is None:
                raise ValueError(f"long history {factor}: volatility-match target {match} not on disk")
            out, beta = vol_matched(out, level_moves(target_levels, factor), min_overlap=min_overlap)
        series[factor] = out
        info[factor] = {"source": source, "proxy": proxy, "beta": beta, "spliced": spliced,
                        "own_start": str(own.index[0]) if own is not None and len(own) else None,
                        "start": str(out.index[0]) if len(out) else None, "missing": own is None}
        if match is not None:
            info[factor]["matched"] = match
    return series, info


def load_long_history(project_root: Path, *, min_overlap: int | None = None,
                      ) -> tuple[dict[str, pd.Series], dict[str, dict[str, Any]]]:
    """Reads the long-history files and the committed mapping, then ``build_long_history``.

    Missing files give empty inputs (every factor ``missing``), so callers can fall back to the
    on-disk 2018+ history.
    """
    cfg = json.loads((Path(project_root) / "configs" / "scenariogen" / "long_history.json").read_text())
    base = storage.data_base(project_root)
    files = cfg.get("files", {})
    prices_loc = storage.join(base, "marketdata", *str(files.get("prices", "equity/long_history_prices.csv"))
                              .split("/"))
    fred_loc = storage.join(base, "marketdata", *str(files.get("fred", "stress/fred_long_history.csv")).split("/"))
    prices = (storage.read_csv(prices_loc) if storage.exists(prices_loc)
              else pd.DataFrame(columns=["date", "ticker", "adj_close"]))
    fred = storage.read_csv(fred_loc) if storage.exists(fred_loc) else pd.DataFrame(columns=["date"])
    # Volatility-match targets (e.g. IG OAS for the BAA10Y-based credit factor) live in another FRED
    # file; their columns join the wide frame by date.
    targets = sorted({str(s["match"]) for s in cfg["factors"].values() if s.get("match")})
    if targets:
        match_loc = storage.join(base, "marketdata", *str(files.get("match", "stress/fred_stress_macro.csv"))
                                 .split("/"))
        if storage.exists(match_loc):
            extra = storage.read_csv(match_loc)
            extra = extra[["date", *[c for c in targets if c in extra.columns and c not in fred.columns]]]
            fred = fred.assign(date=fred["date"].astype(str)).merge(
                extra.assign(date=extra["date"].astype(str)), on="date", how="outer")
    overlap = int(min_overlap if min_overlap is not None else cfg.get("min_overlap", 250))
    return build_long_history(prices, fred, cfg["factors"], min_overlap=overlap)
