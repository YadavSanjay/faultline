"""News freshness + minimum-coverage gate (A3, market-intelligence P3).

Mirrors ``arag/gate.py``: decides whether the news feed is fit to *apply* at all. If the newest
8-K is older than ``max_staleness_days`` (the whole feed is stale), or fewer than
``min_names_with_events`` names have any allowlisted event in the window, news is held out — every
multiplier is 1.0 — rather than modulating scenarios on stale or thin data. Graceful, not fail-loud:
a held gate is a no-op, and a scenario run without news is byte-identical to before.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class NewsGateConfig:
    max_staleness_days: int = 200
    min_names_with_events: int = 3

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> NewsGateConfig:
        g = cfg.get("gate", {})
        return cls(
            max_staleness_days=int(g.get("max_staleness_days", 200)),
            min_names_with_events=int(g.get("min_names_with_events", 3)),
        )


@dataclass
class NewsGateResult:
    ok: bool
    newest_filing: str | None
    staleness_days: int | None
    n_names_with_events: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "newest_filing": self.newest_filing,
                "staleness_days": self.staleness_days,
                "n_names_with_events": self.n_names_with_events, "reason": self.reason}


def check_gate(newest_filing: str | None, n_names_with_events: int, as_of: date,
               cfg: NewsGateConfig) -> NewsGateResult:
    stale: int | None = None
    if newest_filing:
        try:
            stale = (as_of - date.fromisoformat(newest_filing[:10])).days
        except ValueError:
            stale = None
    if newest_filing is None:
        return NewsGateResult(False, None, None, n_names_with_events, "no 8-K events on disk")
    if stale is not None and stale > cfg.max_staleness_days:
        return NewsGateResult(False, newest_filing, stale, n_names_with_events,
                              f"stale: newest 8-K {stale}d old > {cfg.max_staleness_days}d")
    if n_names_with_events < cfg.min_names_with_events:
        return NewsGateResult(False, newest_filing, stale, n_names_with_events,
                              f"thin: {n_names_with_events} names with events < {cfg.min_names_with_events}")
    return NewsGateResult(True, newest_filing, stale, n_names_with_events, "ok")
