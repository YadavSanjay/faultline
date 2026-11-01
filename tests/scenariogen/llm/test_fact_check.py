"""Deterministic company-leg fact-check (harness-audit F4) — pure, offline.

Covers the CoverageView parser and every fact_check verdict: OK, CLAMPED (out-of-band move
bounded), UNGROUNDED (no citation), UNCOVERED / STALE (against ARag coverage), plus the
invariants that non-equity legs pass through and the LLM verify block is preserved.
"""

from __future__ import annotations

from src.scenariogen.factor_keys import RETURN_FLOOR
from src.scenariogen.llm.phases.fact_check import (
    CoverageView,
    FactCheckConfig,
    fact_check,
)
from src.scenariogen.llm.schema import CalibratedScenario, Citation, DriverLeg


def _leg(factor, move, *, cited=True, origin="equity"):
    cites = [Citation(chunk_id="x1", source="arag")] if cited else []
    return DriverLeg(factor=factor, move=move, origin=origin, citations=cites)


def _scen(legs, **kw):
    return CalibratedScenario(name="s", legs=legs, **kw)


# --------------------------------------------------------------------------- #
# CoverageView
# --------------------------------------------------------------------------- #

def test_coverage_from_tool_json_parses_items():
    cv = CoverageView.from_tool_json(
        '{"items": [{"ticker": "nvda", "fiscal_years": [2024, 2025]}, {"ticker": "AMD", "fiscal_years": []}]}')
    assert cv.covers("NVDA") and cv.covers("nvda")  # case-insensitive
    assert cv.fiscal_years("NVDA") == frozenset({2024, 2025})
    assert cv.covers("AMD") and not cv.covers("TSM")
    assert not cv.is_empty


def test_coverage_from_bad_json_is_empty():
    assert CoverageView.from_tool_json("not json").is_empty
    assert CoverageView.from_tool_json("{}").is_empty
    assert CoverageView.empty().is_empty


# --------------------------------------------------------------------------- #
# fact_check verdicts
# --------------------------------------------------------------------------- #

def test_in_band_grounded_leg_is_ok():
    res = fact_check(_scen([_leg("eq:NVDA", -0.20)]))
    assert res.n_clamped == 0 and res.n_flagged == 0
    assert res.verdicts[0].ok
    assert res.calibrated.legs[0].move == -0.20  # unchanged
    assert res.calibrated.verification["fact_check"]["n_clamped"] == 0


def test_out_of_band_move_is_clamped_not_dropped():
    res = fact_check(_scen([_leg("eq:NVDA", -5.0)]))  # -500% is implausible
    assert res.n_clamped == 1
    leg = res.calibrated.legs[0]
    assert leg.move == RETURN_FLOOR                  # clamped to the floor, leg retained
    v = res.verdicts[0]
    assert "CLAMPED" in v.verdict
    assert v.original_move == -5.0 and v.clamped_move == RETURN_FLOOR


def test_positive_out_of_band_clamped_to_upper():
    res = fact_check(_scen([_leg("eq:NVDA", 3.0)]), config=FactCheckConfig(max_up_move=0.95))
    assert res.calibrated.legs[0].move == 0.95


def test_ungrounded_leg_flagged_not_dropped():
    res = fact_check(_scen([_leg("eq:NVDA", -0.20, cited=False)]))
    assert res.n_flagged == 1
    assert len(res.calibrated.legs) == 1             # retained
    assert "UNGROUNDED" in res.verdicts[0].verdict


def test_uncovered_ticker_flagged_when_coverage_present():
    cv = CoverageView.from_items([{"ticker": "NVDA", "fiscal_years": [2025]}])
    res = fact_check(_scen([_leg("eq:AMD", -0.20)]), coverage=cv)
    assert res.n_flagged == 1
    assert "UNCOVERED" in res.verdicts[0].verdict


def test_stale_coverage_flagged():
    cv = CoverageView.from_items([{"ticker": "NVDA", "fiscal_years": [2019, 2020]}])
    res = fact_check(_scen([_leg("eq:NVDA", -0.20)]), coverage=cv,
                     as_of_year=2026, config=FactCheckConfig(freshness_years=3))
    assert "STALE" in res.verdicts[0].verdict


def test_fresh_coverage_is_ok():
    cv = CoverageView.from_items([{"ticker": "NVDA", "fiscal_years": [2024, 2025]}])
    res = fact_check(_scen([_leg("eq:NVDA", -0.20)]), coverage=cv, as_of_year=2026)
    assert res.verdicts[0].ok


def test_require_coverage_flags_when_no_data():
    res = fact_check(_scen([_leg("eq:NVDA", -0.20)]),
                     config=FactCheckConfig(require_coverage=True))
    assert "UNCOVERED" in res.verdicts[0].verdict


def test_non_equity_legs_pass_through_untouched():
    res = fact_check(_scen([
        _leg("ir:DGS10", 2.0, origin="macro"),   # huge but not an equity leg -> ignored
        _leg("fx:TWD", -0.08, origin="macro"),
        _leg("credit", 0.05, origin="macro"),
    ]))
    assert res.n_clamped == 0 and res.n_flagged == 0
    assert [leg.move for leg in res.calibrated.legs] == [2.0, -0.08, 0.05]
    assert res.calibrated.verification["fact_check"]["verdicts"] == []


def test_preserves_existing_llm_verify_block():
    scen = _scen([_leg("eq:NVDA", -0.20)], verification={"n_dropped": 1, "verdicts": [{"factor": "x"}]})
    res = fact_check(scen)
    assert res.calibrated.verification["n_dropped"] == 1          # LLM verify block intact
    assert "fact_check" in res.calibrated.verification            # ours added alongside
