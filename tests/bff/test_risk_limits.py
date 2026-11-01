"""BFF risk-limit utilisation (`/risk/limits`)."""

from __future__ import annotations

from pathlib import Path

from bff_helpers import write_pricer_config, write_risk_report
from ui.bff.app import risk

DATE = "2026-03-01"


def test_limit_row_utilisation_and_breach() -> None:
    row = risk._limit_row("VaR[0.99]", "var", "0.99", observed=250.0, ceiling=1000.0)
    assert row["utilization"] == 0.25
    assert row["headroom"] == 750.0
    assert row["breached"] is False
    assert row["key"] == "0.99"

    breach = risk._limit_row("CS01", "sensitivity", "CS01", observed=120.0, ceiling=100.0)
    assert breach["breached"] is True
    assert breach["utilization"] == 1.2


def test_update_limit_edits_existing_ceiling(bff_root: Path) -> None:
    write_pricer_config(bff_root, {"var": {"0.99": 1_000_000_000.0}, "sensitivities": {"CS01": 100_000_000.0}})
    out = risk.update_limit("var", "0.99", 500_000_000.0)
    assert out["ok"] is True
    assert out["old_ceiling"] == 1_000_000_000.0
    assert out["new_ceiling"] == 500_000_000.0
    # persisted + read back
    assert risk._limits_config()["var"]["0.99"] == 500_000_000.0


def test_update_limit_rejects_unknown_key(bff_root: Path) -> None:
    write_pricer_config(bff_root, {"sensitivities": {"CS01": 100_000_000.0}})
    assert risk.update_limit("sensitivity", "NOPE", 1.0)["ok"] is False   # unknown factor
    assert risk.update_limit("var", "0.99", 1.0)["ok"] is False           # no var section


def test_update_limit_validates_kind_and_ceiling(bff_root: Path) -> None:
    write_pricer_config(bff_root, {"var": {"0.99": 1_000_000_000.0}})
    assert risk.update_limit("bogus", "0.99", 1.0)["ok"] is False
    assert risk.update_limit("var", "0.99", -1.0)["ok"] is False
    assert risk.update_limit("var", "0.99", 0)["ok"] is False
    # a valid edit still lands
    assert risk.update_limit("var", "0.99", 2_000_000_000.0)["ok"] is True


def test_limits_unavailable_without_report(bff_root: Path) -> None:
    write_pricer_config(bff_root, {"var": {"0.99": 1000.0}})
    out = risk.limits(None)
    assert out["available"] is False


def test_limits_full_utilisation_and_bucket_summing(bff_root: Path) -> None:
    write_pricer_config(bff_root, {
        "var": {"0.99": 1000.0},
        "sensitivities": {"CS01": 100.0, "IR_PARALLEL": 100.0},
    })
    write_risk_report(bff_root, DATE, {
        "valuation_date": DATE,
        "run_id": "run-risk-1",
        "snapshot_id": "snap-1",
        "var": {"var": {"0.99": 300.0}},
        # CS01 not present directly → summed from per-bucket keys (|-30| + |-90| = 120 > 100)
        "book_sensitivities": {"IR_PARALLEL": -50.0, "CS01:IG": -30.0, "CS01:HY": -90.0},
    })

    out = risk.limits(None)
    assert out["available"] is True
    assert out["n_limits"] == 3
    assert out["n_breaches"] == 1

    by_name = {r["name"]: r for r in out["limits"]}
    # bucketed CS01 sums to 120 → breach
    assert by_name["CS01"]["observed"] == 120.0
    assert by_name["CS01"]["breached"] is True
    assert by_name["CS01"]["utilization"] == 1.2
    # VaR and IR within mandate
    assert by_name["VaR[0.99]"]["utilization"] == 0.3
    assert by_name["IR_PARALLEL"]["observed"] == 50.0
    assert by_name["IR_PARALLEL"]["breached"] is False

    # sorted by utilisation descending, worst first
    assert out["limits"][0]["name"] == "CS01"
    assert out["max_utilization"] == 1.2
