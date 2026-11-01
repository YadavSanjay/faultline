"""NewsEventProvider — serves the 8-K + GDELT feeds into scenariogen (A3, P3).

Reads the two compact CSVs the marketdata news runner writes (``sec_events.csv``, optional
``gdelt_salience.csv``) through the dual-mode storage façade, and resolves them — **point-in-time**
to the run's ``as_of`` date — into a per-name clamped multiplier on the ARag idiosyncratic leg,
plus two direct wins (design §2.5):

  * **live triggers** — a ``5.02`` (officer departure) 8-K in the window turns ``keyman_nvda`` from
    a hypothetical into a *live* scenario;
  * **earnings dates** — a ``2.02`` (results) 8-K date populates the scenario event calendar.

PIT discipline (reflexivity mitigation, §2.7): only events with ``filing_date <= as_of`` and the
latest tone z-score at/prior to ``as_of`` are used, so a re-run reproduces exactly. Everything
degrades gracefully — no feed, or a held gate, yields multiplier 1.0 everywhere (a no-op).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from src.common import storage

from .gate import NewsGateConfig, NewsGateResult, check_gate
from .mapping import EventRow, NewsLeg, NewsMapConfig, news_multiplier

logger = logging.getLogger("scenariogen.news")


@dataclass
class NewsResolution:
    """Per-run news provenance: the gate verdict, per-name legs, and the two direct wins."""
    as_of: str
    gate: NewsGateResult
    legs: dict[str, NewsLeg] = field(default_factory=dict)          # ticker -> NewsLeg
    live_triggers: dict[str, list[str]] = field(default_factory=dict)  # item -> [tickers]
    earnings: dict[str, list[str]] = field(default_factory=dict)    # ticker -> [dates]
    # PIT inputs for theme-weighted legs (``events.theme_news_multiplier``): events filed <= as_of,
    # the tone z-score at/prior to as_of, and the mapping parameters.
    pit_events: dict[str, list[EventRow]] = field(default_factory=dict)
    tone_z: dict[str, float | None] = field(default_factory=dict)
    map_cfg: NewsMapConfig | None = None

    @property
    def applied(self) -> bool:
        return self.gate.ok

    def multiplier_for(self, ticker: str) -> float:
        if not self.gate.ok:
            return 1.0
        leg = self.legs.get(ticker.upper())
        return leg.multiplier if leg else 1.0

    def leg_for(self, ticker: str) -> NewsLeg | None:
        return self.legs.get(ticker.upper())

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of, "applied": self.applied, "gate": self.gate.to_dict(),
                "legs": {t: leg.to_dict() for t, leg in self.legs.items()},
                "live_triggers": self.live_triggers, "earnings": self.earnings}


class NewsEventProvider:
    """Read-only view over the news feeds, resolvable point-in-time."""

    def __init__(self, events_by_ticker: dict[str, list[EventRow]], tone: dict[str, list[tuple[str, float]]],
                 map_cfg: NewsMapConfig, gate_cfg: NewsGateConfig,
                 keyman_items: list[str], earnings_items: list[str]):
        self._events = events_by_ticker
        self._tone = tone                          # ticker -> [(date, tone_z)] ascending
        self._map = map_cfg
        self._gate = gate_cfg
        self._keyman = set(keyman_items)
        self._earnings = set(earnings_items)

    @classmethod
    def load(cls, project_root: Path, config: dict[str, Any] | None = None) -> NewsEventProvider | None:
        """Build from the on-disk news feeds; returns ``None`` if the 8-K spine is absent or news off."""
        cfg = config or {}
        if not cfg.get("enabled", True):
            return None
        base = storage.join(storage.data_base(project_root), "marketdata", "news")
        sec_loc = storage.join(base, "sec_events.csv")
        if not storage.exists(sec_loc):
            return None
        sec = storage.read_csv(sec_loc)
        if sec.empty or "ticker" not in sec.columns:
            return None
        events: dict[str, list[EventRow]] = {}
        for _, r in sec.iterrows():
            events.setdefault(str(r["ticker"]).upper(), []).append(EventRow(
                filing_date=str(r["filing_date"]), item_codes=str(r.get("item_codes", "")),
                accession=str(r.get("accession", "")), weight=float(r.get("weight", 0.0) or 0.0)))

        tone: dict[str, list[tuple[str, float]]] = {}
        gd_loc = storage.join(base, "gdelt_salience.csv")
        if storage.exists(gd_loc):
            gd = storage.read_csv(gd_loc)
            if not gd.empty and {"ticker", "date", "tone_z_63d"}.issubset(gd.columns):
                gd = gd.sort_values("date")
                for _, r in gd.iterrows():
                    z = r.get("tone_z_63d")
                    if z is not None and str(z) not in ("", "nan"):
                        try:
                            tone.setdefault(str(r["ticker"]).upper(), []).append((str(r["date"]), float(z)))
                        except (TypeError, ValueError):
                            continue

        return cls(events, tone, NewsMapConfig.from_dict(cfg), NewsGateConfig.from_dict(cfg),
                   list(cfg.get("keyman_items", ["5.02"])), list(cfg.get("earnings_items", ["2.02"])))

    def _tone_z(self, ticker: str, as_of: date) -> float | None:
        """The most recent tone z-score at/prior to ``as_of`` (PIT)."""
        series = self._tone.get(ticker, [])
        picked: float | None = None
        for d, z in series:                        # ascending -> last <= as_of wins
            try:
                if date.fromisoformat(d[:10]) <= as_of:
                    picked = z
            except ValueError:
                continue
        return picked

    def resolve(self, as_of: date) -> NewsResolution:
        """Resolve every name's news multiplier + the direct-win signals, point-in-time to ``as_of``."""
        legs: dict[str, NewsLeg] = {}
        pit_events: dict[str, list[EventRow]] = {}
        tone_z: dict[str, float | None] = {}
        live: dict[str, list[str]] = {}
        earnings: dict[str, list[str]] = {}
        newest: str | None = None
        n_with_events = 0

        for ticker, evs in self._events.items():
            pit = [e for e in evs if e.filing_date[:10] <= as_of.isoformat()]
            pit_events[ticker] = pit
            tone_z[ticker] = self._tone_z(ticker, as_of)
            leg = news_multiplier(ticker, pit, tone_z[ticker], as_of, self._map)
            legs[ticker] = leg
            if leg.n_events:
                n_with_events += 1
            for e in pit:
                if e.filing_date[:10] > (newest or ""):
                    newest = e.filing_date[:10]
                codes = {c for c in e.item_codes.split(",") if c}
                for item in codes & self._keyman:
                    # keyman trigger only counts inside the decay window (a live, current signal)
                    if e in _in_window(pit, as_of, self._map.window_days):
                        live.setdefault(item, [])
                        if ticker not in live[item]:
                            live[item].append(ticker)
                if codes & self._earnings:
                    earnings.setdefault(ticker, []).append(e.filing_date[:10])

        gate = check_gate(newest, n_with_events, as_of, self._gate)
        if not gate.ok:
            logger.info("  news: gate held (%s) -> multipliers 1.0", gate.reason)
        else:
            logger.info("  news: applied — %d names with events, newest 8-K %s (%dd)",
                        n_with_events, gate.newest_filing, gate.staleness_days or 0)
        for ticker in self._tone:                  # GDELT-only names still carry their tone
            tone_z.setdefault(ticker, self._tone_z(ticker, as_of))
        return NewsResolution(as_of=as_of.isoformat(), gate=gate, legs=legs,
                              live_triggers=live, earnings=earnings,
                              pit_events=pit_events, tone_z=tone_z, map_cfg=self._map)


def _in_window(events: list[EventRow], as_of: date, window_days: int) -> list[EventRow]:
    out: list[EventRow] = []
    for e in events:
        try:
            age = (as_of - date.fromisoformat(e.filing_date[:10])).days
        except ValueError:
            continue
        if 0 <= age <= window_days:
            out.append(e)
    return out
