"""P0c data-quality gate tests: staleness policy, data contracts, quality report."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest
from src.pricer.input.common.quality import (
    DataContractError,
    DataQualityError,
    QualityAction,
    QualityPolicy,
    default_policy,
)
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.loader.schema import validate_columns
from src.pricer.loader.snapshot import MarketDataSnapshot
from src.pricer.loader.staleness import pick_snapshot
from src.pricer.output.result_writer import write_quality_report


# --------------------------------------------------------------------------- #
# Policy parsing
# --------------------------------------------------------------------------- #
def test_policy_from_config_overrides_and_defaults():
    cfg = {"data_quality": {
        "default_action": "warn",
        "kinds": {"ois_curve": {"action": "block", "max_staleness_days": 2}},
    }}
    p = QualityPolicy.from_config(cfg)
    assert p.action_for("ois_curve") == QualityAction.BLOCK
    assert p.action_for("equity_spot") == QualityAction.WARN   # falls to default
    assert p.tolerance_for("ois_curve", 7) == 2                # per-kind override
    assert p.tolerance_for("equity_spot", 7) == 7              # baseline


def test_empty_config_is_permissive_warn():
    p = QualityPolicy.from_config({})
    assert p.action_for("anything") == QualityAction.WARN


# --------------------------------------------------------------------------- #
# Staleness gate
# --------------------------------------------------------------------------- #
_SERIES = pd.Series(pd.to_datetime(["2026-05-01", "2026-05-29"]))


def test_warn_policy_returns_warning_not_raise():
    ctx = ValuationContext(valuation_date=date(2026, 6, 30), max_data_staleness_days=7,
                           data_quality_policy=default_policy())
    chosen, warns = pick_snapshot(_SERIES, ctx, label="SOFR", kind="ois_curve")
    assert chosen == pd.Timestamp("2026-05-29")
    assert any("stale" in w.lower() for w in warns)


def test_block_policy_raises_on_stale():
    policy = QualityPolicy(actions={"ois_curve": QualityAction.BLOCK})
    ctx = ValuationContext(valuation_date=date(2026, 6, 30), max_data_staleness_days=7,
                           data_quality_policy=policy)
    with pytest.raises(DataQualityError, match="stale"):
        pick_snapshot(_SERIES, ctx, label="SOFR", kind="ois_curve")


def test_block_policy_raises_on_missing():
    policy = QualityPolicy(actions={"ois_curve": QualityAction.BLOCK})
    ctx = ValuationContext(valuation_date=date(2020, 1, 1), data_quality_policy=policy)
    with pytest.raises(DataQualityError, match="on/before"):
        pick_snapshot(_SERIES, ctx, label="SOFR", kind="ois_curve")


def test_block_on_one_kind_does_not_affect_another():
    policy = QualityPolicy(actions={"ois_curve": QualityAction.BLOCK})
    ctx = ValuationContext(valuation_date=date(2026, 6, 30), max_data_staleness_days=7,
                           data_quality_policy=policy)
    # equity_spot is not blocked -> warns, no raise
    _, warns = pick_snapshot(_SERIES, ctx, label="px", kind="equity_spot")
    assert warns


# --------------------------------------------------------------------------- #
# Data contracts
# --------------------------------------------------------------------------- #
def test_validate_columns_passes_and_fails():
    good = pd.DataFrame(columns=["as_of_date", "settlement_date", "forward_sofr"])
    validate_columns("rates/pensford_sofr_forward.csv", good)  # no raise

    bad = pd.DataFrame(columns=["as_of_date", "settlement_date"])
    with pytest.raises(DataContractError, match="forward_sofr"):
        validate_columns("rates/pensford_sofr_forward.csv", bad)


def test_unregistered_file_is_skipped():
    validate_columns("stress/move_index.csv", pd.DataFrame(columns=["x"]))  # no contract -> ok


def test_snapshot_read_enforces_contract(tmp_path):
    md = tmp_path / "data" / "marketdata" / "rates"
    md.mkdir(parents=True)
    f = md / "pensford_sofr_forward.csv"
    f.write_text("as_of_date,settlement_date\n2026-05-29,2026-05-29\n", encoding="utf-8")
    snap = MarketDataSnapshot.build({"rates": {"pensford_file": "pensford_sofr_forward.csv"}}, tmp_path)
    with pytest.raises(DataContractError, match="forward_sofr"):
        snap.read_csv(f)


# --------------------------------------------------------------------------- #
# Quality report
# --------------------------------------------------------------------------- #
def test_quality_report_aggregates_statuses(tmp_path):
    rows = [
        {"trade_id": "T1", "asset_class": "rates", "status": "ok", "warnings": []},
        {"trade_id": "T2", "asset_class": "rates", "status": "ok", "warnings": ["stale x"]},
        {"trade_id": "T3", "asset_class": "credit", "status": "BLOCKED: stale SOFR", "warnings": ["stale SOFR"]},
        {"trade_id": "T4", "asset_class": "fx", "status": "FAILED: boom", "warnings": ["boom"]},
    ]
    out = write_quality_report(rows, "PV", date(2026, 5, 29), tmp_path,
                               run_id="r1", snapshot_id="s1", policy={"default_action": "warn"})
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["summary"] == {"n_trades": 4, "n_ok": 2, "n_blocked": 1,
                              "n_failed": 1, "n_with_warnings": 3}
    assert rep["run_id"] == "r1" and rep["snapshot_id"] == "s1"
    assert not list(out.parent.glob(".*.tmp"))
