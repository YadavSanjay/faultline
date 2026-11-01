"""S4 macro-GATE tests: freshness + citation coverage."""

from __future__ import annotations

from datetime import date

from src.scenariogen.macro.gate import MacroGate
from src.scenariogen.macro.mapping import MacroDriver
from src.scenariogen.macro.models import MacroChunk

AS_OF = date(2026, 6, 18)


def _gate(**kw):
    return MacroGate(AS_OF, **kw)


def test_fresh_cited_driver_passes():
    drivers = [MacroDriver("fx:TWD", 0.08, "USDTWD", ["c1"])]
    chunks = [MacroChunk("c1", release_date="2026-06-10")]
    assert _gate(freshness_days=120).check(drivers, chunks).passed


def test_stale_chunk_fails():
    drivers = [MacroDriver("credit", 0.01, "IG_OAS", ["c1"])]
    chunks = [MacroChunk("c1", release_date="2025-01-01")]
    res = _gate(freshness_days=120).check(drivers, chunks)
    assert not res.passed and any("stale" in f for f in res.findings)


def test_missing_citation_fails():
    res = _gate().check([MacroDriver("fx:TWD", 0.08, "USDTWD", [])], [])
    assert not res.passed and any("citation" in f for f in res.findings)


def test_undated_chunk_fails():
    drivers = [MacroDriver("fx:TWD", 0.08, "USDTWD", ["c1"])]
    res = _gate().check(drivers, [MacroChunk("c1", release_date="")])
    assert not res.passed and any("missing/undated" in f for f in res.findings)


def test_require_citations_off_allows_uncited():
    res = _gate(require_citations=False).check(
        [MacroDriver("fx:TWD", 0.08, "USDTWD", [])], [])
    assert res.passed
