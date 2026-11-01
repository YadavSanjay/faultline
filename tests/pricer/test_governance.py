"""P4 model-governance tests: golden-PV gate, IPV controls, inventory coverage."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.governance.golden import GoldenBaseline, check_golden
from src.pricer.governance.ipv import IpvEngine
from src.pricer.governance.model_inventory import ModelInventory
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import load_portfolio

VDATE = date(2026, 10, 6)   # golden baseline is pinned to the live portfolio default (rebaselined 2026-10-06)


@pytest.fixture(scope="module")
def engine() -> PricingEngine:
    return PricingEngine(load_pricer_config(), get_project_root())


@pytest.fixture
def ctx() -> ValuationContext:
    return ValuationContext(valuation_date=VDATE)


# --------------------------------------------------------------------------- #
# Golden / regression gate
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_golden_baseline_passes(engine, ctx):
    pvs = {o.trade_id: o.result.pv
           for o in engine.price_portfolio(load_portfolio(), ctx) if o.result is not None}
    g = check_golden(pvs, engine.snapshot_id)
    assert g.snapshot_matches, "snapshot drifted from the golden baseline"
    assert g.passed, [b.as_dict() for b in g.breaks]
    assert g.n_checked == 38


def test_golden_detects_a_break():
    base = GoldenBaseline.load()
    pvs = dict(base.pvs)
    pvs["TRADE_22"] *= 1.01                       # 1% move -> must be caught
    g = check_golden(pvs, base.snapshot_id, base)
    assert not g.passed
    assert any(b.trade_id == "TRADE_22" for b in g.breaks)


def test_golden_flags_snapshot_mismatch():
    base = GoldenBaseline.load()
    g = check_golden(base.pvs, "deadbeefdeadbeef", base)
    assert not g.snapshot_matches and not g.passed


# --------------------------------------------------------------------------- #
# Independent price verification
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_ipv_all_controls_pass(engine, ctx):
    rep = IpvEngine(engine).verify(load_portfolio(), ctx)
    assert rep.n_total >= 25
    assert rep.all_passed, [c.as_dict() for c in rep.failures()]


@pytest.mark.needs_market_data
def test_ipv_has_independent_method_controls(engine, ctx):
    rep = IpvEngine(engine).verify(load_portfolio(), ctx)
    kinds = {c.kind for c in rep.controls}
    assert {"identity", "independent_method", "sanity"} <= kinds
    # the bermudan tree-vs-FD cross-check is present and passes
    berm = [c for c in rep.controls if "tree" in c.control.lower()]
    assert berm and all(c.passed for c in berm)


# --------------------------------------------------------------------------- #
# Model inventory coverage
# --------------------------------------------------------------------------- #
def test_model_inventory_covers_every_traded_type():
    inv = ModelInventory.load()
    tts = sorted({t.trade_type.value for t in load_portfolio()})
    covered, missing = inv.coverage(tts)
    assert missing == [], f"ungoverned trade-types: {missing}"
    assert set(covered) == set(tts)


def test_model_entry_has_required_fields():
    inv = ModelInventory.load()
    e = inv.entries["cds"]
    assert e.model and e.engine and e.approved_use
    assert e.assumptions and e.limitations and e.ipv_controls


# --------------------------------------------------------------------------- #
# End-to-end gate
# --------------------------------------------------------------------------- #
@pytest.mark.needs_market_data
def test_governance_runner_passes(tmp_path):
    from src.pricer.governance_runner import run_governance_to
    # the report goes to tmp_path so the test never writes into the real data/pricer store
    rep = run_governance_to(tmp_path, valuation_date=VDATE)
    assert rep.passed
    assert rep.golden.passed and rep.ipv.all_passed and not rep.inventory_missing
