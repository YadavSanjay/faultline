"""cost-per-layer wiring: reported costs on traced calls, and the ablation runner's layer-cost lines.

Synthetic calls and attributions; no ``data/``, services or pricing.
"""

from __future__ import annotations

import pytest
from src.scenariogen.ablation_runner import layer_cost_lines
from src.scenariogen.layer_cost import OVERHEAD, layer_usage, layer_value
from src.scenariogen.trace.capture import CallRecord, record_llm_call, record_rest_call, trace_run


def test_rest_call_takes_the_cost_a_response_reports():
    with trace_run("r") as col:
        col.set_scenario("yen")
        record_rest_call(service="macrorag", purpose="/query", ok=True, latency_ms=1500.0,
                         response={"summary": "x", "usage": {"cost_usd": 0.25}})
        record_rest_call(service="arag", purpose="/api/v1/narrative/search", ok=True, latency_ms=500.0,
                         response={"chunks": []})
    reported, silent = col.records
    assert (reported.cost_usd, reported.cost_reported, reported.scenario) == (0.25, True, "yen")
    assert (silent.cost_usd, silent.cost_reported) == (0.0, False)
    assert reported.to_dict()["cost_reported"] is True and silent.to_dict()["cost_reported"] is False


def test_a_broken_reported_cost_is_logged_and_the_call_still_recorded(caplog):
    with trace_run("r") as col, caplog.at_level("WARNING", logger="scenariogen.trace"):
        record_rest_call(service="macrorag", purpose="/query", ok=True, latency_ms=10.0,
                         response={"usage": {"cost_usd": -1.0}})
    (rec,) = col.records
    assert (rec.cost_usd, rec.cost_reported) == (0.0, False)
    assert "ignoring reported cost" in caplog.text


def test_llm_calls_always_report_their_cost_and_no_collector_is_a_no_op():
    with trace_run("r") as col:
        record_llm_call(purpose="narrative", model="gpt-5", ok=True, latency_ms=900.0, cost_usd=0.01)
    assert col.records[0].cost_reported is True
    record_rest_call(service="macrorag", purpose="/query", ok=True, latency_ms=1.0)   # outside a run: no error
    assert CallRecord(1, "arag", "/x", True, 1.0).cost_reported is False


def test_traced_calls_join_the_ablation_attributions():
    with trace_run("r") as col:
        col.set_scenario("tariff")
        record_rest_call(service="macrorag", purpose="/query", ok=True, latency_ms=2000.0,
                         response={"usage": {"cost_usd": 0.5}})
        record_rest_call(service="arag", purpose="/api/v1/facts/lookup", ok=False, latency_ms=1000.0)
        record_llm_call(purpose="narrative", model="gpt-5", ok=True, latency_ms=500.0, cost_usd=0.125)
    block = layer_value(layer_usage([r.to_dict() for r in col.records]),
                        [{"scenario": "tariff", "severity": "mild",
                          "shapley": {"macrorag": -4_000_000.0, "xbrlrag_facts": 1_000_000.0}}])
    by = {t["layer"]: t for t in block["totals"]}
    assert by["macrorag"]["contribution_per_s"] == pytest.approx(2_000_000.0)      # 4M / 2 s
    assert by["macrorag"]["contribution_per_usd"] == pytest.approx(8_000_000.0)    # 4M / $0.5
    assert by["xbrlrag_facts"]["cost_usd"] is None and by["news"]["n_calls"] == 0
    assert block["overhead"] == {"n_calls": 1, "latency_s": pytest.approx(0.5), "cost_usd": pytest.approx(0.125)}
    assert OVERHEAD == "overhead"


def test_layer_cost_lines_one_per_total_then_overhead():
    block = {"totals": [
        {"severity": "mild", "layer": "macrorag", "contribution": 4_000_000.0, "n_calls": 2, "latency_s": 2.0,
         "cost_usd": None, "contribution_per_s": 2_000_000.0, "contribution_per_usd": None},
        {"severity": "mild", "layer": "news", "contribution": 0.0, "n_calls": 0, "latency_s": 0.0,
         "cost_usd": None, "contribution_per_s": None, "contribution_per_usd": None}],
        "overhead": {"n_calls": 1, "latency_s": 0.5, "cost_usd": 0.125}}
    lines = layer_cost_lines(block)
    assert len(lines) == 3
    assert "macrorag" in lines[0] and "4,000,000" in lines[0] and "not reported" in lines[0]
    assert lines[0].endswith("2,000,000/s") and not lines[1].endswith("/s")
    assert lines[2] == "overhead 1 call(s) 0.5s  cost $0.1250"
