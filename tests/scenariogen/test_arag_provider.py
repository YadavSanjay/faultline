"""Dual-RAG P1 AragCompanyProvider tests: seed -> grounding -> fact-scaling -> gate (fail-loud)."""

from __future__ import annotations

from datetime import date

import pytest
from src.scenariogen.arag.client import AragTransportError
from src.scenariogen.arag.models import (
    AragFactPoint,
    AragFactSeries,
    AragNarrativeChunk,
    AragNarrativeResult,
)
from src.scenariogen.arag.provider import AragCompanyProvider
from src.scenariogen.errors import ScenarioGenerationError

AS_OF = date(2026, 7, 13)


def _narr(ticker: str, fy: int = 2025) -> AragNarrativeResult:
    return AragNarrativeResult(
        ticker=ticker, resolution="exact_ticker", query="risk",
        chunks=[AragNarrativeChunk(chunk_id=f"{ticker}::rf::{fy}", fiscal_year=fy,
                                   form_type="10-K", ticker=ticker)])


class FakeAragClient:
    def __init__(self, *, narrative=None, facts=None, raise_narrative=False):
        self._narr = narrative
        self._facts = facts or {}
        self._raise = raise_narrative
        self.calls: list = []

    def narrative_search(self, company, query, *, section_hint=None, fiscal_years=None, top_k=8):
        self.calls.append(("narr", company))
        if self._raise:
            raise AragTransportError("timeout past 600s ceiling")
        return self._narr or _narr(company.upper())

    def facts_lookup(self, company, concept, fiscal_years, *, dimension=None, fiscal_period="FY"):
        self.calls.append(("facts", concept))
        key = (concept, tuple(sorted((dimension or {}).items())))
        vals = self._facts.get(key, {})
        series = [AragFactPoint(fiscal_year=fy, status="OK" if fy in vals else "NOT_FOUND",
                                value=vals.get(fy)) for fy in fiscal_years]
        return AragFactSeries(ticker=company.upper(), concept=concept, series=series)


def _cfg(companies, required=("arag",)):
    return {
        "gate": {"freshness_years": 2, "min_chunks_per_leg": 1, "require_citations": True},
        "defaults": {"section_hint": "Risk Factors", "top_k": 8, "top_n_chunks": 3},
        "scenarios": {"t_scn": {"required_services": list(required), "companies": companies}},
    }


def _provider(cfg, client):
    return AragCompanyProvider(cfg, as_of=AS_OF, client=client)


def test_seed_from_config_no_calibration():
    cfg = _cfg([{"ticker": "NVDA", "seed_move": -0.30, "narrative_query": "key-man"}])
    res = _provider(cfg, FakeAragClient()).resolve("t_scn")
    assert res.used_arag and res.gate.passed
    assert res.company_drivers == {"eq:NVDA": -0.30}
    src = res.sources["eq:NVDA"]
    assert src["origin"] == "arag" and src["seed_move"] == -0.30
    assert "calibration" not in src                 # no calibration block -> grounding only


def test_calibration_scales_seed():
    cfg = _cfg([{
        "ticker": "NVDA", "seed_move": -0.30, "narrative_query": "AI capex",
        "calibration": {"mode": "share", "concept": "Revenue",
                        "dimension": {"segment": "Data Center"}, "fiscal_year": 2025,
                        "elasticity": 0.6, "baseline": 0.5, "min_scale": 0.5, "max_scale": 1.6},
    }])
    client = FakeAragClient(facts={
        ("Revenue", (("segment", "Data Center"),)): {2025: 88.0},
        ("Revenue", ()): {2025: 100.0},
    })
    res = _provider(cfg, client).resolve("t_scn")
    # -0.30 * 1.228 = -0.3684
    assert abs(res.company_drivers["eq:NVDA"] - (-0.3684)) < 1e-4
    assert res.sources["eq:NVDA"]["calibration"]["applied"] is True


def test_no_companies_returns_empty():
    res = _provider(_cfg([]), FakeAragClient()).resolve("t_scn")
    assert not res.used_arag and res.company_drivers == {}


def test_narrative_timeout_stops_scenario():
    cfg = _cfg([{"ticker": "NVDA", "seed_move": -0.30, "narrative_query": "q"}])
    with pytest.raises(ScenarioGenerationError) as ei:
        _provider(cfg, FakeAragClient(raise_narrative=True)).resolve("t_scn")
    assert ei.value.scenario == "t_scn" and "narrative_search" in ei.value.reason


def test_gate_failure_stops_required_scenario():
    # narrative chunk has no fiscal_year -> "no dated filing" -> gate fail; arag required -> stop
    undated = AragNarrativeResult(ticker="NVDA", resolution="exact_ticker", query="q",
                                  chunks=[AragNarrativeChunk(chunk_id="c1", fiscal_year=None)])
    cfg = _cfg([{"ticker": "NVDA", "seed_move": -0.30, "narrative_query": "q"}])
    with pytest.raises(ScenarioGenerationError) as ei:
        _provider(cfg, FakeAragClient(narrative=undated)).resolve("t_scn")
    assert "GATE" in ei.value.reason


def test_gate_failure_degrades_when_not_required():
    undated = AragNarrativeResult(ticker="NVDA", resolution="exact_ticker", query="q",
                                  chunks=[AragNarrativeChunk(chunk_id="c1", fiscal_year=None)])
    cfg = _cfg([{"ticker": "NVDA", "seed_move": -0.30, "narrative_query": "q"}], required=())
    res = _provider(cfg, FakeAragClient(narrative=undated)).resolve("t_scn")
    assert res.used_arag and res.gate is not None and not res.gate.passed   # kept, flagged


def test_missing_seed_stops_scenario():
    # no seed_move and 't_scn' is not in scenarios.json -> no baseline -> stop
    cfg = _cfg([{"ticker": "NVDA", "narrative_query": "q"}])
    with pytest.raises(ScenarioGenerationError) as ei:
        _provider(cfg, FakeAragClient()).resolve("t_scn")
    assert "seed" in ei.value.reason
