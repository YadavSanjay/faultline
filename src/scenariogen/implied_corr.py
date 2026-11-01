"""Implied-correlation regime switch on Σ (F7, market-intelligence P5).

The dual-RAG model splits a shock into systematic + idiosyncratic; *how much* of a driver
propagates to the rest of the book is governed by Σ — a **historical** estimate steering a
**forward-looking** scenario. `covariance.json` picks the propagation strength by hand via a fixed
`shrinkage` (Ledoit-Wolf toward the diagonal: high shrinkage damps cross-factor covariances → a
shock stays idiosyncratic; low shrinkage lets it propagate broadly → systemic).

Options markets price forward correlation directly. CBOE's implied-correlation indices (COR1M/
COR3M) and the S&P dispersion index (DSPX) are on disk (ingested in P0). F7 reads the *current*
implied-correlation percentile and maps it onto the shrinkage knob, turning a hand-chosen constant
into a **market-implied regime** (design FINDING 7):

  elevated implied correlation (high percentile) → **low** shrinkage  → propagate broadly (systemic)
  low implied correlation      (low percentile)  → **high** shrinkage → keep the shock idiosyncratic

Costs **zero** Σ dimensionality — it modulates an existing knob, it does not add a factor. Fully
reversible: `enabled: false` (or an absent series) falls back to the fixed config `shrinkage`, so
the estimate is byte-identical to pre-F7. The band is centered near the current 0.5 so authored
scenarios stay promotable at both ends of the regime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import storage

# The dispersion index moves *opposite* to correlation (high dispersion = low correlation), so if
# it is ever used as the source its percentile is inverted before mapping to shrinkage.
_DISPERSION_SOURCES = frozenset({"DSPX"})


@dataclass(frozen=True)
class RegimeState:
    """The market-implied Σ regime resolved for a run (stamped into covariance provenance)."""
    enabled: bool
    source: str
    asof: str
    value: float
    percentile: float          # 0..1, correlation-oriented (already inverted for DSPX)
    regime: str                # "systemic" | "normal" | "idiosyncratic"
    shrinkage: float           # the shrinkage this regime implies (capped at max_shrinkage)
    n_obs: int
    max_shrinkage: float = 1.0  # cap on the regime shrinkage (crisis-sigma-tiers); 1.0 = no cap

    def as_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "source": self.source, "asof": self.asof,
                "value": self.value, "percentile": self.percentile, "regime": self.regime,
                "shrinkage": self.shrinkage, "n_obs": self.n_obs, "max_shrinkage": self.max_shrinkage}


@dataclass(frozen=True)
class ImpliedCorrelationRegime:
    """Resolves a market-implied shrinkage from the latest implied-correlation percentile."""

    source: str                 # vol-index file stem, e.g. "COR3M"
    series: pd.Series           # date-indexed close
    shrink_systemic: float      # shrinkage at the elevated-correlation end (low value)
    shrink_idiosyncratic: float # shrinkage at the low-correlation end (high value)
    systemic_pct: float         # percentile >= this -> "systemic"
    idiosyncratic_pct: float    # percentile <= this -> "idiosyncratic"
    max_shrinkage: float = 1.0  # cap so a calm regime can't damp systematic co-moves below it

    @classmethod
    def load(cls, project_root: Path | None, config: dict[str, Any] | None = None) -> ImpliedCorrelationRegime | None:
        cfg = config or {}
        source = str(cfg.get("source", "COR3M"))
        loc = storage.join(storage.data_base(project_root), "marketdata", "options",
                           "vol_indices", f"{source}_History.csv")
        if not storage.exists(loc):
            return None
        df = storage.read_csv(loc)
        if df.empty or "close" not in df.columns:
            return None
        dc = "date" if "date" in df.columns else df.columns[0]
        s = pd.to_numeric(df.set_index(dc)["close"], errors="coerce").dropna()
        # drop the flat synthetic head some CBOE series carry (constant open=high=low=close)
        s = s[s.diff().fillna(1.0) != 0.0]
        if len(s) < 2:
            return None
        return cls(
            source=source, series=s,
            shrink_systemic=float(cfg.get("shrink_systemic", 0.4)),
            shrink_idiosyncratic=float(cfg.get("shrink_idiosyncratic", 0.65)),
            systemic_pct=float(cfg.get("systemic_pct", 0.66)),
            idiosyncratic_pct=float(cfg.get("idiosyncratic_pct", 0.33)),
            max_shrinkage=float(cfg.get("max_shrinkage", 1.0)),
        )

    def resolve(self, lookback: int | None = None, *, as_of: date | None = None) -> RegimeState:
        """Resolve the regime from the implied-correlation level on or before ``as_of``.

        Args:
            lookback: Keep only the last ``lookback`` observations for the percentile (None = all).
            as_of: Point-in-time cutoff (inclusive) applied before the lookback tail, so a replay
                reads the level known on its as-of date; None reads the latest level.

        Returns:
            The resolved ``RegimeState``; its ``asof`` is the date of the level actually used.

        Raises:
            ValueError: No observation on or before ``as_of``.
        """
        s = series_as_of(self.series, as_of)
        if s.empty:
            raise ValueError(f"no {self.source} observation on or before {as_of}")
        s = s.tail(lookback) if lookback else s
        latest = float(s.iloc[-1])
        pct = float((s <= latest).mean())               # correlation percentile of today's level
        if self.source in _DISPERSION_SOURCES:
            pct = 1.0 - pct                              # dispersion is inverse to correlation
        # elevated correlation (pct high) -> low shrinkage; low correlation -> high shrinkage
        shrinkage = regime_shrinkage(pct, shrink_systemic=self.shrink_systemic,
                                     shrink_idiosyncratic=self.shrink_idiosyncratic,
                                     max_shrinkage=self.max_shrinkage)
        regime = ("systemic" if pct >= self.systemic_pct
                  else "idiosyncratic" if pct <= self.idiosyncratic_pct else "normal")
        return RegimeState(
            enabled=True, source=self.source, asof=str(pd.Timestamp(s.index[-1]).date()),
            value=latest, percentile=pct, regime=regime, shrinkage=shrinkage, n_obs=len(s),
            max_shrinkage=self.max_shrinkage)


def resolve_regime(project_root: Path | None, config: dict[str, Any] | None,
                   fixed_shrinkage: float, *, as_of: date | None = None) -> RegimeState:
    """Resolve the Σ regime for a run.

    Returns a disabled ``RegimeState`` carrying the fixed config ``shrinkage`` when F7 is off or its
    series is absent — so the caller uses one code path and the estimate stays byte-identical to
    pre-F7 when disabled. ``as_of`` (a replay's point-in-time cutoff) reads the level on or before
    that date; with fewer than two observations by then the regime is disabled, as for a short
    series. None reads the latest level.
    """
    cfg = config or {}
    if not cfg.get("enabled", False):
        return _disabled(fixed_shrinkage)
    regime = ImpliedCorrelationRegime.load(project_root, cfg)
    if regime is None:
        return _disabled(fixed_shrinkage, source=str(cfg.get("source", "COR3M")))
    if as_of is not None and len(series_as_of(regime.series, as_of)) < 2:
        return _disabled(fixed_shrinkage, source=regime.source)
    return regime.resolve(lookback=cfg.get("lookback_days"), as_of=as_of)


def _disabled(shrinkage: float, source: str = "") -> RegimeState:
    return RegimeState(enabled=False, source=source, asof="", value=float("nan"),
                       percentile=float("nan"), regime="fixed", shrinkage=shrinkage, n_obs=0)


def regime_shrinkage(percentile: float, *, shrink_systemic: float, shrink_idiosyncratic: float,
                     max_shrinkage: float) -> float:
    """Maps an implied-correlation percentile onto the Σ shrinkage, capped at ``max_shrinkage``.

    Args:
        percentile: Implied-correlation percentile of today's level, in ``[0, 1]``.
        shrink_systemic: Shrinkage at the elevated-correlation end (percentile 1).
        shrink_idiosyncratic: Shrinkage at the low-correlation end (percentile 0).
        max_shrinkage: Cap on the resulting shrinkage.

    Returns:
        ``min(shrink_idiosyncratic - percentile * (shrink_idiosyncratic - shrink_systemic), max_shrinkage)``.

    Raises:
        ValueError: ``percentile`` outside ``[0, 1]``.
    """
    if not 0.0 <= percentile <= 1.0:
        raise ValueError(f"implied-correlation percentile must be in [0, 1], got {percentile}")
    mapped = shrink_idiosyncratic - percentile * (shrink_idiosyncratic - shrink_systemic)
    return min(mapped, max_shrinkage)


def series_as_of(series: pd.Series, as_of: date | None) -> pd.Series:
    """Return the part of a date-indexed series on or before ``as_of``.

    Args:
        series: A series whose index holds dates (strings or timestamps).
        as_of: The point-in-time cutoff (inclusive); ``None`` keeps the whole series.

    Returns:
        A copy holding the entries whose index date is ``<= as_of``, in the original order
        (the whole series for ``as_of=None``). The input is not mutated.

    Raises:
        ValueError: When the index cannot be parsed as dates.
    """
    if as_of is None:
        return series.copy()
    try:
        days = pd.to_datetime(series.index)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"series index cannot be parsed as dates: {exc}") from exc
    return series[days <= pd.Timestamp(as_of)].copy()
