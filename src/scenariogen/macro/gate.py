"""Macro-source GATE: freshness + citation coverage (S4, catalog §10).

Before MacroRAG-sourced drivers enter the stress run, their supporting chunks must be
*fresh* and *cited*: hold a scenario's macro override back unless every driver cites at
least ``min_chunks_per_driver`` chunk(s) and those chunks are within ``freshness_days`` of
the valuation date. A failed GATE means the provider falls back to the authored static
drivers (or, in the dual-RAG path when macro is required, the scenario is stopped) — the run
never silently uses stale or uncited macro numbers.

Market-episode cards (``doc_type == "market_episode"``) are exempt from the freshness
comparison only: they are realized moves measured from market data over a stated horizon, and
their ``release_date`` is the end of the measurement window, so age says nothing about whether
they apply. They must still be cited, present and dated. Every other chunk, including one an
``analog`` field cites, is checked for freshness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .mapping import MacroDriver
from .models import MacroChunk

EPISODE_DOC_TYPE = "market_episode"     # MacroRAG's measured stress-episode cards


@dataclass
class MacroGateResult:
    passed: bool
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "findings": list(self.findings)}


class MacroGate:
    def __init__(self, as_of: date, *, freshness_days: int = 120,
                 min_chunks_per_driver: int = 1, require_citations: bool = True):
        self.as_of = as_of
        self.freshness_days = freshness_days
        self.min_chunks = min_chunks_per_driver
        self.require_citations = require_citations

    def check(self, drivers: list[MacroDriver], chunks: list[MacroChunk]) -> MacroGateResult:
        by_id = {c.chunk_id: c for c in chunks}
        findings: list[str] = []

        for d in drivers:
            if self.require_citations and len(d.chunk_ids) < self.min_chunks:
                findings.append(f"{d.factor}: {len(d.chunk_ids)} citation(s) "
                                f"< required {self.min_chunks}")
            for cid in d.chunk_ids:
                chunk = by_id.get(cid)
                age = self._age_days(chunk)
                if age is None or chunk is None:
                    findings.append(f"{d.factor}: chunk {cid} missing/undated")
                elif age > self.freshness_days and chunk.doc_type != EPISODE_DOC_TYPE:
                    findings.append(f"{d.factor}: chunk {cid} stale ({age}d "
                                    f"> {self.freshness_days}d)")
        return MacroGateResult(passed=not findings, findings=findings)

    def _age_days(self, chunk: MacroChunk | None) -> int | None:
        if chunk is None or not chunk.release_date:
            return None
        try:
            rel = date.fromisoformat(chunk.release_date[:10])
        except ValueError:
            return None
        return (self.as_of - rel).days
