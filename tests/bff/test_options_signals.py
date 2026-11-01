"""BFF option-implied signal reads (P6/B6): `/api/options/signals[/credit|/{name}|/{name}/rnd]`."""

from __future__ import annotations

from pathlib import Path

from bff_helpers import write_feed_csv
from ui.bff.app import options_signals


def _seed(root: Path) -> None:
    sig_header = ["snapshot_date", "underlying", "atm_iv_30d", "atm_iv_90d", "term_ratio",
                  "term_inverted", "skew_25d_30d", "vrp_20d", "expected_move_front",
                  "prob_down_10", "rnd_spot", "rnd_forward", "rnd_dte"]
    write_feed_csv(root, "options/signals/option_signals.csv", sig_header, [
        # NVDA: two snapshots (history); latest is 2026-07-10
        ["2026-06-17", "NVDA", 0.55, 0.50, 1.10, "True", 0.06, -0.02, 0.05, 0.07, 170.0, 171.0, 30],
        ["2026-07-10", "NVDA", 0.52, 0.49, 1.06, "True", 0.05, -0.03, 0.04, 0.06, 180.0, 181.0, 30],
        ["2026-07-10", "AAPL", 0.21, 0.24, 0.88, "False", 0.02, -0.04, 0.02, 0.01, 300.0, 301.0, 30],
    ])
    write_feed_csv(root, "options/signals/rnd_curves.csv",
                   ["underlying", "snapshot_date", "dte", "strike", "pdf", "cdf"], [
                       ["NVDA", "2026-07-10", 30, 150.0, 0.001, 0.05],
                       ["NVDA", "2026-07-10", 30, 180.0, 0.010, 0.50],
                       ["NVDA", "2026-07-10", 30, 210.0, 0.001, 0.95],
                   ])
    write_feed_csv(root, "options/signals/credit_implied.csv",
                   ["snapshot_date", "underlying", "merton_dd", "merton_pd", "cg_spread_bp",
                    "merton_spread_bp", "cds_reference_bp", "basis_bp", "leverage", "equity_vol"], [
                       ["2026-07-10", "ORCL", 2.5, 0.006, 40.0, 35.0, 90.0, 5.0, 0.55, 0.30],
                       ["2026-07-10", "NVDA", 12.0, 0.0, 1.0, 0.5, "", "", 0.05, 0.52],
                   ])


def test_list_signals_summary_and_availability(bff_root: Path) -> None:
    _seed(bff_root)
    out = options_signals.list_signals()
    assert out["count"] == 2
    by = {n["underlying"]: n for n in out["names"]}
    # latest snapshot wins for NVDA
    assert by["NVDA"]["snapshot_date"] == "2026-07-10"
    assert by["NVDA"]["atm_iv_30d"] == 0.52
    assert by["NVDA"]["has_rnd"] is True and by["NVDA"]["has_credit"] is True
    assert by["NVDA"]["merton_dd"] == 12.0
    assert by["AAPL"]["has_rnd"] is False          # no RND curve seeded for AAPL
    assert out["snapshots"] == ["2026-06-17", "2026-07-10"]


def test_signal_detail_has_history_and_credit(bff_root: Path) -> None:
    _seed(bff_root)
    d = options_signals.signal_detail("NVDA")
    assert d is not None
    assert d["latest"]["atm_iv_30d"] == 0.52
    assert len(d["history"]) == 2                  # both snapshots
    assert d["history"][0]["snapshot_date"] == "2026-06-17"
    assert d["credit_implied"]["merton_dd"] == 12.0
    assert options_signals.signal_detail("ZZZ") is None


def test_rnd_curve_returns_grid_and_meta(bff_root: Path) -> None:
    _seed(bff_root)
    r = options_signals.rnd_curve("NVDA")
    assert r is not None
    assert r["strike"] == [150.0, 180.0, 210.0]
    assert r["cdf"] == [0.05, 0.5, 0.95]
    assert r["rnd_spot"] == 180.0                  # meta joined from the signals row
    assert r["prob_down_10"] == 0.06
    assert options_signals.rnd_curve("AAPL") is None   # no RND grid for AAPL


def test_credit_table_ranked_by_distance_to_default(bff_root: Path) -> None:
    _seed(bff_root)
    c = options_signals.credit_table()
    assert c["count"] == 2
    # riskiest (lowest DD) first: ORCL 2.5 before NVDA 12.0
    assert [r["underlying"] for r in c["rows"]] == ["ORCL", "NVDA"]
    # NVDA's missing CDS cross-check is null, not NaN
    nvda = c["rows"][1]
    assert nvda["cds_reference_bp"] is None


def test_empty_when_no_signals_on_disk(bff_root: Path) -> None:
    out = options_signals.list_signals()
    assert out["count"] == 0 and out["names"] == []
    assert options_signals.credit_table()["count"] == 0
