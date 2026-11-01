"""ARag Risk-Factor evidence -> canonical company-leg translation (dual-RAG P0).

MacroRAG supplies the *macro* legs of a scenario; ARag supplies the **company** legs — a
per-name ``eq:<TICKER>`` idiosyncratic return grounded in what the company itself discloses in
its SEC 10-K/Q Risk Factors. ARag's REST returns qualitative *evidence* (chunks) and
deterministic *facts*, not a numeric shock, so an ``AragCompanyRisk`` pairs a company leg's
**move** (decided by the P1 provider's calibration policy) with the ARag chunks/facts that
justify and cite it. This module is the translation/provenance surface; P1 owns the magnitude.

Notes:
  * ARag covers per-name equity only — never macro legs (those stay MacroRAG's).
  * The move is a decimal return (negative = a sell-off); it is floored at ``RETURN_FLOOR``
    downstream by the materializer, so no floor is applied here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import factor_keys as fk
from .models import AragNarrativeChunk, AragNarrativeResult


def eq_key(ticker: str) -> str:
    """``NVDA`` -> ``eq:NVDA`` (canonical, upper-cased)."""
    return f"{fk.EQUITY_PREFIX}{ticker.strip().upper()}"


@dataclass
class AragCompanyRisk:
    """A canonical company leg sourced from ARag, with its filing citations.

    ``factor`` is ``eq:<TICKER>``; ``move`` is a decimal return; ``risk_factor`` is a short
    label of the disclosed risk theme (e.g. "Taiwan/TSMC supply concentration"); ``chunk_ids``
    and ``fiscal_years`` are the citing 10-K/Q chunks and their filing years.
    """

    factor: str
    move: float
    ticker: str
    risk_factor: str = ""
    chunk_ids: list[str] = field(default_factory=list)
    fiscal_years: list[int] = field(default_factory=list)
    form_types: list[str] = field(default_factory=list)
    rationale: str = ""

    def as_source(self) -> dict[str, Any]:
        return {"origin": "arag", "ticker": self.ticker, "risk_factor": self.risk_factor,
                "chunk_ids": list(self.chunk_ids), "fiscal_years": list(self.fiscal_years),
                "form_types": list(self.form_types), "rationale": self.rationale}


def company_risk_from_result(result: AragNarrativeResult, move: float, *, risk_factor: str = "",
                             rationale: str = "", top_n_chunks: int = 3) -> AragCompanyRisk:
    """Assemble an ``AragCompanyRisk`` for a company from an ARag narrative-search result.

    Takes the top ``top_n_chunks`` retrieved chunks (already rerank-ordered by ARag) as the
    leg's citations and folds their fiscal-years/form-types into the provenance. ``move`` is
    the calibrated magnitude supplied by the caller (P1 policy); this function only wires the
    grounding, so the leg is deterministic given (result, move).
    """
    chunks: list[AragNarrativeChunk] = list(result.chunks)[: max(0, top_n_chunks)]
    fiscal_years = sorted({c.fiscal_year for c in chunks if c.fiscal_year is not None})
    form_types = sorted({c.form_type for c in chunks if c.form_type})
    ticker = result.ticker.upper()
    return AragCompanyRisk(
        factor=eq_key(ticker), move=float(move), ticker=ticker,
        risk_factor=risk_factor or (result.query[:80]),
        chunk_ids=[c.chunk_id for c in chunks if c.chunk_id],
        fiscal_years=fiscal_years, form_types=form_types, rationale=rationale,
    )
