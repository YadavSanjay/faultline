"""Dual-RAG P1 fact-scaling tests: seed * clamp(1 + elasticity*(exposure - baseline))."""

from __future__ import annotations

from src.scenariogen.arag.calibrate import (
    CompanyCalibration,
    resolve_multiplier,
)
from src.scenariogen.arag.models import AragFactPoint, AragFactSeries


class FakeFactsClient:
    """Serves facts_lookup from a {(concept, dim_key): {fy: value}} table."""

    def __init__(self, facts: dict):
        self.facts = facts
        self.calls: list = []

    def facts_lookup(self, company, concept, fiscal_years, *, dimension=None, fiscal_period="FY"):
        self.calls.append((concept, tuple(sorted((dimension or {}).items()))))
        key = (concept, tuple(sorted((dimension or {}).items())))
        vals = self.facts.get(key, {})
        series = [AragFactPoint(fiscal_year=fy, status="OK" if fy in vals else "NOT_FOUND",
                                value=vals.get(fy), citation={"chunk_id": f"{concept}::{fy}"})
                  for fy in fiscal_years]
        return AragFactSeries(ticker=company.upper(), concept=concept, series=series)


def _share_cal(**kw) -> CompanyCalibration:
    base = {"mode": "share", "concept": "Revenue", "dimension": {"segment": "Data Center"},
            "denominator_concept": "Revenue", "fiscal_year": 2025, "elasticity": 0.6,
            "baseline": 0.5, "min_scale": 0.5, "max_scale": 1.6}
    base.update(kw)
    return CompanyCalibration.from_config(base)


def test_share_multiplier_from_two_facts():
    client = FakeFactsClient({
        ("Revenue", (("segment", "Data Center"),)): {2025: 88.0},
        ("Revenue", ()): {2025: 100.0},
    })
    r = resolve_multiplier(client, _share_cal(), ticker="NVDA")
    assert r.applied
    assert abs(r.exposure - 0.88) < 1e-9
    # 1 + 0.6*(0.88-0.5) = 1.228
    assert abs(r.multiplier - 1.228) < 1e-6
    assert r.numerator == 88.0 and r.denominator == 100.0
    assert r.citation_chunk_ids                     # facts carried their citations


def test_share_multiplier_clamped_to_max():
    client = FakeFactsClient({
        ("Revenue", (("segment", "Data Center"),)): {2025: 99.0},
        ("Revenue", ()): {2025: 100.0},
    })
    r = resolve_multiplier(client, _share_cal(elasticity=5.0), ticker="NVDA")
    assert r.multiplier == 1.6                       # clamped at max_scale


def test_missing_numerator_falls_back_to_unity():
    client = FakeFactsClient({("Revenue", ()): {2025: 100.0}})   # no segment fact
    r = resolve_multiplier(client, _share_cal(), ticker="NVDA")
    assert not r.applied and r.multiplier == 1.0 and "unavailable" in r.note


def test_zero_denominator_falls_back():
    client = FakeFactsClient({
        ("Revenue", (("segment", "Data Center"),)): {2025: 88.0},
        ("Revenue", ()): {2025: 0.0},
    })
    r = resolve_multiplier(client, _share_cal(), ticker="NVDA")
    assert not r.applied and r.multiplier == 1.0


def test_raw_mode():
    client = FakeFactsClient({("CustomerConcentration", ()): {2025: 0.7}})
    cal = CompanyCalibration.from_config({"mode": "raw", "concept": "CustomerConcentration",
                                          "fiscal_year": 2025, "elasticity": 1.0, "baseline": 0.5,
                                          "min_scale": 0.5, "max_scale": 2.0})
    r = resolve_multiplier(client, cal, ticker="NVDA")
    assert r.applied and abs(r.multiplier - 1.2) < 1e-9      # 1 + 1.0*(0.7-0.5)
