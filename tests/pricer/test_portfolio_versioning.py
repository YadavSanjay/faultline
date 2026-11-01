"""Portfolio versioning tests: content-addressed version, governed store, replay, lineage."""

from __future__ import annotations

from datetime import date

import pytest
from src.pricer.config import get_project_root, load_pricer_config
from src.pricer.engine import PricingEngine
from src.pricer.input.common.valuation import ValuationContext
from src.pricer.portfolio.portfolio_loader import build_portfolio, load_portfolio_config
from src.pricer.portfolio.versioning import PortfolioStore, compute_version


def _raw(*trades):
    return {"name": "test", "trades": list(trades)}


T1 = {"trade_id": "T1", "asset_class": "rates", "trade_type": "ois_swap",
      "currency": "USD", "notional": 1, "params": {}}
T2 = {"trade_id": "T2", "asset_class": "equity", "trade_type": "stock",
      "currency": "USD", "notional": 1, "params": {"ticker": "NVDA", "quantity": 1}}


def test_version_is_deterministic_and_content_addressed():
    a = compute_version(_raw(T1, T2))
    assert a == compute_version(_raw(T1, T2))            # deterministic
    assert len(a) == 16
    # editing a position economics changes the version
    T1b = {**T1, "notional": 2}
    assert compute_version(_raw(T1b, T2)) != a
    # documentation-only change does NOT change the version
    assert compute_version({"description": "new docs", "trades": [T1, T2]}) == a


def test_store_register_load_idempotent(tmp_path):
    store = PortfolioStore(tmp_path)
    raw = _raw(T1, T2)
    v1 = store.register(raw)
    v2 = store.register(raw)                              # idempotent — same version
    assert v1 == v2
    loaded = store.load_version(v1)
    assert loaded["portfolio_version"] == v1
    assert len(loaded["trades"]) == 2
    versions = store.versions()
    assert len(versions) == 1 and versions.iloc[0]["portfolio_version"] == v1
    assert int(versions.iloc[0]["n_trades"]) == 2


def test_two_versions_tracked(tmp_path):
    store = PortfolioStore(tmp_path)
    v1 = store.register(_raw(T1))
    v2 = store.register(_raw(T1, T2))
    assert v1 != v2
    assert set(store.versions()["portfolio_version"]) == {v1, v2}


def test_stored_version_replays_to_same_trades(tmp_path):
    """A version loaded from the store builds the same Trade objects as the live load."""
    store = PortfolioStore(tmp_path)
    raw = load_portfolio_config().raw
    version = store.register(raw)
    replayed = build_portfolio(store.load_version(version))
    live = build_portfolio(raw)
    assert [t.trade_id for t in replayed] == [t.trade_id for t in live]


@pytest.mark.needs_market_data
def test_engine_provenance_carries_portfolio_version():
    """End-to-end: a result's provenance records the portfolio_version."""
    from dataclasses import replace

    from src.pricer.provenance import RunProvenance
    root = get_project_root()
    raw = load_portfolio_config().raw
    version = PortfolioStore(root).register(raw)
    eng = PricingEngine(load_pricer_config(), root)
    ctx = ValuationContext(valuation_date=date(2026, 5, 29))
    run_prov = replace(RunProvenance.capture(root, load_pricer_config()),
                       portfolio_version=version)
    res = eng.price(build_portfolio(raw, trade_id="TRADE_22")[0], ctx, run_prov)
    assert res.provenance["portfolio_version"] == version
