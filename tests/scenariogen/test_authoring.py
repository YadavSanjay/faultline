"""Dual-RAG P4a authoring tests: one doc -> three governed configs (validate + apply + export)."""

from __future__ import annotations

import json

import pytest
from src.scenariogen.authoring import (
    AuthoringError,
    apply_set,
    export_current,
    validate,
)
from src.scenariogen.config import (
    load_arag_config,
    load_macro_config,
    load_scenario_config,
)


@pytest.fixture
def root(tmp_path):
    cdir = tmp_path / "configs" / "scenariogen"
    cdir.mkdir(parents=True)
    (cdir / "scenarios.json").write_text(json.dumps({
        "description": "", "severity_tiers": {"mild": 1.0, "adverse": 1.5, "severe": 2.0},
        "scenarios": {"existing": {"narrative": "e", "trigger": "t", "method": "conditional_covariance",
                                   "horizon_days": 10, "drivers": {"eq:XLK": {"move_pct": -10}},
                                   "overlays": {}}}}))
    (cdir / "macro.json").write_text(json.dumps({
        "connection": {"enabled": True}, "gate": {}, "units": {"move_base": 100.0},
        "scenarios": {"existing": {"synthesis_question": "q", "filters": {}}}}))
    (cdir / "arag.json").write_text(json.dumps({
        "connection": {"enabled": True}, "gate": {}, "defaults": {},
        "scenarios": {"existing": {"required_services": ["macro"], "companies": []}}}))
    return tmp_path


def _doc(**over):
    scn = {
        "narrative": "AI capex bust", "trigger": "capex framing", "horizon_days": 10,
        "drivers": {"eq:XLK": {"move_pct": -20}}, "overlays": {"eqvol": {"move_pts": 8}},
        "required_services": ["macro", "arag"],
        "macro": {"synthesis_question": "quantify credit/vol shocks", "filters": {"factor_tags": ["credit_spread"]},
                  "expect_fields": ["IG_OAS", "VIX"]},
        "companies": [{"ticker": "NVDA", "seed_move": -0.35, "narrative_query": "AI demand",
                       "fiscal_years": [2024, 2025]}],
    }
    scn.update(over)
    return {"scenarios": {"newscn": scn}}


# --- validation ------------------------------------------------------------ #
def test_validate_ok():
    validate(_doc())            # no raise


def test_validate_missing_narrative():
    with pytest.raises(AuthoringError) as ei:
        validate(_doc(narrative=""))
    assert any("narrative" in e for e in ei.value.errors)


def test_validate_bad_factor_and_service():
    with pytest.raises(AuthoringError) as ei:
        validate(_doc(drivers={"bogus:X": {"move_pct": 1}}, required_services=["macro", "xxx"]))
    joined = " ".join(ei.value.errors)
    assert "invalid factor" in joined and "unknown required service" in joined


def test_validate_macro_required_without_question():
    with pytest.raises(AuthoringError):
        validate(_doc(macro={"filters": {}}))     # required macro but no synthesis_question


def test_validate_arag_required_without_companies():
    with pytest.raises(AuthoringError):
        validate(_doc(companies=[]))


def test_validate_company_without_seed_or_driver():
    with pytest.raises(AuthoringError) as ei:
        validate(_doc(companies=[{"ticker": "AMD", "narrative_query": "q"}]))  # no seed, no eq:AMD driver
    assert any("AMD needs" in e for e in ei.value.errors)


def test_validate_company_seed_from_driver_ok():
    validate(_doc(drivers={"eq:XLK": {"move_pct": -20}, "eq:AMD": {"move_pct": -25}},
                  companies=[{"ticker": "AMD", "narrative_query": "q"}]))   # seed via eq:AMD driver


# --- apply (upsert) -------------------------------------------------------- #
def test_apply_upsert_expands_three_configs(root):
    summary = apply_set(_doc(), root=root)
    assert set(summary["scenarios_now"]) == {"existing", "newscn"}   # upsert kept 'existing'

    scen = load_scenario_config(root)["scenarios"]["newscn"]
    assert scen["drivers"] == {"eq:XLK": {"move_pct": -20}} and scen["horizon_days"] == 10

    macro = load_macro_config(root)["scenarios"]["newscn"]
    assert macro["synthesis_question"].startswith("quantify") and macro["expect_fields"] == ["IG_OAS", "VIX"]

    arag = load_arag_config(root)["scenarios"]["newscn"]
    assert arag["required_services"] == ["macro", "arag"]
    assert arag["companies"][0]["ticker"] == "NVDA" and arag["expect_tickers"] == ["NVDA"]


def test_apply_replace_set_swaps_everything(root):
    apply_set(_doc(), root=root, replace_set=True)
    assert load_scenario_config(root)["scenarios"] .keys() == {"newscn"}
    assert "existing" not in load_macro_config(root)["scenarios"]
    assert "existing" not in load_arag_config(root)["scenarios"]
    # top-level blocks preserved
    assert load_macro_config(root)["connection"]["enabled"] is True
    assert load_scenario_config(root)["severity_tiers"]["severe"] == 2.0


def test_apply_without_macro_removes_stale_macro_entry(root):
    apply_set(_doc(), root=root)                 # newscn now has a macro entry
    assert "newscn" in load_macro_config(root)["scenarios"]
    # re-author newscn as arag-only (a company with its own seed, no macro block)
    doc2 = {"scenarios": {"newscn": {"narrative": "keyman", "required_services": ["arag"],
                                     "drivers": {"eq:NVDA": {"move_pct": -30}},
                                     "companies": [{"ticker": "NVDA", "narrative_query": "keyman"}]}}}
    apply_set(doc2, root=root)
    assert "newscn" not in load_macro_config(root)["scenarios"]   # stale macro entry pruned


def test_export_roundtrips(root):
    apply_set(_doc(), root=root)
    doc = export_current(root)
    assert set(doc["scenarios"]) == {"existing", "newscn"}
    assert doc["scenarios"]["newscn"]["macro"]["synthesis_question"].startswith("quantify")
    assert doc["scenarios"]["newscn"]["companies"][0]["ticker"] == "NVDA"
    # re-applying the exported doc is idempotent
    apply_set(doc, root=root)
    assert set(load_scenario_config(root)["scenarios"]) == {"existing", "newscn"}
