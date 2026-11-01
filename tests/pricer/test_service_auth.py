"""PRICER_API_KEY auth on the FastAPI facade (Phase D).

The middleware reads the key per-request, so these drive the same warm ``service.app`` and just
toggle the env var. No key configured ⇒ open (dev parity); key configured ⇒ X-API-Key enforced on
everything except /health and /metrics.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from src.pricer import service

client = TestClient(service.app)


def test_open_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRICER_API_KEY", raising=False)
    assert client.get("/runs").status_code == 200


def test_key_enforced_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICER_API_KEY", "s3cret")
    assert client.get("/runs").status_code == 401                                  # missing
    assert client.get("/runs", headers={"X-API-Key": "nope"}).status_code == 401   # wrong
    assert client.get("/runs", headers={"X-API-Key": "s3cret"}).status_code == 200  # correct


def test_health_and_metrics_stay_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICER_API_KEY", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200
