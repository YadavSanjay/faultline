"""Tests for the F2 cash / collateral line (market-intelligence P4).

Offline: the estimator is driven with in-memory ``TradeLiqMeta`` + a hand-built
``ScenarioStressResult`` + a factor vector, so nothing touches data/ or the pricer.
The headline property is that net cash outflow can differ in sign from net P&L.
"""

from __future__ import annotations

import pytest
from src.pricer.risk.scenarios import ScenarioPnl
from src.scenariogen.collateral import CollateralConfig, CollateralEstimator, _vol_stress
from src.scenariogen.liquidity import TradeLiqMeta
from src.scenariogen.stress import ScenarioStressResult

CFG = {
    "enabled": True,
    "margined_trade_types": ["ois_swap", "cds", "xccy_swap"],
    "margined_overrides": {"SHORT": True, "LONGEQ": False},
    "im": {"bps_of_notional": {"cds": 50, "ois_swap": 15, "xccy_swap": 60, "stock": 15},
           "vol_stress_floor": 0.25, "vol_stress_cap": 3.0, "eqvol_base": 0.20},
    "funding": {"collateral_haircut_bps": 25},
}


def _meta(tid: str, ttype: str, notional: float) -> TradeLiqMeta:
    return TradeLiqMeta(trade_id=tid, trade_type=ttype, notional=notional,
                        is_derivative=ttype != "stock", option_underlier=None,
                        foreign_ccy=None, cds_is_index=False)


def _estimator(funding_bps: float | None = -12.0) -> CollateralEstimator:
    return CollateralEstimator(CollateralConfig.from_dict(CFG), funding_bps)


def test_margined_classification_types_and_overrides():
    cfg = CollateralConfig.from_dict(CFG)
    assert cfg.is_margined(_meta("t", "cds", 1.0)) is True            # by type
    assert cfg.is_margined(_meta("t", "corp_bond", 1.0)) is False     # not a margined type
    assert cfg.is_margined(_meta("SHORT", "stock", 1.0)) is True      # override forces margined
    assert cfg.is_margined(_meta("LONGEQ", "stock", 1.0)) is False    # override forces unmargined


def test_vol_stress_takes_the_larger_shock_and_clamps():
    cfg = CollateralConfig.from_dict(CFG)
    assert _vol_stress({}, cfg) == pytest.approx(0.25)                  # floor when no vol shock
    assert _vol_stress({"eqvol": 0.20}, cfg) == pytest.approx(1.0)      # 0.20 / eqvol_base 0.20
    assert _vol_stress({"ratesvol": 5.0}, cfg) == pytest.approx(3.0)    # cap


def test_vm_flow_is_negative_pnl_for_margined_only():
    e = _estimator()
    res = ScenarioStressResult(
        scenario="s", severity="adverse", valuation_date="2026-07-10",
        results=[ScenarioPnl("D", "credit", "USD", 0.0, -100_000.0),      # margined loss
                 ScenarioPnl("BOND", "bonds", "USD", 0.0, -500_000.0)])   # unmargined loss
    meta = {"D": _meta("D", "cds", 10_000_000.0),
            "BOND": _meta("BOND", "corp_bond", 5_000_000.0)}
    out = e.estimate(res, meta, {"eqvol": 0.20})
    # only the CDS is margined -> a 100k loss means posting 100k of VM
    assert out["vm_call"] == pytest.approx(100_000.0)
    assert {t["trade_id"] for t in out["trades"]} == {"D"}              # bond excluded from cash


def test_net_cash_outflow_can_oppose_pnl_sign():
    """The design's headline: the book loses on the mark while margined hedges receive cash."""
    e = _estimator()
    res = ScenarioStressResult(
        scenario="s", severity="severe", valuation_date="2026-07-10",
        results=[ScenarioPnl("SHORT", "equity", "USD", 0.0, +8_000_000.0),   # margined gain
                 ScenarioPnl("LONGEQ", "equity", "USD", 0.0, -20_000_000.0)])  # unmargined loss
    meta = {"SHORT": _meta("SHORT", "stock", 5_000_000.0),
            "LONGEQ": _meta("LONGEQ", "stock", 30_000_000.0)}
    out = e.estimate(res, meta, {})
    assert out["total_pnl"] == pytest.approx(-12_000_000.0)             # book loses on the mark
    # the margined short GAINS -> receives VM (negative outflow); IM increase is small & positive
    assert out["vm_call"] == pytest.approx(-8_000_000.0)
    assert out["im_increase"] > 0.0
    assert out["net_cash_outflow"] < 0.0                               # net cash RECEIVED
    # opposite signs: P&L < 0 but cash flow > 0 (received)
    assert out["total_pnl"] < 0.0 < -out["net_cash_outflow"]


def test_haircut_only_applies_when_posting_and_funding_context_passes_through():
    e = _estimator(funding_bps=-12.0)
    # a net poster (margined loss) accrues the collateral haircut on the posted amount
    res = ScenarioStressResult(
        scenario="s", severity="adverse", valuation_date="2026-07-10",
        results=[ScenarioPnl("D", "credit", "USD", 0.0, -1_000_000.0)])
    out = e.estimate(res, {"D": _meta("D", "cds", 10_000_000.0)}, {})
    assert out["collateral_haircut"] == pytest.approx(1_000_000.0 * 25 / 1e4)
    assert out["funding_spread_bps"] == pytest.approx(-12.0)


def test_disabled_config_builds_no_estimator(tmp_path):
    cfg = dict(CFG, enabled=False)
    assert CollateralEstimator.build(tmp_path, config=cfg) is None
