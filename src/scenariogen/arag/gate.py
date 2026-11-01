"""ARag-source GATE: filing freshness + citation coverage (dual-RAG P0).

The macro analogue (`macro/gate.py`) checks chunk *release dates*; ARag chunks are dated by
their filing **fiscal year** instead, so freshness here means "cited by a filing no older than
``freshness_years`` fiscal years before the valuation date". Before an ARag-sourced company
leg enters the stress run, every leg must cite at least ``min_chunks_per_leg`` chunk(s) and
those chunks must be fresh. A failed GATE is fail-loud for the affected scenario (dual-RAG
decision 2): the scenario's generation is stopped rather than run on stale/uncited company
evidence — the provider surfaces the findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .mapping import AragCompanyRisk


@dataclass
class AragGateResult:
    passed: bool
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "findings": list(self.findings)}


class AragGate:
    def __init__(self, as_of: date, *, freshness_years: int = 2,
                 min_chunks_per_leg: int = 1, require_citations: bool = True):
        self.as_of = as_of
        self.freshness_years = freshness_years
        self.min_chunks = min_chunks_per_leg
        self.require_citations = require_citations

    def check(self, legs: list[AragCompanyRisk]) -> AragGateResult:
        findings: list[str] = []
        min_fy = self.as_of.year - self.freshness_years

        for leg in legs:
            if self.require_citations and len(leg.chunk_ids) < self.min_chunks:
                findings.append(f"{leg.factor}: {len(leg.chunk_ids)} citation(s) "
                                f"< required {self.min_chunks}")
            if self.require_citations and not leg.fiscal_years:
                findings.append(f"{leg.factor}: no dated filing behind the citation(s)")
            elif leg.fiscal_years and max(leg.fiscal_years) < min_fy:
                findings.append(f"{leg.factor}: newest citing filing FY{max(leg.fiscal_years)} "
                                f"stale (< FY{min_fy})")
        return AragGateResult(passed=not findings, findings=findings)
