"""Dual-RAG P2 attribution tests: what each service contributed vs the authored baseline."""

from __future__ import annotations

from src.scenariogen.attribution import build_attribution


def _dualrag_ai_bust() -> dict:
    return {
        "scenario": "ai_bust", "severity": "adverse", "severity_scale": 1.5,
        "driver_sources": {"eq:XLK": {"origin": "static"},
                           "credit": {"origin": "macrorag", "macro_field": "IG_OAS"}},
        "overlay_sources": {"eq:NVDA": {"origin": "arag", "mode": "overlay", "applied_move": -0.6455},
                            "eq:AMD": {"origin": "arag", "mode": "overlay"}},
        "used_macro": True, "used_arag": True,
        "macro": {
            "scenario": "ai_bust",
            "drivers": {"eq:NVDA": -0.35, "eq:XLK": -0.30, "credit": 0.009},
            "sources": {"eq:NVDA": {"origin": "static"}, "eq:XLK": {"origin": "static"},
                        "credit": {"origin": "macrorag", "macro_field": "IG_OAS",
                                   "chunk_ids": ["igoas::01"], "rationale": "spreads widen"}},
            "used_macro": True, "gate": {"passed": True, "findings": []},
            "macro_summary": "credit widens on AI-capex rollover",
            "world_view_id": "macro-2026-07-11"},
        "arag": {
            "scenario": "ai_bust",
            "company_drivers": {"eq:NVDA": -0.4304, "eq:AMD": -0.30},
            "sources": {
                "eq:NVDA": {"origin": "arag", "ticker": "NVDA", "risk_factor": "AI capex dependence",
                            "chunk_ids": ["n1", "n2", "n3"], "fiscal_years": [2024, 2025],
                            "seed_move": -0.35,
                            "calibration": {"applied": True, "multiplier": 1.2296,
                                            "exposure": 0.8827, "concept": "Revenue"}},
                "eq:AMD": {"origin": "arag", "ticker": "AMD", "risk_factor": "AI demand",
                           "chunk_ids": ["a1"], "fiscal_years": [2024], "seed_move": -0.30,
                           "calibration": None}},
            "used_arag": True, "gate": {"passed": True, "findings": []}},
    }


_AUTHORED = {"drivers": {"eq:NVDA": {"move_pct": -35}, "eq:XLK": {"move_pct": -30}}, "overlays": {}}


def test_macro_contribution_new_leg_with_citation():
    attr = build_attribution(_dualrag_ai_bust(), _AUTHORED)
    m = attr.macrorag
    assert m.applied and m.status == "applied" and m.world_view_id == "macro-2026-07-11"
    assert [leg.factor for leg in m.legs] == ["credit"]        # only macrorag-origin legs
    credit = m.legs[0]
    assert credit.baseline_move is None and credit.delta is None   # authored had no credit leg
    assert credit.citations == ["igoas::01"] and credit.detail["macro_field"] == "IG_OAS"
    assert m.n_citations == 1


def test_arag_contribution_scaled_and_grounded():
    attr = build_attribution(_dualrag_ai_bust(), _AUTHORED)
    a = attr.arag
    assert a.applied and a.gate_passed
    legs = {leg.factor: leg for leg in a.legs}
    nvda = legs["eq:NVDA"]
    assert nvda.role == "overlay"
    assert abs(nvda.baseline_move - (-0.35)) < 1e-9            # authored eq:NVDA seed
    assert abs(nvda.delta - (-0.4304 - (-0.35))) < 1e-6       # ARag scaled it more negative
    assert nvda.detail["calibration"]["applied"] and len(nvda.citations) == 3
    amd = legs["eq:AMD"]
    assert amd.baseline_move is None                          # not in authored -> service-added
    assert not (amd.detail["calibration"] or {})             # grounding-only


def test_headlines_mention_both_services():
    attr = build_attribution(_dualrag_ai_bust(), _AUTHORED)
    text = " | ".join(attr.headline_lines)
    assert "MacroRAG set credit" in text
    assert "ARag overlay eq:NVDA" in text and "share" in text  # fact-scaling described
    assert "ARag overlay eq:AMD" in text and "seed" in text    # grounding-only described


def test_macro_gate_failed_status():
    dr = _dualrag_ai_bust()
    dr["used_macro"] = False
    dr["macro"]["used_macro"] = False
    dr["macro"]["gate"] = {"passed": False, "findings": ["credit: chunk stale"]}
    attr = build_attribution(dr, _AUTHORED)
    assert not attr.macrorag.applied and attr.macrorag.status == "gate_failed"
    assert any("GATE" in ln for ln in attr.headline_lines)


def test_macro_reduces_authored_rate_shock_shows_negative_delta():
    dr = {
        "scenario": "rate_capital", "severity": "adverse", "severity_scale": 1.5,
        "driver_sources": {}, "overlay_sources": {}, "used_macro": True, "used_arag": False,
        "macro": {"scenario": "rate_capital", "drivers": {"ir:DGS10": 0.001},
                  "sources": {"ir:DGS10": {"origin": "macrorag", "macro_field": "UST_10Y",
                                           "chunk_ids": ["tbac::015"]}},
                  "used_macro": True, "gate": {"passed": True, "findings": []},
                  "world_view_id": "macro-2026-07-11"},
        "arag": None,
    }
    authored = {"drivers": {"ir:DGS10": {"move_bp": 100}}, "overlays": {}}
    attr = build_attribution(dr, authored)
    leg = attr.macrorag.legs[0]
    assert abs(leg.baseline_move - 0.01) < 1e-9              # authored +100bp
    assert abs(leg.final_move - 0.001) < 1e-9               # MacroRAG +10bp
    assert leg.delta < 0                                     # MacroRAG reduced the shock
    assert attr.arag.status == "not_configured"
