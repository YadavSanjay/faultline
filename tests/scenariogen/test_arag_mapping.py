"""Dual-RAG ARag mapping tests: narrative result -> canonical company leg."""

from __future__ import annotations

from src.scenariogen.arag.mapping import (
    AragCompanyRisk,
    company_risk_from_result,
    eq_key,
)
from src.scenariogen.arag.models import AragNarrativeChunk, AragNarrativeResult


def _chunk(cid: str, fy: int, form: str = "10-K") -> AragNarrativeChunk:
    return AragNarrativeChunk(chunk_id=cid, fiscal_year=fy, form_type=form, ticker="NVDA")


def test_eq_key_canonicalizes_ticker():
    assert eq_key("nvda") == "eq:NVDA"
    assert eq_key(" amd ") == "eq:AMD"


def test_company_risk_from_result_wires_grounding():
    result = AragNarrativeResult(
        ticker="NVDA", resolution="exact_ticker", query="Taiwan/TSMC supply concentration",
        chunks=[_chunk("nvda::2025::rf::07", 2025), _chunk("nvda::2024::rf::05", 2024),
                _chunk("nvda::2023::rf::02", 2023, "10-Q"), _chunk("nvda::2022::rf::01", 2022)],
    )
    leg = company_risk_from_result(result, move=-0.30, risk_factor="Taiwan/TSMC concentration",
                                   rationale="disclosed foundry dependence", top_n_chunks=3)
    assert isinstance(leg, AragCompanyRisk)
    assert leg.factor == "eq:NVDA" and leg.ticker == "NVDA" and leg.move == -0.30
    # only the top-3 rerank-ordered chunks are cited
    assert leg.chunk_ids == ["nvda::2025::rf::07", "nvda::2024::rf::05", "nvda::2023::rf::02"]
    assert leg.fiscal_years == [2023, 2024, 2025]
    assert set(leg.form_types) == {"10-K", "10-Q"}
    src = leg.as_source()
    assert src["origin"] == "arag" and src["ticker"] == "NVDA"
    assert src["risk_factor"] == "Taiwan/TSMC concentration"


def test_company_risk_falls_back_to_query_label():
    result = AragNarrativeResult(ticker="AMD", resolution="exact_ticker",
                                 query="reliance on TSMC foundries", chunks=[_chunk("a::1", 2025)])
    leg = company_risk_from_result(result, move=-0.2)
    assert leg.risk_factor == "reliance on TSMC foundries"     # query used when no label given
