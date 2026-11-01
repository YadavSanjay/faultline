"""Item code + salience -> per-name news multiplier (A3, market-intelligence P3).

The mapping is the design's clamped, mode-(ii) calibration (§2.5)::

    event_score(t)   = Σ over allowlisted 8-K events in [as_of-window, as_of]
                        of  weight · 0.5^(age_days / halflife)          # age-decayed
    news_multiplier  = clamp(1 + β_event·event_score + β_tone·(-tone_z), min_scale, max_scale)

A recent, heavily-weighted 8-K (a 5.02 officer departure, a 4.02 restatement) and a negative GDELT
tone z-score both push the multiplier up; nothing pushes it past ``max_scale`` or below
``min_scale``. Salience != severity, so tone is signed (``-tone_z``: negative tone raises the shock)
and the multiplier is clamped in both directions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class NewsMapConfig:
    beta_event: float = 0.15
    beta_tone: float = 0.10
    window_days: int = 90
    decay_halflife_days: float = 30.0
    min_scale: float = 0.8
    max_scale: float = 1.5

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> NewsMapConfig:
        return cls(
            beta_event=float(cfg.get("beta_event", 0.15)),
            beta_tone=float(cfg.get("beta_tone", 0.10)),
            window_days=int(cfg.get("window_days", 90)),
            decay_halflife_days=float(cfg.get("decay_halflife_days", 30.0)),
            min_scale=float(cfg.get("min_scale", 0.8)),
            max_scale=float(cfg.get("max_scale", 1.5)),
        )


@dataclass
class EventRow:
    """One allowlisted 8-K event (from sec_events.csv), already PIT-filtered to <= as_of."""
    filing_date: str
    item_codes: str
    accession: str
    weight: float


@dataclass
class NewsLeg:
    """The resolved news calibration for one name — auditable: events + tone -> multiplier."""
    ticker: str
    multiplier: float
    event_score: float
    tone_z: float | None
    n_events: int
    accessions: list[str] = field(default_factory=list)
    item_codes: list[str] = field(default_factory=list)
    applied: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ticker": self.ticker, "multiplier": round(self.multiplier, 4),
                "event_score": round(self.event_score, 4), "tone_z": self.tone_z,
                "n_events": self.n_events, "accessions": list(self.accessions),
                "item_codes": list(self.item_codes), "applied": self.applied, "note": self.note}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def event_score(events: list[EventRow], as_of: date, cfg: NewsMapConfig) -> tuple[float, list[EventRow]]:
    """Age-decayed weighted sum over events within the trailing window; also returns those in-window."""
    score = 0.0
    in_window: list[EventRow] = []
    for e in events:
        try:
            age = (as_of - date.fromisoformat(e.filing_date[:10])).days
        except ValueError:
            continue
        if age < 0 or age > cfg.window_days:
            continue
        decay = 0.5 ** (age / cfg.decay_halflife_days) if cfg.decay_halflife_days > 0 else 1.0
        score += e.weight * decay
        in_window.append(e)
    return score, in_window


def news_multiplier(ticker: str, events: list[EventRow], tone_z: float | None,
                    as_of: date, cfg: NewsMapConfig) -> NewsLeg:
    """Compose the clamped multiplier for one name from its 8-K events + GDELT tone."""
    score, in_window = event_score(events, as_of, cfg)
    raw = 1.0 + cfg.beta_event * score + cfg.beta_tone * (-(tone_z or 0.0))
    mult = _clamp(raw, cfg.min_scale, cfg.max_scale)
    codes: list[str] = []
    for e in in_window:
        codes.extend(c for c in e.item_codes.split(",") if c and c not in codes)
    applied = mult != 1.0 or bool(in_window) or tone_z is not None
    return NewsLeg(
        ticker=ticker, multiplier=mult, event_score=score, tone_z=tone_z,
        n_events=len(in_window), accessions=[e.accession for e in in_window],
        item_codes=codes, applied=applied,
        note="" if applied else "no events in window and no tone")
