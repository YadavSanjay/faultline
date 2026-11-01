"""Crowding / positioning scenario calibration (F5, market-intelligence P7).

Every name in this book is consensus-long; the dominant non-fundamental risk is that everyone owns
the same AI names and leaves together (FINDING 5). Crowding is a positioning *state*, not a
covariance, so Σ cannot represent it. From FINRA **days-to-cover** per name (short position / ADV,
twice-monthly), this derives — PIT to the run's as_of — a clamped multiplier on the ARag
idiosyncratic leg: a crowded name unwinds harder. Mode-(ii): no Σ cost; it composes with the news
multiplier (``idiosyncratic = ARag x news x crowding``).

    multiplier = clamp(1 + β*(2*dtc_percentile - 1), min_scale, max_scale)   # crowded -> amplify

The #6 SMH position is a **short**, so a crowded SMH short is *squeeze* risk — an upside tail a
long-book stress framework will never flag; a short-listed name at/above ``squeeze_percentile`` is
flagged. Everything degrades to a no-op (multiplier 1.0) when the feed or config is absent.

CFTC COT (the other F5 source) is unreachable (§11.5 gate 2) and is dropped; this ships the verified
FINRA short-interest subset.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.common import storage

logger = logging.getLogger("scenariogen.crowding")


@dataclass(frozen=True)
class CrowdingConfig:
    enabled: bool = True
    beta: float = 0.3
    baseline_percentile: float = 0.5
    min_scale: float = 0.8
    max_scale: float = 1.4
    squeeze_percentile: float = 0.8
    short_names: frozenset[str] = frozenset({"SMH"})

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> CrowdingConfig:
        return cls(
            enabled=bool(cfg.get("enabled", True)),
            beta=float(cfg.get("beta", 0.3)),
            baseline_percentile=float(cfg.get("baseline_percentile", 0.5)),
            min_scale=float(cfg.get("min_scale", 0.8)),
            max_scale=float(cfg.get("max_scale", 1.4)),
            squeeze_percentile=float(cfg.get("squeeze_percentile", 0.8)),
            short_names=frozenset(str(n).upper() for n in cfg.get("short_names", ["SMH"])),
        )


@dataclass
class CrowdingLeg:
    ticker: str
    multiplier: float
    days_to_cover: float
    percentile: float
    is_short: bool
    squeeze: bool

    def to_dict(self) -> dict[str, Any]:
        return {"ticker": self.ticker, "multiplier": round(self.multiplier, 4),
                "days_to_cover": self.days_to_cover, "percentile": round(self.percentile, 4),
                "is_short": self.is_short, "squeeze": self.squeeze}


@dataclass
class CrowdingResolution:
    """Per-run crowding provenance: per-name legs + the crowded-short squeeze flags."""
    as_of: str
    legs: dict[str, CrowdingLeg] = field(default_factory=dict)
    squeeze_names: list[str] = field(default_factory=list)
    applied: bool = True
    # ticker -> settlement dates <= as_of (for ``events.crowding_reliable``); not in to_dict.
    history: dict[str, list[str]] = field(default_factory=dict)

    def multiplier_for(self, ticker: str) -> float:
        leg = self.legs.get(ticker.upper())
        return leg.multiplier if leg else 1.0

    def leg_for(self, ticker: str) -> CrowdingLeg | None:
        return self.legs.get(ticker.upper())

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of, "applied": self.applied,
                "legs": {t: leg.to_dict() for t, leg in self.legs.items()},
                "squeeze_names": self.squeeze_names}


class CrowdingProvider:
    """Read-only view over per-name days-to-cover, resolvable point-in-time."""

    def __init__(self, dtc: dict[str, list[tuple[str, float]]], config: CrowdingConfig):
        self._dtc = dtc            # ticker -> [(settlement_date, days_to_cover)] ascending
        self.cfg = config

    @classmethod
    def load(cls, project_root: Path, config: dict[str, Any] | None = None) -> CrowdingProvider | None:
        cfg = CrowdingConfig.from_dict(config or {})
        if not cfg.enabled:
            return None
        loc = storage.join(storage.data_base(project_root), "marketdata", "crowding", "short_interest.csv")
        if not storage.exists(loc):
            return None
        df = storage.read_csv(loc)
        if df.empty or not {"symbol", "settlement_date", "days_to_cover"}.issubset(df.columns):
            return None
        import pandas as pd

        df = df.sort_values("settlement_date")
        dtc: dict[str, list[tuple[str, float]]] = {}
        for _, r in df.iterrows():
            v = pd.to_numeric(r["days_to_cover"], errors="coerce")
            if pd.notna(v):
                dtc.setdefault(str(r["symbol"]).upper(), []).append((str(r["settlement_date"]), float(v)))
        if not dtc:
            return None
        return cls(dtc, cfg)

    def resolve(self, as_of: date) -> CrowdingResolution:
        legs: dict[str, CrowdingLeg] = {}
        squeeze: list[str] = []
        history: dict[str, list[str]] = {}
        for ticker, series in self._dtc.items():
            hist = [(d, v) for d, v in series if d[:10] <= as_of.isoformat()]
            history[ticker] = [d for d, _ in hist]
            if len(hist) < 6:
                continue
            vals = [v for _, v in hist]
            latest = vals[-1]
            pct = sum(1 for v in vals if v <= latest) / len(vals)
            raw = 1.0 + self.cfg.beta * (2.0 * pct - 1.0)
            mult = max(self.cfg.min_scale, min(self.cfg.max_scale, raw))
            is_short = ticker in self.cfg.short_names
            sq = is_short and pct >= self.cfg.squeeze_percentile
            legs[ticker] = CrowdingLeg(ticker, mult, latest, pct, is_short, sq)
            if sq:
                squeeze.append(ticker)
        applied = bool(legs)
        if applied:
            logger.info("  crowding: %d names, squeeze=%s", len(legs), squeeze or "none")
        return CrowdingResolution(as_of=as_of.isoformat(), legs=legs, squeeze_names=squeeze,
                                  applied=applied, history=history)
