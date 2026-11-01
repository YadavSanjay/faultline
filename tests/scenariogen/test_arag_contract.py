"""ARag REST contract test (harness-audit F7).

Flow B depends on ARag (a separate repo) at a pinned location; our models parse ARag's v1
responses leniently, so a field rename in ARag would silently degrade to empty results (fail-loud
only later, at the gate). This test pins ARag's v1 response shapes as recorded fixtures and, for
each, asserts BOTH:

  1. the fixture carries the exact field names the dual-RAG path depends on — the *contract*; and
  2. our models parse the fixture and surface those fields with the right values.

So a field rename is caught in this repo: when the fixtures are re-recorded from ARag's live
/v1 responses (their canonical shape), a renamed/removed field breaks (1); a parser that drifts
from the recorded contract breaks (2).

Regenerate the fixtures under tests/scenariogen/fixtures/arag/ from ARag's actual
GET /api/health, GET /api/v1/coverage, POST /api/v1/narrative/search, POST /api/v1/facts/lookup.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.scenariogen.arag.models import (
    AragCoverage,
    AragFactSeries,
    AragHealth,
    AragNarrativeResult,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "arag"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _require(d: dict, fields: set[str], where: str) -> None:
    missing = fields - set(d)
    assert not missing, f"ARag contract drift: {where} missing field(s) {missing} (present: {set(d)})"


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #

def test_health_contract():
    raw = _load("health.json")
    _require(raw, {"status", "pipelines"}, "health")     # preflight readiness depends on these
    h = AragHealth.from_response(raw)
    assert h.ok and h.status == "ok" and h.pipelines


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #

def test_coverage_contract():
    raw = _load("coverage.json")
    assert raw.get("items"), "coverage contract: items[] required"
    _require(raw["items"][0], {"ticker", "fiscal_years"}, "coverage.items[]")  # F4 fact-check deps
    cov = AragCoverage.from_response(raw)
    assert cov.covers("NVDA") and cov.covers("AMD")
    nvda = next(it for it in cov.items if it.ticker == "NVDA")
    assert nvda.fiscal_years == [2023, 2024, 2025]


# --------------------------------------------------------------------------- #
# narrative/search
# --------------------------------------------------------------------------- #

def test_narrative_search_contract():
    raw = _load("narrative_search.json")
    _require(raw, {"company", "chunks"}, "narrative_search")
    _require(raw["company"], {"ticker", "resolution"}, "narrative_search.company")
    # The gate + provenance mapping depend on exactly these chunk fields.
    _require(raw["chunks"][0], {"chunk_id", "fiscal_year", "form_type", "accession_no"},
             "narrative_search.chunks[]")
    res = AragNarrativeResult.from_response(raw)
    assert res.resolved and res.ticker == "NVDA"
    chunk = res.chunks[0]
    assert chunk.chunk_id == "nvda::2025::rf::07"
    assert chunk.fiscal_year == 2025 and chunk.form_type == "10-K"
    assert chunk.accession_no == "0001045810-25-000010"


# --------------------------------------------------------------------------- #
# facts/lookup
# --------------------------------------------------------------------------- #

def test_facts_lookup_contract():
    raw = _load("facts_lookup.json")
    _require(raw, {"company", "concept", "series"}, "facts_lookup")
    # calibrate.py depends on status/value/fiscal_year (+ citation for provenance).
    _require(raw["series"][0], {"fiscal_year", "status", "value", "citation"},
             "facts_lookup.series[]")
    fs = AragFactSeries.from_response(raw)
    assert fs.ticker == "NVDA" and not fs.unresolved
    pt = next(p for p in fs.series if p.fiscal_year == 2025)
    assert pt.status == "OK" and pt.value == 130497.0
    assert pt.citation.get("chunk_id") == "nvda::2025::fin::12"
