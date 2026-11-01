"""Dual-RAG ARag client/model tests (fake transport — no network)."""

from __future__ import annotations

from typing import Any

import pytest
from src.scenariogen.arag.client import AragClient, AragTransportError, RestTransport

_HEALTH_RESP = {"status": "ok", "audit_db": True, "pipelines": True}

_COVERAGE_RESP = {
    "items": [
        {"cik": "0001045810", "ticker": "NVDA", "company_name": "NVIDIA Corp",
         "fiscal_years": [2023, 2024, 2025], "form_types": ["10-K", "10-Q"]},
        {"cik": "0000002488", "ticker": "AMD", "company_name": "Advanced Micro Devices",
         "fiscal_years": [2024, 2025], "form_types": ["10-K"]},
    ]
}

_NARRATIVE_RESP = {
    "company": {"ticker": "nvda", "resolution": "exact_ticker", "similarity": 1.0},
    "query": "Taiwan/TSMC supply concentration",
    "chunks": [
        {"chunk_id": "nvda::2025::rf::07", "text": "We depend on TSMC in Taiwan ...",
         "section_path": "Item 1A Risk Factors", "ticker": "NVDA", "fiscal_year": 2025,
         "form_type": "10-K", "accession_no": "0001045810-25-000010", "rerank_score": 0.88},
        {"chunk_id": "nvda::2024::rf::05", "text": "Geopolitical tensions ...",
         "section_path": "Item 1A", "ticker": "NVDA", "fiscal_year": 2024,
         "form_type": "10-K", "accession_no": "0001045810-24-000008", "rerank_score": 0.71},
    ],
}

_FACTS_RESP = {
    "company": {"ticker": "NVDA", "resolution": "exact_ticker"},
    "concept": "Revenue",
    "unresolved": False,
    "series": [
        {"fiscal_year": 2024, "status": "OK", "value": 60922.0, "unit": "USD", "confidence": 0.99},
        {"fiscal_year": 2025, "status": "OK", "value": 130497.0, "unit": "USD", "confidence": 0.99},
    ],
}


class FakeTransport:
    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple] = []

    def get(self, path: str) -> dict[str, Any]:
        self.calls.append(("GET", path))
        return self.responses[path]

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("POST", path, payload))
        return self.responses[path]


def test_health_ok_requires_pipelines():
    client = AragClient(FakeTransport({"/api/health": _HEALTH_RESP}))
    h = client.health()
    assert h.ok and h.status == "ok" and h.pipelines

    down = AragClient(FakeTransport({"/api/health": {"status": "ok", "pipelines": False}}))
    assert not down.health().ok            # retrieval not importable -> not ready


def test_health_pipelines_dict_form():
    # ARag reports pipelines as a per-pipeline map; ready if any is up.
    up = AragClient(FakeTransport({"/api/health": {
        "status": "ok", "audit_db": True, "pipelines": {"xbrl": True, "sec10k": True}}}))
    h = up.health()
    assert h.ok and h.pipelines and h.pipelines_detail == {"xbrl": True, "sec10k": True}

    all_down = AragClient(FakeTransport({"/api/health": {
        "status": "ok", "pipelines": {"xbrl": False, "sec10k": False}}}))
    assert not all_down.health().ok        # every pipeline down -> not ready


def test_coverage_parses_and_indexes():
    cov = AragClient(FakeTransport({"/api/v1/coverage": _COVERAGE_RESP})).coverage()
    assert cov.tickers() == {"NVDA", "AMD"}
    assert cov.covers("nvda") and not cov.covers("TSM")


def test_narrative_search_sends_body_and_parses_chunks():
    t = FakeTransport({"/api/v1/narrative/search": _NARRATIVE_RESP})
    res = AragClient(t).narrative_search("NVDA", "Taiwan/TSMC", fiscal_years=[2024, 2025], top_k=5)
    assert res.resolved and res.ticker == "NVDA"
    assert [c.chunk_id for c in res.chunks] == ["nvda::2025::rf::07", "nvda::2024::rf::05"]
    _, path, payload = t.calls[0]
    assert path == "/api/v1/narrative/search"
    assert payload["company"] == "NVDA" and payload["top_k"] == 5
    assert payload["section_hint"] == "Risk Factors"      # default section hint sent
    assert payload["fiscal_years"] == [2024, 2025]


def test_facts_lookup_parses_series():
    fs = AragClient(FakeTransport({"/api/v1/facts/lookup": _FACTS_RESP})).facts_lookup(
        "NVDA", "Revenue", [2024, 2025])
    assert fs.ticker == "NVDA" and not fs.unresolved
    assert [p.value for p in fs.series] == [60922.0, 130497.0]


# --------------------------------------------------------------------------- #
# RestTransport resilience (injected fake `http` — no network, no sleeps)
# --------------------------------------------------------------------------- #
class _NetErr(Exception):
    pass


class _FakeExceptions:
    RequestException = _NetErr


class _FakeResp:
    def __init__(self, status: int, json_data: Any = None, headers: dict | None = None):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _NetErr(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._json


class _FakeHttp:
    exceptions = _FakeExceptions

    def __init__(self, queue: list[Any]):
        self.queue = list(queue)
        self.calls = 0

    def get(self, url: str, headers: dict, timeout: float, **kw: Any) -> Any:
        return self._next()

    def post(self, url: str, headers: dict, timeout: float, **kw: Any) -> Any:
        return self._next()

    def _next(self) -> Any:
        self.calls += 1
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def _sleeps(monkeypatch):
    captured: list[float] = []
    monkeypatch.setattr("src.scenariogen.arag.client.time.sleep", captured.append)
    return captured


def _transport(http: _FakeHttp, **kw: Any) -> RestTransport:
    return RestTransport("http://arag.test", http=http, backoff_s=1.0, **kw)


def test_transport_retries_then_succeeds(_sleeps):
    http = _FakeHttp([_FakeResp(503), _FakeResp(200, {"ok": 1})])
    assert _transport(http, max_attempts=2).get("/api/health") == {"ok": 1}
    assert http.calls == 2


def test_transport_retry_exhausted_raises(_sleeps):
    http = _FakeHttp([_FakeResp(503), _FakeResp(503)])
    with pytest.raises(AragTransportError):
        _transport(http, max_attempts=2).get("/api/health")
    assert http.calls == 2


def test_transport_4xx_not_retried(_sleeps):
    http = _FakeHttp([_FakeResp(404)])
    with pytest.raises(AragTransportError):
        _transport(http).get("/api/v1/coverage")
    assert http.calls == 1             # client errors fail fast


def test_transport_circuit_breaker_opens(_sleeps):
    http = _FakeHttp([_FakeResp(500), _FakeResp(500)])
    t = _transport(http, max_attempts=1, breaker_threshold=2)
    for _ in range(2):
        with pytest.raises(AragTransportError):
            t.get("/api/health")
    with pytest.raises(AragTransportError, match="circuit breaker"):
        t.get("/api/health")
    assert http.calls == 2


def test_no_api_key_sends_empty_headers():
    t = RestTransport("http://arag.test", http=_FakeHttp([]))
    assert t.headers == {}
    t2 = RestTransport("http://arag.test", api_key="k", http=_FakeHttp([]))
    assert t2.headers == {"X-API-Key": "k"}
