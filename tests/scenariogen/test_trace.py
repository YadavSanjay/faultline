"""Dual-RAG P3 call-trace capture tests (no network)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from src.scenariogen.arag.client import RestTransport
from src.scenariogen.trace.capture import (
    configure_capture,
    current_collector,
    record_llm_call,
    record_rest_call,
    trace_run,
)
from src.scenariogen.trace.store import write_trace


@pytest.fixture(autouse=True)
def _reset_capture():
    configure_capture(enabled=True, max_chars=20_000)
    yield
    configure_capture(enabled=True, max_chars=20_000)


def test_recording_is_noop_without_collector():
    assert current_collector() is None
    record_rest_call(service="arag", purpose="/x", ok=True, latency_ms=1.0, request={"a": 1})
    record_llm_call(purpose="attribution", model="gpt-5-mini", ok=True, latency_ms=1.0)
    assert current_collector() is None          # still nothing bound; no error


def test_rest_and_llm_records_and_summary():
    with trace_run("run-1") as col:
        record_rest_call(service="macrorag", purpose="/query", ok=True, latency_ms=231000.0,
                         request={"mode": "synthesize"}, response={"shock_vector": {"UST_10Y": 1}})
        record_rest_call(service="arag", purpose="/api/v1/narrative/search", ok=True,
                         latency_ms=120000.0, request={"company": "NVDA"}, response={"chunks": []})
        record_llm_call(purpose="attribution", model="gpt-5-mini", ok=True, latency_ms=1500.0,
                        messages=[{"role": "system", "content": "sys"},
                                  {"role": "user", "content": "usr"}],
                        content="narrative text", prompt_tokens=100, completion_tokens=50,
                        cost_usd=0.0055)
    assert [r.service for r in col.records] == ["macrorag", "arag", "llm"]
    assert [r.seq for r in col.records] == [1, 2, 3]
    llm = col.records[2]
    assert llm.system == "sys" and llm.user == "usr" and llm.response == "narrative text"
    assert llm.prompt_hash and llm.prompt_tokens == 100
    assert llm.to_dict()["total_tokens"] == 150

    s = col.summary()
    assert s["n_calls"] == 3
    assert s["by_service"]["macrorag"]["n_calls"] == 1
    assert s["by_service"]["llm"]["cost_usd"] == 0.0055
    assert s["total_cost_usd"] == 0.0055
    assert s["by_service"]["arag"]["total_tokens"] == 0


def test_capture_toggle_off_keeps_metadata_drops_bodies():
    configure_capture(enabled=False)
    with trace_run("run-2") as col:
        record_rest_call(service="arag", purpose="/x", ok=True, latency_ms=5.0,
                         request={"secret": "big"}, response={"data": "big"})
    rec = col.records[0]
    assert rec.request == "" and rec.response == ""     # bodies gated off
    assert rec.service == "arag" and rec.latency_ms == 5.0   # metadata kept


def test_truncation_marks_oversized_body():
    configure_capture(max_chars=20)
    with trace_run("run-3") as col:
        record_rest_call(service="arag", purpose="/x", ok=True, latency_ms=1.0,
                         response={"text": "x" * 500})
    assert "truncated" in col.records[0].response


def test_scenario_tagging():
    with trace_run("run-tag") as col:
        col.set_scenario("ai_bust")
        record_rest_call(service="arag", purpose="/x", ok=True, latency_ms=1.0)
        col.set_scenario("taiwan_geopolitical")
        record_llm_call(purpose="attribution", model="gpt-5-mini", ok=True, latency_ms=1.0,
                        messages=[{"role": "user", "content": "u"}])
    assert col.records[0].scenario == "ai_bust"
    assert col.records[1].scenario == "taiwan_geopolitical"


def test_error_call_recorded():
    with trace_run("run-4") as col:
        record_rest_call(service="macrorag", purpose="/query", ok=False, latency_ms=600000.0,
                         request={"mode": "synthesize"}, error="Read timed out")
    rec = col.records[0]
    assert not rec.ok and rec.error == "Read timed out"
    assert col.summary()["by_service"]["macrorag"]["n_errors"] == 1


def test_transport_hook_records_inside_run():
    """The ARag RestTransport records into the active collector end-to-end (offline http fake)."""
    class _Resp:
        status_code = 200
        headers: dict = {}

        def raise_for_status(self) -> None: ...
        def json(self) -> dict: return {"status": "ok", "pipelines": {"sec10k": True}}

    class _Exc(Exception):
        pass

    class _Http:
        exceptions = type("E", (), {"RequestException": _Exc})

        def get(self, url: str, headers: dict, timeout: float, **kw: Any) -> Any:
            return _Resp()

    t = RestTransport("http://arag.test", http=_Http())
    with trace_run("run-5") as col:
        t.get("/api/health")
    assert len(col.records) == 1
    rec = col.records[0]
    assert rec.service == "arag" and rec.purpose == "/api/health" and rec.ok
    assert json.loads(rec.response)["status"] == "ok"


def test_write_trace_jsonl(tmp_path):
    with trace_run("run-6") as col:
        record_rest_call(service="arag", purpose="/x", ok=True, latency_ms=1.0, response={"a": 1})
        record_llm_call(purpose="attribution", model="gpt-5-mini", ok=True, latency_ms=2.0,
                        messages=[{"role": "user", "content": "u"}], content="c",
                        prompt_tokens=1, completion_tokens=2, cost_usd=0.001)
    out = write_trace(col, "2026-07-10", tmp_path)
    assert out.exists() and out.name == "run-6.jsonl"
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["service"] == "arag" and first["seq"] == 1
