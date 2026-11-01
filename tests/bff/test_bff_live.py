"""BFF live re-price proxy to the pricer service (`GET /pricing/live/{id}`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ui.bff.app import pricing


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def test_live_reprice_graceful_when_service_down(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # point at a dead port so httpx refuses immediately
    monkeypatch.setenv("QLP_SERVICE_URL", "http://127.0.0.1:59999")
    out = pricing.live_price("TRADE_1", None)
    assert out["available"] is False
    assert out["service"] == "down"
    assert out.get("error")


def test_live_reprice_success_via_mocked_service(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "pv": 123.0, "measures": {"dv01": 10.0}, "leg_pvs": {}, "diagnostics": {},
        "warnings": [], "provenance": {"market_data_snapshot_id": "snap-live"},
    }
    monkeypatch.setattr(pricing.httpx, "post", lambda *_a, **_k: _FakeResp(200, payload))
    out = pricing.live_price("TRADE_1", None)
    assert out["available"] is True
    assert out["service"] == "up"
    assert out["result"]["pv"] == 123.0
    assert out["snapshot_id"] == "snap-live"
    assert out["priced_at"]


def test_live_reprice_trade_not_found(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pricing.httpx, "post", lambda *_a, **_k: _FakeResp(404, text="not found"))
    out = pricing.live_price("NOPE", None)
    assert out["available"] is False
    assert out["service"] == "up"
    assert "not found" in out["error"]


def test_live_reprice_upstream_error(bff_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pricing.httpx, "post", lambda *_a, **_k: _FakeResp(422, text="bad trade"))
    out = pricing.live_price("TRADE_1", None)
    assert out["available"] is False
    assert out["service"] == "up"
    assert "422" in out["error"]
