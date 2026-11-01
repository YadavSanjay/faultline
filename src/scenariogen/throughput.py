"""Physical-throughput scenario calibration (F9, market-intelligence P7).

The whole book is one bet on the AI-capex cycle; ARag gives disclosure and MacroRAG narrative, but
neither gives *physical* verification (design FINDING 9). This corroborates the thesis with hard
data — FRED semiconductor industrial production (IPG3344S) + EIA datacenter-region power demand —
and sizes the ``ai_bust`` scenario by it. It is the **inverse** of the GPR calibration (F8): here a
*decelerating* throughput (the thesis breaking) amplifies the shock, while accelerating throughput
damps it. Mode-(ii): a clamped severity multiplier + a thesis flag, no covariance cost.

    multiplier = clamp(1 + β*(1 - 2*growth_percentile), min_scale, max_scale)   # low growth amplifies
    trigger    = (growth_percentile <= trigger_low)  OR  (yoy < 0)               # contracting = breaking

The primary signal is IPG3344S year-over-year growth, percentiled over its full history ``<= as_of``
(long, robust). EIA power-demand YoY is carried alongside as corroboration. PIT — a re-run
reproduces exactly. No-op (multiplier 1.0) when the feed or config is absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from src.common import storage

logger = logging.getLogger("scenariogen.throughput")


@dataclass(frozen=True)
class ThroughputConfig:
    enabled: bool = True
    beta: float = 0.35
    min_scale: float = 0.85
    max_scale: float = 1.4
    yoy_months: int = 12
    trigger_low_percentile: float = 0.15
    power_respondents: tuple[str, ...] = ("CISO", "ERCO", "PJM", "MISO")

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> ThroughputConfig:
        return cls(
            enabled=bool(cfg.get("enabled", True)),
            beta=float(cfg.get("beta", 0.35)),
            min_scale=float(cfg.get("min_scale", 0.85)),
            max_scale=float(cfg.get("max_scale", 1.4)),
            yoy_months=int(cfg.get("yoy_months", 12)),
            trigger_low_percentile=float(cfg.get("trigger_low_percentile", 0.15)),
            power_respondents=tuple(str(r) for r in cfg.get("power_respondents",
                                                            ["CISO", "ERCO", "PJM", "MISO"])),
        )


@dataclass
class ThroughputResolution:
    """The resolved throughput calibration for one scenario — auditable: growth -> multiplier."""
    as_of: str
    ipg_yoy: float | None
    ipg_percentile: float | None
    power_yoy: float | None
    thesis: str                 # "intact" | "cooling" | "breaking"
    multiplier: float
    trigger: bool
    n_obs: int
    applied: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of, "ipg_yoy": self.ipg_yoy,
                "ipg_percentile": round(self.ipg_percentile, 4) if self.ipg_percentile is not None else None,
                "power_yoy": self.power_yoy, "thesis": self.thesis,
                "multiplier": round(self.multiplier, 4), "trigger": self.trigger,
                "n_obs": self.n_obs, "applied": self.applied, "note": self.note}


def _held(as_of: date, note: str) -> ThroughputResolution:
    return ThroughputResolution(as_of=as_of.isoformat(), ipg_yoy=None, ipg_percentile=None,
                                power_yoy=None, thesis="unknown", multiplier=1.0, trigger=False,
                                n_obs=0, applied=False, note=note)


class ThroughputProvider:
    """Read-only view over the AI-capex throughput series, resolvable point-in-time."""

    def __init__(self, ipg: list[tuple[str, float]], power: list[tuple[str, float]],
                 config: ThroughputConfig):
        self._ipg = ipg            # [(month, IPG3344S)] ascending
        self._power = power        # [(month, total_demand_mwh)] ascending (monthly mean)
        self.cfg = config

    @classmethod
    def load(cls, project_root: Path, config: dict[str, Any] | None = None) -> ThroughputProvider | None:
        cfg = ThroughputConfig.from_dict(config or {})
        if not cfg.enabled:
            return None
        base = storage.join(storage.data_base(project_root), "marketdata", "stress")
        ipg = _read_series(storage.join(base, "fred_throughput.csv"), "date", "IPG3344S")
        power = _read_power(storage.join(base, "eia_power_demand.csv"), cfg.power_respondents)
        if not ipg:
            return None
        return cls(ipg, power, cfg)

    def resolve(self, as_of: date) -> ThroughputResolution:
        ipg = [(d, v) for d, v in self._ipg if d[:10] <= as_of.isoformat()]
        yoy_series = _yoy(ipg, self.cfg.yoy_months)
        if len(yoy_series) < 12:
            return _held(as_of, f"only {len(yoy_series)} IPG YoY obs <= as_of")
        vals = [v for _, v in yoy_series]
        latest = vals[-1]
        n = len(vals)
        percentile = sum(1 for v in vals if v <= latest) / n
        raw = 1.0 + self.cfg.beta * (1.0 - 2.0 * percentile)     # low growth percentile -> amplify
        mult = max(self.cfg.min_scale, min(self.cfg.max_scale, raw))
        trigger = percentile <= self.cfg.trigger_low_percentile or latest < 0.0
        power_yoy = _latest_yoy(self._power, as_of, self.cfg.yoy_months)
        thesis = ("breaking" if (latest < 0.0 or trigger)
                  else "cooling" if percentile < 0.5 else "intact")
        return ThroughputResolution(
            as_of=as_of.isoformat(), ipg_yoy=round(latest, 4), ipg_percentile=percentile,
            power_yoy=round(power_yoy, 4) if power_yoy is not None else None, thesis=thesis,
            multiplier=mult, trigger=trigger, n_obs=n, applied=True,
            note="AI-capex thesis breaking" if trigger else "")


def _yoy(series: list[tuple[str, float]], months: int) -> list[tuple[str, float]]:
    """Year-over-year growth of a monthly series: value / value[months ago] - 1."""
    out: list[tuple[str, float]] = []
    for i in range(months, len(series)):
        prev = series[i - months][1]
        if prev != 0:
            out.append((series[i][0], series[i][1] / prev - 1.0))
    return out


def _latest_yoy(series: list[tuple[str, float]], as_of: date, months: int) -> float | None:
    hist = [(d, v) for d, v in series if d[:10] <= as_of.isoformat()]
    yoy = _yoy(hist, months)
    return yoy[-1][1] if yoy else None


def _read_series(loc: str, date_col: str, value_col: str) -> list[tuple[str, float]]:
    if not storage.exists(loc):
        return []
    df = storage.read_csv(loc)
    if df.empty or date_col not in df.columns or value_col not in df.columns:
        return []
    import pandas as pd

    df = df.sort_values(date_col)
    vals = pd.to_numeric(df[value_col], errors="coerce")
    return [(str(d), float(v)) for d, v in zip(df[date_col], vals, strict=True) if pd.notna(v)]


def _read_power(loc: str, respondents: tuple[str, ...]) -> list[tuple[str, float]]:
    """EIA daily per-respondent demand -> total monthly-mean demand series (ascending)."""
    if not storage.exists(loc):
        return []
    df = storage.read_csv(loc)
    if df.empty or not {"date", "respondent", "demand_mwh"}.issubset(df.columns):
        return []
    import pandas as pd

    df = df[df["respondent"].astype(str).isin(respondents)].copy()
    df["demand_mwh"] = pd.to_numeric(df["demand_mwh"], errors="coerce")
    df = df.dropna(subset=["demand_mwh"])
    daily = df.groupby("date", as_index=False)["demand_mwh"].sum()      # total across respondents
    daily["month"] = daily["date"].astype(str).str[:7]
    monthly = daily.groupby("month", as_index=False)["demand_mwh"].mean().sort_values("month")
    return [(f"{m}-01", float(v)) for m, v in zip(monthly["month"], monthly["demand_mwh"], strict=True)]
