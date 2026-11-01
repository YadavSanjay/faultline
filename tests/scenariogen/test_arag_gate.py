"""Dual-RAG ARag GATE tests: filing freshness (by fiscal year) + citation coverage."""

from __future__ import annotations

from datetime import date

from src.scenariogen.arag.gate import AragGate
from src.scenariogen.arag.mapping import AragCompanyRisk

AS_OF = date(2026, 7, 13)


def _leg(chunk_ids: list[str], fiscal_years: list[int]) -> AragCompanyRisk:
    return AragCompanyRisk(factor="eq:NVDA", move=-0.30, ticker="NVDA",
                           chunk_ids=chunk_ids, fiscal_years=fiscal_years)


def test_fresh_cited_leg_passes():
    res = AragGate(AS_OF, freshness_years=2).check([_leg(["c1"], [2025])])
    assert res.passed and not res.findings


def test_uncited_leg_fails():
    res = AragGate(AS_OF).check([_leg([], [])])
    assert not res.passed
    assert any("citation" in f for f in res.findings)


def test_stale_filing_fails():
    # newest citing filing FY2023, as-of 2026, freshness 2y -> min FY2024 -> stale
    res = AragGate(AS_OF, freshness_years=2).check([_leg(["c1"], [2022, 2023])])
    assert not res.passed
    assert any("stale" in f for f in res.findings)


def test_cited_but_undated_fails():
    res = AragGate(AS_OF).check([_leg(["c1"], [])])
    assert not res.passed
    assert any("no dated filing" in f for f in res.findings)


def test_require_citations_off_is_lenient():
    res = AragGate(AS_OF, require_citations=False).check([_leg([], [])])
    assert res.passed
