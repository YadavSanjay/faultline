"""S4 MacroRAG client/model tests (fake transport — no network)."""

from __future__ import annotations

from typing import Any

import pytest
from src.scenariogen.macro.client import MacroRAGClient, MacroTransportError, RequestsTransport
from src.scenariogen.macro.models import MacroFilter

_QUERY_RESP = {
    "summary": "USD/TWD likely +8% on export-control escalation.",
    "refused": False,
    "details": [{"text": "TWD weakens", "chunk_ids": ["fedreg::001"]}],
    "unanswered": [],
    "used_chunk_ids": ["fedreg::001"],
    "timeseries": [{"source": "bis_xru", "publisher": "BIS", "n_rows": 12, "note": "fx"}],
    "route": "DECOMPOSE",
    "cached": False,
    "chunks": [{"chunk_id": "fedreg::001", "source_id": "federal_register",
                "publisher": "BIS-Commerce", "release_date": "2026-06-10",
                "doc_type": "rule", "rerank_score": 0.81}],
}

_SYNTH_RESP = {
    "summary": "Taiwan tail shock vector.",
    "refused": False,
    "world_view_id": "macro-2026-06-18",
    "shock_vector": {
        "USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["fedreg::001"], "rationale": "rule"},
        "IG_OAS": {"value": 90.0, "unit": "bp", "chunk_ids": ["gfsr::004"]},
    },
    "chunks": [{"chunk_id": "fedreg::001", "release_date": "2026-06-10"},
               {"chunk_id": "gfsr::004", "release_date": "2026-05-20"}],
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


def test_query_parses_answer_and_chunks():
    client = MacroRAGClient(FakeTransport({"/query": _QUERY_RESP}))
    ans = client.query("export controls", MacroFilter(entity_tags=["china"]))
    assert ans.route == "DECOMPOSE" and not ans.refused
    assert ans.chunks[0].chunk_id == "fedreg::001"
    assert ans.chunks[0].release_date == "2026-06-10"


def test_query_sends_filters_payload():
    t = FakeTransport({"/query": _QUERY_RESP})
    MacroRAGClient(t).query("q", MacroFilter(factor_tags=["fx"], entity_tags=["taiwan"]))
    _, path, payload = t.calls[0]
    assert path == "/query"
    assert payload["filters"]["factor_tags"] == ["fx"]
    assert payload["filters"]["entity_tags"] == ["taiwan"]


def test_synthesize_parses_shock_vector_and_sets_mode():
    t = FakeTransport({"/query": _SYNTH_RESP})
    synth = MacroRAGClient(t).synthesize("taiwan tail", MacroFilter())
    assert synth.available and len(synth.fields) == 2
    assert t.calls[0][2]["mode"] == "synthesize"          # mode flag sent
    assert synth.world_view_id == "macro-2026-06-18"      # provenance pin parsed
    names = {f.name for f in synth.fields}
    assert names == {"USDTWD", "IG_OAS"}


def test_synthesize_unavailable_when_no_shock_vector():
    synth = MacroRAGClient(FakeTransport({"/query": _QUERY_RESP})).synthesize("q")
    assert not synth.available and synth.fields == []


def test_healthz_and_citation():
    client = MacroRAGClient(FakeTransport({
        "/healthz": {"status": "ok", "built": True},
        "/citations/fedreg::001": {"chunk_id": "fedreg::001", "release_date": "2026-06-10"},
    }))
    assert client.healthz()["status"] == "ok"
    assert client.citation("fedreg::001").chunk_id == "fedreg::001"


# --------------------------------------------------------------------------- #
# RequestsTransport resilience (injected fake `http` — no network, no sleeps)
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
    monkeypatch.setattr("src.scenariogen.macro.client.time.sleep", captured.append)
    return captured


def _transport(http: _FakeHttp, **kw: Any) -> RequestsTransport:
    return RequestsTransport("http://macro.test", http=http, backoff_s=1.0, **kw)


def test_transport_retries_then_succeeds(_sleeps):
    http = _FakeHttp([_FakeResp(503), _FakeResp(200, {"ok": 1})])
    assert _transport(http).get("/healthz") == {"ok": 1}
    assert http.calls == 2


def test_transport_retry_exhausted_raises(_sleeps):
    http = _FakeHttp([_FakeResp(503), _FakeResp(503), _FakeResp(503)])
    with pytest.raises(MacroTransportError):
        _transport(http, max_attempts=3).get("/healthz")
    assert http.calls == 3


def test_transport_429_honours_retry_after(_sleeps):
    http = _FakeHttp([_FakeResp(429, headers={"Retry-After": "5"}), _FakeResp(200, {"ok": 1})])
    _transport(http, backoff_max_s=16.0).get("/q")
    assert _sleeps[0] == 5.0           # slept for the server-advertised Retry-After


def test_transport_network_error_retried(_sleeps):
    http = _FakeHttp([_NetErr("boom"), _FakeResp(200, {"ok": 1})])
    assert _transport(http).post("/query", {"q": 1}) == {"ok": 1}
    assert http.calls == 2


def test_transport_4xx_not_retried(_sleeps):
    http = _FakeHttp([_FakeResp(404)])
    with pytest.raises(MacroTransportError):
        _transport(http).get("/q")
    assert http.calls == 1             # client errors fail fast


def test_transport_circuit_breaker_opens(_sleeps):
    http = _FakeHttp([_FakeResp(500), _FakeResp(500)])
    t = _transport(http, max_attempts=1, breaker_threshold=2)
    for _ in range(2):
        with pytest.raises(MacroTransportError):
            t.get("/q")
    # breaker now open -> next call fails fast without touching http
    with pytest.raises(MacroTransportError, match="circuit breaker"):
        t.get("/q")
    assert http.calls == 2
