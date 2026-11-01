"""BFF book-history aggregation (`/pricing/book/history`)."""

from __future__ import annotations

from pathlib import Path

from bff_helpers import pv_record, write_results
from ui.bff.app import pricing

D1 = "2026-01-01"
D2 = "2026-02-01"


def test_empty_store_returns_empty_shape(bff_root: Path) -> None:
    h = pricing.book_history()
    assert h == {"dates": [], "asset_classes": [], "total": [], "series": {}}


def test_book_history_totals_pnl_and_series(bff_root: Path) -> None:
    # d1: equity 100 + rates 40 = 140
    write_results(bff_root, [
        pv_record(trade_id="T1", date=D1, asset_class="equity", pv=100.0,
                  priced_at=f"{D1}T09:00:00", run_id="rA", trade_type="stock"),
        pv_record(trade_id="T2", date=D1, asset_class="rates", pv=40.0,
                  priced_at=f"{D1}T09:00:00", run_id="rA", trade_type="ois_swap"),
    ], run_id="rA")
    # d2: an earlier (stale) run for T1 that must be superseded by the later one
    write_results(bff_root, [
        pv_record(trade_id="T1", date=D2, asset_class="equity", pv=999.0,
                  priced_at=f"{D2}T08:00:00", run_id="rB0", trade_type="stock"),
    ], run_id="rB0")
    # d2 latest: equity 150 + rates 60 = 210
    write_results(bff_root, [
        pv_record(trade_id="T1", date=D2, asset_class="equity", pv=150.0,
                  priced_at=f"{D2}T09:00:00", run_id="rB", trade_type="stock"),
        pv_record(trade_id="T2", date=D2, asset_class="rates", pv=60.0,
                  priced_at=f"{D2}T09:00:00", run_id="rB", trade_type="ois_swap"),
    ], run_id="rB")

    h = pricing.book_history()

    assert h["dates"] == [D1, D2]
    assert h["asset_classes"] == ["equity", "rates"]

    total = h["total"]
    assert [t["valuation_date"] for t in total] == [D1, D2]
    assert total[0]["total_pv"] == 140.0
    assert total[0]["pnl"] is None  # first date has no prior
    assert total[0]["n_trades"] == 2
    # latest run wins (150, not 999) → d2 total 210, pnl +70
    assert total[1]["total_pv"] == 210.0
    assert total[1]["pnl"] == 70.0

    # per-asset-class series aligned to dates
    assert h["series"]["equity"] == [100.0, 150.0]
    assert h["series"]["rates"] == [40.0, 60.0]


def test_series_null_where_asset_class_absent_on_a_date(bff_root: Path) -> None:
    write_results(bff_root, [
        pv_record(trade_id="T1", date=D1, asset_class="equity", pv=100.0,
                  priced_at=f"{D1}T09:00:00", run_id="rA"),
        pv_record(trade_id="T2", date=D1, asset_class="rates", pv=40.0,
                  priced_at=f"{D1}T09:00:00", run_id="rA", trade_type="ois_swap"),
    ], run_id="rA")
    # d2 has only equity — rates absent
    write_results(bff_root, [
        pv_record(trade_id="T1", date=D2, asset_class="equity", pv=120.0,
                  priced_at=f"{D2}T09:00:00", run_id="rB"),
    ], run_id="rB")

    h = pricing.book_history()
    assert h["dates"] == [D1, D2]
    assert h["series"]["equity"] == [100.0, 120.0]
    assert h["series"]["rates"] == [40.0, None]  # null on the date it wasn't priced
