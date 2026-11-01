"""OptionSignalProvider — serves the B1-B3 option signals into scenariogen (B4, options-shock-sizing).

Loads the derived option signals (`option_signals.csv`) and risk-neutral-density curves
(`rnd_curves.csv`) that the marketdata `signals_runner` produces. With an ``as_of`` it is
**point-in-time**: per name, the latest snapshot on or before ``as_of`` within the staleness limit
(``options.sizing.select_snapshot``), signals and RND curve from that same date. A name with no
qualifying snapshot but its own CBOE vol index (``options_sizing.json`` ``vol_index``) falls back to a
lognormal with the index close on or before ``as_of``. It exposes:

  * `implied_prob(ticker, move)` — the market-implied probability of an equity move at least as
    extreme as ``move`` (decimal): off the RND CDF, or the lognormal fallback. Powers the
    forward-looking plausibility check.
  * `quantile(ticker, q)` — the option-implied ``q`` quantile return and where it came from; sizes the
    option-anchored tiers (``keyman_nvda``) and the option-tail severity check.
  * `atm_iv(ticker)` / `skew(ticker)` — per-name option-implied vol level and 25d skew.

Everything degrades gracefully: a name with no data, or a missing signals file, yields ``None``.
Reads go through `src.common.storage`, so it is dual-mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common import storage

from .sizing import (
    OptionSizingConfig,
    lognormal_quantile,
    lognormal_tail_prob,
    rnd_quantile,
    select_snapshot,
)

# Horizon of the vol-index fallback: the indices are 30-day implied vols.
INDEX_T_YEARS = 30.0 / 365.0


@dataclass
class _NameSignals:
    spot: float | None
    atm_iv: float | None
    skew_25d: float | None
    term_ratio: float | None      # atm_iv_30d / atm_iv_90d ; >1 = inverted (near-term stress)
    vrp: float | None
    strikes: np.ndarray | None    # RND grid
    cdf: np.ndarray | None        # P(S_T <= K)
    snapshot_date: str | None = None
    dte: int | None = None        # RND horizon in calendar days
    vol_index: str | None = None  # set when this name is the vol-index fallback (atm_iv = index / 100)
    index_date: str | None = None


class OptionSignalProvider:
    """Read-only view over the option signals, keyed by underlier ticker (PIT when built with as_of)."""

    def __init__(self, by_name: dict[str, _NameSignals], config: dict[str, Any] | None = None):
        self._by_name = by_name
        self.config = dict(config or {})

    # -- construction -------------------------------------------------------- #
    @classmethod
    def load(cls, project_root: Path, config: dict[str, Any] | None = None, *, as_of: date | None = None,
             sizing: OptionSizingConfig | None = None) -> OptionSignalProvider | None:
        """Build from the on-disk signal CSVs (and, with ``sizing``, the CBOE vol indices).

        Args:
            project_root: Repo root (data location via the storage façade).
            config: The plausibility ``market_implied`` block (kept for provenance).
            as_of: The run date; None = each name's latest snapshot (legacy, look-ahead for a past run).
            sizing: Staleness limit and the vol-index fallback map; defaults when None.

        Returns:
            The provider, or None when neither signals nor any fallback index is available.
        """
        sizing = sizing or OptionSizingConfig()
        base = storage.join(storage.data_base(project_root), "marketdata", "options")
        sig_loc = storage.join(base, "signals", "option_signals.csv")
        sig = storage.read_csv(sig_loc) if storage.exists(sig_loc) else pd.DataFrame()
        by_name: dict[str, _NameSignals] = {}
        if not sig.empty and "underlying" in sig.columns:
            sig["snapshot_date"] = sig["snapshot_date"].astype(str).str[:10]
            rnd_loc = storage.join(base, "signals", "rnd_curves.csv")
            rnd = storage.read_csv(rnd_loc) if storage.exists(rnd_loc) else pd.DataFrame()
            if not rnd.empty:
                rnd["snapshot_date"] = rnd["snapshot_date"].astype(str).str[:10]
            for name, g in sig.groupby("underlying"):
                if as_of is None:
                    day: str | None = str(g["snapshot_date"].max())
                else:
                    day = select_snapshot(g["snapshot_date"].tolist(), as_of,
                                          max_staleness_days=sizing.max_staleness_days)
                if day is None:
                    continue
                row = g[g["snapshot_date"] == day].iloc[-1]
                strikes = cdf = None
                dte = None
                if not rnd.empty:
                    gg = rnd[(rnd["underlying"] == name) & (rnd["snapshot_date"] == day)].sort_values("strike")
                    if not gg.empty:
                        strikes, cdf = gg["strike"].to_numpy(float), gg["cdf"].to_numpy(float)
                        dte = int(gg["dte"].iloc[0]) if "dte" in gg.columns else None
                by_name[str(name)] = _NameSignals(
                    spot=_f(row.get("rnd_spot")), atm_iv=_f(row.get("atm_iv_30d")),
                    skew_25d=_f(row.get("skew_25d_30d")), term_ratio=_f(row.get("term_ratio")),
                    vrp=_f(row.get("vrp_20d")), strikes=strikes, cdf=cdf, snapshot_date=day, dte=dte)
        if as_of is not None:
            for ticker, index in sizing.vol_index.items():
                if ticker in by_name and by_name[ticker].strikes is not None:
                    continue
                fallback = _index_signals(base, index, as_of, sizing.max_staleness_days)
                if fallback is not None:
                    by_name[ticker] = fallback
        if not by_name:
            return None
        return cls(by_name, config)

    # -- accessors ----------------------------------------------------------- #
    def names(self) -> set[str]:
        return set(self._by_name)

    def data_date(self, ticker: str) -> str | None:
        """The ISO date of the name's data: its option snapshot, else its vol-index close; None without data."""
        s = self._by_name.get(ticker)
        return (s.snapshot_date or s.index_date) if s else None

    def atm_iv(self, ticker: str) -> float | None:
        s = self._by_name.get(ticker)
        return s.atm_iv if s else None

    def skew(self, ticker: str) -> float | None:
        s = self._by_name.get(ticker)
        return s.skew_25d if s else None

    def implied_prob(self, ticker: str, move: float) -> float | None:
        """Market-implied probability of an equity move at least as extreme as ``move`` (decimal).

        From the RND: downside ``P(S_T <= spot*(1+move))``, upside ``1 - CDF``; ``None`` when the target
        strike is outside the fitted grid (no extrapolation). From the vol-index fallback: the lognormal
        tail probability. ``None`` for a zero move or a name without data.
        """
        s = self._by_name.get(ticker)
        if s is None or move == 0.0:
            return None
        if s.spot is not None and s.strikes is not None and s.cdf is not None:
            target = s.spot * (1.0 + move)
            if target < s.strikes[0] or target > s.strikes[-1]:
                return None                               # outside the fitted band; don't extrapolate
            cdf_at = float(np.interp(target, s.strikes, s.cdf))
            return cdf_at if move < 0 else max(0.0, 1.0 - cdf_at)
        if s.vol_index is not None and s.atm_iv and move > -1.0:
            return lognormal_tail_prob(move, s.atm_iv, INDEX_T_YEARS)
        return None

    def quantile(self, ticker: str, q: float) -> tuple[float, dict[str, Any]] | None:
        """The option-implied ``q`` quantile return and its source, or None without usable data.

        Args:
            ticker: The underlier.
            q: Probability in (0, 1) (lower tail for a down move, e.g. 0.01; ``1 - q`` for up).

        Returns:
            ``(move, source)``: ``source`` is ``{"source": "options", "snapshot_date", "dte", "q"}`` from
            the RND, or ``{"source": "vol_index", "index", "date", "vol", "q"}`` from the fallback.
        """
        s = self._by_name.get(ticker)
        if s is None:
            return None
        if s.spot is not None and s.strikes is not None and s.cdf is not None:
            move = rnd_quantile(s.strikes.tolist(), s.cdf.tolist(), s.spot, q)
            if move is None:
                return None
            return move, {"source": "options", "snapshot_date": s.snapshot_date, "dte": s.dte, "q": q}
        if s.vol_index is not None and s.atm_iv:
            return lognormal_quantile(q, s.atm_iv, INDEX_T_YEARS), {
                "source": "vol_index", "index": s.vol_index, "date": s.index_date, "vol": s.atm_iv, "q": q}
        return None


def _index_signals(base: str, index: str, as_of: date, max_staleness_days: int) -> _NameSignals | None:
    """The vol-index fallback for one name: the index close on or before ``as_of`` within the staleness."""
    loc = storage.join(base, "vol_indices", f"{index}_History.csv")
    if not storage.exists(loc):
        return None
    df = storage.read_csv(loc)
    if df.empty or not {"date", "close"}.issubset(df.columns):
        return None
    df["date"] = df["date"].astype(str).str[:10]
    day = select_snapshot(df["date"].tolist(), as_of, max_staleness_days=max_staleness_days)
    if day is None:
        return None
    close = _f(df[df["date"] == day]["close"].iloc[-1])
    if close is None or close <= 0:
        return None
    return _NameSignals(spot=None, atm_iv=close / 100.0, skew_25d=None, term_ratio=None, vrp=None,
                        strikes=None, cdf=None, vol_index=index, index_date=day)


def _f(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f
