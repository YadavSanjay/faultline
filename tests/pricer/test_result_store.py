"""P1c results-store tests: Parquet roundtrip + DuckDB queries (latest-run dedup)."""

from __future__ import annotations

from src.pricer.output.result_store import ResultStore


def _rec(trade_id, vdate, pv, run_id, priced_at, *, asset_class="rates",
         status="ok", n_warn=0):
    return {
        "valuation_type": "PV", "valuation_date": vdate, "asset_class": asset_class,
        "trade_id": trade_id, "trade_type": "ois_swap", "currency": "USD",
        "pv": pv, "status": status, "n_warnings": n_warn,
        "run_id": run_id, "snapshot_id": "snap1", "git_sha": "abc1234",
        "quantlib_version": "1.42.1", "priced_at": priced_at, "measures_json": "{}",
    }


def test_empty_store_returns_empty(tmp_path):
    store = ResultStore(tmp_path)
    assert store.pv_history("TRADE_A").empty
    assert store.book_pv_by_asset_class("2026-05-29").empty


def test_write_and_query_latest_run_dedup(tmp_path):
    store = ResultStore(tmp_path)
    # run r1 then r2 (later) on the same date: A re-priced 100 -> 110
    store.write_run([_rec("TRADE_A", "2026-05-29", 100.0, "r1", "2026-06-20T01:00:00+00:00"),
                     _rec("TRADE_B", "2026-05-29", 200.0, "r1", "2026-06-20T01:00:00+00:00")], "r1")
    store.write_run([_rec("TRADE_A", "2026-05-29", 110.0, "r2", "2026-06-20T02:00:00+00:00")], "r2")
    # later date
    store.write_run([_rec("TRADE_A", "2026-05-30", 120.0, "r3", "2026-06-21T01:00:00+00:00")], "r3")

    hist = store.pv_history("TRADE_A")
    assert list(hist["valuation_date"]) == ["2026-05-29", "2026-05-30"]
    assert list(hist["pv"]) == [110.0, 120.0]          # latest run wins on 05-29

    book = store.book_pv_by_asset_class("2026-05-29")
    row = book[book["asset_class"] == "rates"].iloc[0]
    assert int(row["n_trades"]) == 2 and row["total_pv"] == 310.0   # 110 + 200

    latest = store.latest_run("2026-05-29")
    assert set(latest["trade_id"]) == {"TRADE_A", "TRADE_B"}
    assert latest[latest.trade_id == "TRADE_A"].iloc[0]["pv"] == 110.0


def test_trades_with_warnings(tmp_path):
    store = ResultStore(tmp_path)
    store.write_run([
        _rec("OK1", "2026-05-29", 1.0, "r1", "2026-06-20T01:00:00+00:00"),
        _rec("WARN1", "2026-05-29", 2.0, "r1", "2026-06-20T01:00:00+00:00", n_warn=3),
        _rec("FAIL1", "2026-05-29", None, "r1", "2026-06-20T01:00:00+00:00", status="FAILED: boom"),
    ], "r1")
    flagged = store.trades_with_warnings("2026-05-29")
    assert set(flagged["trade_id"]) == {"WARN1", "FAIL1"}
