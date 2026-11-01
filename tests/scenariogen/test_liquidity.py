"""Tests for the F1 liquidity-horizon + bid-ask exit-value pass (market-intelligence P4).

Offline: the adjuster is driven with in-memory ``TradeLiqMeta`` + a hand-built
``ScenarioStressResult``, so nothing touches data/ or the pricer.
"""

from __future__ import annotations

import math

import pytest
from src.pricer.risk.scenarios import ScenarioPnl
from src.scenariogen.liquidity import (
    ChainSpreadProvider,
    LiquidityAdjuster,
    LiquidityConfig,
    TradeLiqMeta,
)
from src.scenariogen.stress import ScenarioStressResult

CFG = {
    "enabled": True,
    "base_horizon_days": 10,
    "horizon_days": {
        "by_trade_type": {"stock": 10, "equity_option": 40, "corp_bond": 20, "xccy_swap": 20,
                          "cds": 40},
        "cds_index_horizon_days": 20,
        "by_foreign_ccy": {"TWD": 60},
    },
    "bid_ask": {
        "use_chain_spreads": True, "max_relative_half_spread": 0.25,
        "half_spread_bps": {"stock": 3, "corp_bond": 30, "xccy_swap": 40, "cds": 25,
                            "equity_option": 50},
    },
}


def _meta(tid: str, ttype: str, notional: float, *, is_deriv: bool = False,
          underlier: str | None = None, ccy: str | None = None,
          cds_index: bool = False) -> TradeLiqMeta:
    return TradeLiqMeta(trade_id=tid, trade_type=ttype, notional=notional,
                        is_derivative=is_deriv, option_underlier=underlier,
                        foreign_ccy=ccy, cds_is_index=cds_index)


def _adjuster(spreads: ChainSpreadProvider | None = None) -> LiquidityAdjuster:
    return LiquidityAdjuster(LiquidityConfig.from_dict(CFG), spreads)


def test_horizon_resolution_by_class_and_overrides():
    a = _adjuster()
    assert a.horizon_days(_meta("t", "stock", 1.0)) == 10
    assert a.horizon_days(_meta("t", "equity_option", 1.0)) == 40
    assert a.horizon_days(_meta("t", "corp_bond", 1.0)) == 20
    # single-name CDS -> 40; index CDS -> the credit-index horizon 20
    assert a.horizon_days(_meta("t", "cds", 1.0)) == 40
    assert a.horizon_days(_meta("t", "cds", 1.0, cds_index=True)) == 20
    # the USD/TWD XCCY swap: the per-currency override (60) beats the trade-type default (20)
    assert a.horizon_days(_meta("t", "xccy_swap", 1.0, ccy="TWD")) == 60
    # a trade type not in the table falls back to the base horizon
    assert a.horizon_days(_meta("t", "mystery", 1.0)) == 10


def test_scaler_is_sqrt_of_horizon_ratio():
    a = _adjuster()
    ev = a._exit_value(-100.0, base_pv=1000.0, meta=_meta("t", "equity_option", 0.0))
    assert ev.horizon_scaler == pytest.approx(math.sqrt(40 / 10))       # = 2.0


def test_horizon_deepens_losses_only_not_gains():
    a = _adjuster()
    loss = a._exit_value(-100.0, base_pv=0.0, meta=_meta("t", "equity_option", 0.0))
    gain = a._exit_value(+100.0, base_pv=0.0, meta=_meta("t", "equity_option", 0.0))
    assert loss.horizon_pnl == pytest.approx(-200.0)     # loss scaled by 2x
    assert gain.horizon_pnl == pytest.approx(+100.0)     # gain untouched by the horizon


def test_bid_ask_haircut_is_always_a_cost_and_uses_the_right_base():
    a = _adjuster()
    # cash instrument: haircut on |base_pv|, 30bps corp bond -> half-spread applied to 1,000,000
    cash = a._exit_value(0.0, base_pv=1_000_000.0, meta=_meta("t", "corp_bond", 5_000_000.0))
    assert cash.bid_ask_haircut == pytest.approx(30 / 1e4 * 1_000_000.0)   # base_pv, not notional
    # derivative: haircut on |notional| (PV ~ 0), 40bps xccy on 25,000,000 notional
    deriv = a._exit_value(0.0, base_pv=0.0,
                          meta=_meta("t", "xccy_swap", 25_000_000.0, is_deriv=True, ccy="TWD"))
    assert deriv.bid_ask_haircut == pytest.approx(40 / 1e4 * 25_000_000.0)
    assert deriv.exit_pnl == pytest.approx(-deriv.bid_ask_haircut)         # a pure cost


def test_chain_spread_used_for_options_when_available():
    a = _adjuster(ChainSpreadProvider({"NVDA": 0.06}))
    ev = a._exit_value(0.0, base_pv=100_000.0,
                       meta=_meta("t", "equity_option", 0.0, underlier="NVDA"))
    assert ev.spread_source == "chain"
    assert ev.half_spread == pytest.approx(0.06)
    # a name with no chain spread falls back to the config half-spread (50bps)
    ev2 = a._exit_value(0.0, base_pv=100_000.0,
                        meta=_meta("t", "equity_option", 0.0, underlier="ZZZZ"))
    assert ev2.spread_source == "config"
    assert ev2.half_spread == pytest.approx(50 / 1e4)


def test_adjust_aggregates_and_liquidity_cost_is_non_positive():
    a = _adjuster()
    res = ScenarioStressResult(
        scenario="s", severity="adverse", valuation_date="2026-07-10",
        results=[ScenarioPnl("A", "equity", "USD", 1_000_000.0, 900_000.0),   # -100k loss, opt
                 ScenarioPnl("B", "bonds", "USD", 500_000.0, 520_000.0)])     # +20k gain, bond
    meta = {"A": _meta("A", "equity_option", 0.0, underlier="ZZZZ"),
            "B": _meta("B", "corp_bond", 5_000_000.0)}
    out = a.adjust(res, meta)
    assert out["mid_pnl"] == pytest.approx(-80_000.0)
    assert out["exit_pnl"] < out["mid_pnl"]              # exit value is worse than the mid mark
    assert out["liquidity_cost"] <= 0.0
    assert out["horizon_cost"] <= 0.0
    assert out["bid_ask_cost"] >= 0.0
    assert out["max_horizon_days"] == 40


def test_disabled_config_builds_no_adjuster(tmp_path):
    cfg = dict(CFG, enabled=False)
    assert LiquidityAdjuster.build(tmp_path, config=cfg) is None


def test_weighted_half_spread_from_a_chain_frame():
    import pandas as pd
    from src.scenariogen.liquidity import _weighted_half_spread

    df = pd.DataFrame({
        "underlying": ["X"] * 3,
        "bid": [0.98, 1.90, 0.0],           # last row mid=0 -> dropped
        "ask": [1.02, 2.10, 0.0],
        "mid": [1.00, 2.00, 0.0],
        "days_to_expiry": [30, 60, 30],
        "open_interest": [100, 100, 999],
    })
    # rel half-spread: (0.04/1)/2 = 0.02 and (0.20/2)/2 = 0.05, OI-equal -> mean 0.035
    assert _weighted_half_spread(df, max_relative=0.25) == pytest.approx(0.035, abs=1e-9)
