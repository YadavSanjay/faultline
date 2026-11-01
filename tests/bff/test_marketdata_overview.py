"""BFF market-data overview + feed→trade dependency (`/md/overview`, `/md/dependency`)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from bff_helpers import write_dq_gate, write_feed_csv, write_portfolio
from ui.bff.app import marketdata

TODAY = dt.date.today().isoformat()


def _seed(root: Path) -> None:
    # one consumed feed (equity spot → PV+risk for a cash-equity trade) and one unused feed
    write_portfolio(root, [
        {"trade_id": "TRADE_1", "asset_class": "equity", "trade_type": "stock",
         "description": "NVDA stock", "params": {}},
    ])
    write_feed_csv(root, "equity/prices_daily.csv", ["date", "close"], [[TODAY, 100.0]])
    write_feed_csv(root, "equity/splits.csv", ["date", "ratio"], [[TODAY, 1.0]])
    write_dq_gate(root, {"equity/prices_daily.csv": {"required_columns": ["date", "close"]}})


def test_overview_rollup_counts(bff_root: Path) -> None:
    _seed(bff_root)
    o = marketdata.overview()

    assert o["n_feeds"] == 2
    assert o["n_consumed"] == 1  # prices_daily (pv+risk)
    assert o["n_unused"] == 1  # splits (reference)
    assert o["consumption_counts"]["both"] == 1
    assert o["consumption_counts"]["none"] == 1
    # prices_daily is fresh + gated (ok); splits is undeclared (ungated)
    assert o["dq_counts"]["ok"] == 1
    assert o["dq_counts"]["ungated"] == 1
    assert o["n_stale"] == 0

    equity = next(r for r in o["by_asset_class"] if r["asset_class"] == "equity")
    assert equity["n_feeds"] == 2
    assert equity["n_consumed"] == 1


def test_dependency_links_feed_to_trades(bff_root: Path) -> None:
    _seed(bff_root)
    d = marketdata.dependency()

    # only the consumed feed appears (splits excluded)
    assert d["n_feeds_consumed"] == 1
    feed = d["feeds"][0]
    assert feed["id"] == "equity/prices_daily.csv"
    assert feed["consumed_by"] == "both"
    assert feed["is_risk_factor"] is False
    assert feed["n_trades"] == 1
    assert feed["trades"][0]["trade_id"] == "TRADE_1"

    # reverse index: the trade lists the feed it depends on
    assert d["n_trades"] == 1
    trade = d["trades"][0]
    assert trade["trade_id"] == "TRADE_1"
    assert "equity/prices_daily.csv" in trade["feeds"]
