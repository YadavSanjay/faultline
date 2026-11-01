"""P1b HTTP service tests (skipped if FastAPI/httpx aren't installed).

Drives the FastAPI facade with the in-process TestClient — the same engine and
snapshot the batch runner uses, so prices match.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from src.pricer import service  # builds the warm engine at import

client = TestClient(service.app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["snapshot_id"] and body["quantlib_version"]


@pytest.mark.needs_market_data
def test_price_single_trade():
    r = client.post("/price", json={"trade_id": "TRADE_22", "valuation_date": "2026-05-29"})
    assert r.status_code == 200
    body = r.json()
    assert body["trade_id"] == "TRADE_22" and body["pv"] != 0
    snap = client.get("/health").json()["snapshot_id"]
    assert body["provenance"]["market_data_snapshot_id"] == snap
    assert body["provenance"]["run_id"]


def test_price_unknown_trade_404():
    r = client.post("/price", json={"trade_id": "NOPE"})
    assert r.status_code == 404


@pytest.mark.needs_market_data
def test_price_portfolio():
    r = client.post("/price-portfolio", json={"asset_class": "rates", "valuation_date": "2026-05-29"})
    assert r.status_code == 200
    body = r.json()
    assert body["snapshot_id"]
    assert len(body["outcomes"]) == 4
    assert {o["status"] for o in body["outcomes"]} == {"ok"}
    assert all(o["pv"] is not None for o in body["outcomes"])


def test_results_endpoints_after_a_run(tmp_path):
    # Populate a tmp columnar store via the batch runner, then read it back over HTTP from an
    # app rooted at the same tmp dir, so the test never writes into the real data/pricer store.
    from src.pricer.runner import run_to
    run_to(tmp_path, asset_class="rates", valuation_date=__import__("datetime").date(2026, 5, 29))
    client = TestClient(service.create_app_at(tmp_path))

    book = client.get("/results/book/2026-05-29").json()["by_asset_class"]
    assert any(r["asset_class"] == "rates" and r["n_trades"] >= 4 for r in book)

    hist = client.get("/results/pv-history/TRADE_22").json()["history"]
    assert any(h["valuation_date"] == "2026-05-29" for h in hist)

    trades = client.get("/results/run/2026-05-29").json()["trades"]
    assert "TRADE_22" in {t["trade_id"] for t in trades}


@pytest.mark.needs_market_data
def test_risk_sensitivities_endpoint():
    r = client.post("/risk/sensitivities",
                    json={"trade_id": "TRADE_22", "valuation_date": "2026-05-29"})
    assert r.status_code == 200
    factors = {s["factor"] for s in r.json()["sensitivities"]}
    assert "IR_PARALLEL" in factors


@pytest.mark.needs_market_data
def test_risk_scenario_endpoint():
    r = client.post("/risk/scenario",
                    json={"scenario": "rates_selloff_100bp", "asset_class": "rates",
                          "valuation_date": "2026-05-29"})
    assert r.status_code == 200
    body = r.json()
    assert body["scenario"] == "rates_selloff_100bp"
    assert "pnl_by_asset_class" in body and body["trades"]


@pytest.mark.needs_market_data
def test_risk_var_endpoint():
    r = client.post("/risk/var", json={"valuation_date": "2026-05-29"})
    assert r.status_code == 200
    body = r.json()
    assert body["n_scenarios"] > 0 and body["var"]["0.99"] > 0


def test_risk_scenario_unknown_404():
    r = client.post("/risk/scenario", json={"scenario": "does_not_exist"})
    assert r.status_code == 404


def test_metrics_endpoint():
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "pricer_trades_total" in r.text


def test_runs_endpoint():
    r = client.get("/runs", params={"limit": 5})
    assert r.status_code == 200
    assert "runs" in r.json()


def test_model_inventory_endpoint():
    r = client.get("/governance/model-inventory")
    assert r.status_code == 200
    body = r.json()
    assert body["n_models"] >= 11 and "cds" in body["models"]


def test_governance_verify_endpoint():
    r = client.post("/governance/verify",
                    json={"asset_class": "rates", "valuation_date": "2026-05-29"})
    assert r.status_code == 200
    body = r.json()
    assert "golden" in body and "ipv" in body and "model_inventory" in body
    assert body["ipv"]["all_passed"] is True
