"""ARag REST request/response models (dual-RAG P0).

Mirrors ARag's live v1 Analyst API (`src/api/v1/schemas.py` over there). We consume four
endpoints:

  GET  /api/health                 liveness (audit DB + retrieval pipelines importable)
  GET  /api/v1/coverage            which companies/fiscal-years/forms are ingested
  POST /api/v1/narrative/search    qualitative Risk-Factor chunks (no synthesis) -> the
                                   company-specific risk evidence for a scenario
  POST /api/v1/facts/lookup        deterministic cited fact series (for magnitude calibration)

Only the fields scenariogen needs are parsed; everything is tolerant of missing keys so a
partial ARag response never raises here (the GATE/preflight decide fitness, not the parser).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AragHealth:
    """ARag ``/api/health`` — reachable + retrieval pipelines ready.

    ARag reports ``pipelines`` either as a bool or as a per-pipeline dict, e.g.
    ``{"xbrl": true, "sec10k": true}``. We treat retrieval as ready if *any* pipeline is up
    (``narrative_search`` runs on the sec10k 10-K/Q pipeline); ``pipelines_detail`` keeps the
    raw map for the readiness message.
    """

    status: str = ""
    audit_db: bool = False
    pipelines: bool = False
    pipelines_detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        # Retrieval must be importable for narrative_search to work; the audit DB is
        # observability-only, so it does not gate readiness.
        return self.status.lower() == "ok" and self.pipelines

    @classmethod
    def from_response(cls, d: dict[str, Any]) -> AragHealth:
        raw = d.get("pipelines", False)
        if isinstance(raw, dict):
            ready = any(bool(v) for v in raw.values())
            detail = dict(raw)
        else:
            ready, detail = bool(raw), {}
        return cls(
            status=str(d.get("status", "")),
            audit_db=bool(d.get("audit_db", False)),
            pipelines=ready,
            pipelines_detail=detail,
        )


@dataclass
class AragCoverageItem:
    """One ingested filer (from ``/api/v1/coverage``)."""

    cik: str = ""
    ticker: str = ""
    company_name: str = ""
    fiscal_years: list[int] = field(default_factory=list)
    form_types: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AragCoverageItem:
        return cls(
            cik=str(d.get("cik") or ""),
            ticker=str(d.get("ticker") or ""),
            company_name=str(d.get("company_name") or ""),
            fiscal_years=[int(y) for y in d.get("fiscal_years", []) if y is not None],
            form_types=list(d.get("form_types", [])),
        )


@dataclass
class AragCoverage:
    items: list[AragCoverageItem] = field(default_factory=list)

    def tickers(self) -> set[str]:
        return {it.ticker.upper() for it in self.items if it.ticker}

    def covers(self, ticker: str) -> bool:
        return ticker.upper() in self.tickers()

    @classmethod
    def from_response(cls, d: dict[str, Any]) -> AragCoverage:
        return cls(items=[AragCoverageItem.from_dict(it) for it in d.get("items", [])])


@dataclass
class AragNarrativeChunk:
    """A retrieved qualitative chunk (Risk-Factor / MD&A) with its filing provenance.

    ``chunk_id`` is the citation anchor; ``fiscal_year`` + ``form_type`` + ``accession_no``
    are the freshness/citation keys the GATE checks.
    """

    chunk_id: str
    text: str = ""
    section_path: str = ""
    ticker: str = ""
    cik: str = ""
    fiscal_year: int | None = None
    form_type: str = ""
    accession_no: str = ""
    rerank_score: float = 0.0
    is_expansion: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AragNarrativeChunk:
        fy = d.get("fiscal_year")
        return cls(
            chunk_id=str(d.get("chunk_id", "")),
            text=str(d.get("text") or ""),
            section_path=str(d.get("section_path") or ""),
            ticker=str(d.get("ticker") or ""),
            cik=str(d.get("cik") or ""),
            fiscal_year=int(fy) if fy is not None else None,
            form_type=str(d.get("form_type") or ""),
            accession_no=str(d.get("accession_no") or ""),
            rerank_score=float(d.get("rerank_score") or 0.0),
            is_expansion=bool(d.get("is_expansion", False)),
        )


@dataclass
class AragNarrativeResult:
    """Mirrors ARag's ``/api/v1/narrative/search`` response."""

    ticker: str = ""              # resolved company ticker
    resolution: str = ""          # exact_ticker | fuzzy_name | ambiguous | unresolved
    query: str = ""
    chunks: list[AragNarrativeChunk] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.resolution not in ("", "unresolved", "ambiguous")

    @classmethod
    def from_response(cls, d: dict[str, Any]) -> AragNarrativeResult:
        company = d.get("company") or {}
        return cls(
            ticker=str(company.get("ticker") or "").upper(),
            resolution=str(company.get("resolution") or ""),
            query=str(d.get("query", "")),
            chunks=[AragNarrativeChunk.from_dict(c) for c in d.get("chunks", [])],
        )


@dataclass
class AragFactPoint:
    """One cited deterministic fact (from ``/api/v1/facts/lookup``)."""

    fiscal_year: int
    status: str = ""
    value: float | None = None
    unit: str = ""
    confidence: float = 0.0
    citation: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AragFactPoint:
        val = d.get("value")
        return cls(
            fiscal_year=int(d.get("fiscal_year", 0)),
            status=str(d.get("status") or ""),
            value=float(val) if val is not None else None,
            unit=str(d.get("unit") or ""),
            confidence=float(d.get("confidence") or 0.0),
            citation=dict(d.get("citation") or {}),
        )


@dataclass
class AragFactSeries:
    """Mirrors ARag's ``/api/v1/facts/lookup`` response."""

    ticker: str = ""
    concept: str = ""
    unresolved: bool = False
    series: list[AragFactPoint] = field(default_factory=list)

    @classmethod
    def from_response(cls, d: dict[str, Any]) -> AragFactSeries:
        company = d.get("company") or {}
        return cls(
            ticker=str(company.get("ticker") or "").upper(),
            concept=str(d.get("concept", "")),
            unresolved=bool(d.get("unresolved", False)),
            series=[AragFactPoint.from_dict(p) for p in d.get("series", [])],
        )
