"""Geopolitical-risk (GPR) scenario calibration (F8, market-intelligence P7).

The Taiwan scenario's trigger narrative already says "GPR-index spike" but has no data behind it
(design FINDING 8). This resolves that into a number: from the Caldara-Iacoviello GPR series it
derives, **point-in-time** to the run's as_of, a clamped severity multiplier on the whole scenario
shock and a live-trigger flag — mode-(ii), so it never touches the covariance matrix.

    multiplier = clamp(1 + β*(2*percentile - 1), min_scale, max_scale)
    trigger    = (z-score >= trigger_z)  OR  (percentile >= trigger_percentile)

``percentile`` and ``z`` are over all history ``<= as_of`` (PIT — a re-run reproduces exactly). A
calm regime damps the scenario only mildly (``min_scale`` near 1 keeps the stress test substantial);
an elevated regime amplifies it and flips the trigger to *live*. GPRC_TWN is the monthly Taiwan
sub-index; GPRD the daily global index. Everything degrades to a no-op (multiplier 1.0, trigger
off) when the feed or config is absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.common import storage

logger = logging.getLogger("scenariogen.geopolitical")


@dataclass(frozen=True)
class GeoConfig:
    enabled: bool = True
    default_series: str = "GPRC_TWN"
    beta: float = 0.4
    baseline_percentile: float = 0.5
    min_scale: float = 0.85
    max_scale: float = 1.4
    trigger_z: float = 1.5
    trigger_percentile: float = 0.9

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> GeoConfig:
        return cls(
            enabled=bool(cfg.get("enabled", True)),
            default_series=str(cfg.get("default_series", "GPRC_TWN")),
            beta=float(cfg.get("beta", 0.4)),
            baseline_percentile=float(cfg.get("baseline_percentile", 0.5)),
            min_scale=float(cfg.get("min_scale", 0.85)),
            max_scale=float(cfg.get("max_scale", 1.4)),
            trigger_z=float(cfg.get("trigger_z", 1.5)),
            trigger_percentile=float(cfg.get("trigger_percentile", 0.9)),
        )


@dataclass
class GeoResolution:
    """The resolved GPR calibration for one scenario — auditable: level -> percentile -> multiplier."""
    series: str
    as_of: str
    asof_date: str          # the series date actually used (<= as_of; PIT)
    level: float
    percentile: float
    z: float
    multiplier: float
    trigger: bool
    n_obs: int
    applied: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"series": self.series, "as_of": self.as_of, "asof_date": self.asof_date,
                "level": self.level, "percentile": round(self.percentile, 4), "z": round(self.z, 3),
                "multiplier": round(self.multiplier, 4), "trigger": self.trigger,
                "n_obs": self.n_obs, "applied": self.applied, "note": self.note}


def _held(series: str, as_of: date, note: str) -> GeoResolution:
    return GeoResolution(series=series, as_of=as_of.isoformat(), asof_date="", level=float("nan"),
                         percentile=float("nan"), z=float("nan"), multiplier=1.0, trigger=False,
                         n_obs=0, applied=False, note=note)


class GeopoliticalProvider:
    """Read-only view over the GPR series, resolvable point-in-time."""

    def __init__(self, series: dict[str, list[tuple[str, float]]], config: GeoConfig):
        self._series = series          # name -> [(date, value)] ascending
        self.cfg = config

    @classmethod
    def load(cls, project_root: Path, config: dict[str, Any] | None = None) -> GeopoliticalProvider | None:
        cfg = GeoConfig.from_dict(config or {})
        if not cfg.enabled:
            return None
        base = storage.join(storage.data_base(project_root), "marketdata", "stress")
        series: dict[str, list[tuple[str, float]]] = {}
        _load_file(series, storage.join(base, "gpr_country.csv"), "month")
        _load_file(series, storage.join(base, "gpr_daily.csv"), "date")
        if not series:
            return None
        return cls(series, cfg)

    def has_series(self, name: str) -> bool:
        return name in self._series

    def resolve(self, series: str | None, as_of: date) -> GeoResolution:
        name = series or self.cfg.default_series
        pts = self._series.get(name)
        if not pts:
            return _held(name, as_of, f"series {name!r} not on disk")
        hist = [(d, v) for d, v in pts if d[:10] <= as_of.isoformat()]
        if len(hist) < 12:
            return _held(name, as_of, f"only {len(hist)} obs <= as_of")
        vals = [v for _, v in hist]
        latest_date, latest = hist[-1]
        n = len(vals)
        percentile = sum(1 for v in vals if v <= latest) / n
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = var ** 0.5
        z = (latest - mean) / std if std > 0 else 0.0
        raw = 1.0 + self.cfg.beta * (2.0 * percentile - 1.0)
        mult = max(self.cfg.min_scale, min(self.cfg.max_scale, raw))
        trigger = z >= self.cfg.trigger_z or percentile >= self.cfg.trigger_percentile
        return GeoResolution(series=name, as_of=as_of.isoformat(), asof_date=latest_date[:10],
                             level=float(latest), percentile=percentile, z=z, multiplier=mult,
                             trigger=trigger, n_obs=n, applied=True,
                             note="live geopolitical spike" if trigger else "")


def _load_file(out: dict[str, list[tuple[str, float]]], loc: str, date_col: str) -> None:
    if not storage.exists(loc):
        return
    df = storage.read_csv(loc)
    if df.empty or date_col not in df.columns:
        return
    import pandas as pd

    df = df.sort_values(date_col)
    for col in df.columns:
        if col == date_col:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        pts = [(str(d), float(v)) for d, v in zip(df[date_col], vals, strict=True) if pd.notna(v)]
        if pts:
            out[str(col)] = pts
