"""BFF LLM-cost / operations rollups (`/llmops/*`)."""

from __future__ import annotations

import json
from pathlib import Path

from bff_helpers import (
    call_record,
    write_llm_config,
    write_llm_manifest,
    write_rag_config,
    write_trace,
)
from ui.bff.app import llmops

BUDGETS = {"max_total_cost_usd": 5.0, "max_scenarios": 10, "max_tool_calls_per_phase": 6}
TIERS = {"propose": "gpt-5", "narrate": "gpt-5-mini"}


def _manifest(cost: float) -> dict:
    return {
        "mode": "comprehensive",
        "created_at": "2026-04-01T10:00:00",
        "world_view_id": "macro-x",
        "models": TIERS,
        "calibrated": [{"name": "s1"}, {"name": "s2"}],
        "cost": {
            "total_cost_usd": cost,
            "total_prompt_tokens": 100,
            "total_completion_tokens": 50,
            "n_llm_calls": 3,
            "n_tool_calls": 1,
            "per_stage": {"propose": {"n_calls": 1, "prompt_tokens": 100,
                                      "completion_tokens": 50, "cost_usd": cost}},
        },
    }


def test_budget_model_tiers_come_from_the_active_profile(bff_root: Path, monkeypatch) -> None:
    d = bff_root / "configs" / "scenariogen" / "llm"
    d.mkdir(parents=True, exist_ok=True)
    (d / "llm.json").write_text(json.dumps({
        "active_profile": "${SCENARIOGEN_LLM_PROFILE:-openai}", "budgets": BUDGETS,
        "profiles": {"openai": TIERS, "deepseek": {"propose": "deepseek-v4-pro"}},
    }), encoding="utf-8")
    monkeypatch.delenv("SCENARIOGEN_LLM_PROFILE", raising=False)
    assert llmops.summary()["budget"]["model_tiers"] == TIERS
    monkeypatch.setenv("SCENARIOGEN_LLM_PROFILE", "deepseek")
    assert llmops.summary()["budget"]["model_tiers"] == {"propose": "deepseek-v4-pro"}


def test_cost_is_split_by_provider_and_legacy_runs_count_as_openai(bff_root: Path) -> None:
    write_llm_config(bff_root, BUDGETS, TIERS)
    write_llm_manifest(bff_root, "OLD", _manifest(2.0))  # pre-multi-provider: no `llm` block
    mixed = _manifest(0.0)
    mixed["cost"]["per_stage"] = {
        "propose": {"n_calls": 1, "prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.3},
        "evidence": {"n_calls": 2, "prompt_tokens": 400, "completion_tokens": 20, "cost_usd": 0.1},
    }
    mixed["llm"] = {"profile": "mixed", "sdk_versions": {"anthropic": "0.109.1"},
                    "stages": {"propose": {"model": "claude-sonnet-5-5", "provider": "anthropic"},
                               "evidence": {"model": "deepseek-flash", "provider": "deepseek"}}}
    write_llm_manifest(bff_root, "NEW", mixed)

    s = llmops.summary()
    by = s["llm"]["per_provider"]
    assert by["openai"]["cost_usd"] == 2.0
    assert by["anthropic"]["cost_usd"] == 0.3
    assert by["deepseek"]["cost_usd"] == 0.1
    assert by["deepseek"]["n_calls"] == 2
    rows = {r["run_id"]: r for r in s["llm"]["per_run"]}
    assert (rows["OLD"]["profile"], rows["OLD"]["providers"]) == ("openai", ["openai"])
    assert (rows["NEW"]["profile"], rows["NEW"]["providers"]) == ("mixed", ["anthropic", "deepseek"])

    d = llmops.run_detail("NEW")
    assert d["stage_providers"] == {"propose": "anthropic", "evidence": "deepseek"}
    assert d["sdk_versions"] == {"anthropic": "0.109.1"}
    assert set(d["cost"]["per_provider"]) == {"anthropic", "deepseek"}


def test_summary_aggregates_manifests_and_traces(bff_root: Path) -> None:
    write_llm_config(bff_root, BUDGETS, TIERS)
    write_llm_manifest(bff_root, "R1", _manifest(2.0))
    write_trace(bff_root, "2026-04-01", "T1", [
        call_record(seq=1, service="llm", purpose="narrate", cost_usd=0.01, total_tokens=500, model="gpt-5-mini"),
        call_record(seq=2, service="macrorag", purpose="/query", latency_ms=5000.0),
    ])

    s = llmops.summary()
    assert s["budget"]["max_total_cost_usd"] == 5.0
    assert s["llm"]["n_runs"] == 1
    assert s["llm"]["total_cost_usd"] == 2.0
    assert "propose" in s["llm"]["per_stage"]
    assert s["rag"]["n_traces"] == 1
    assert s["rag"]["n_calls"] == 2
    assert s["rag"]["by_service"]["llm"]["cost_usd"] == 0.01
    assert s["rag"]["by_service"]["macrorag"]["n_calls"] == 1
    # combined total = manifest 2.0 + trace 0.01
    assert s["totals"]["total_cost_usd"] == 2.01
    assert len(s["spend_over_time"]) == 1


def test_runs_index_and_run_detail(bff_root: Path) -> None:
    write_llm_config(bff_root, BUDGETS, TIERS)
    write_llm_manifest(bff_root, "R1", _manifest(2.0))

    idx = llmops.runs()
    assert idx["count"] == 1
    assert idx["runs"][0]["run_id"] == "R1"
    assert idx["runs"][0]["n_scenarios"] == 2

    d = llmops.run_detail("R1")
    assert d["available"] is True
    assert d["cost"]["total_cost_usd"] == 2.0
    assert d["scenarios"] == ["s1", "s2"]
    assert d["models"]["propose"] == "gpt-5"
    assert d["budget"]["over_budget"] is False


def test_run_detail_over_budget_and_missing(bff_root: Path) -> None:
    write_llm_config(bff_root, BUDGETS, TIERS)
    write_llm_manifest(bff_root, "R2", _manifest(6.0))  # over the 5.0 ceiling

    d = llmops.run_detail("R2")
    assert d["budget"]["over_budget"] is True

    missing = llmops.run_detail("NOPE")
    assert missing["available"] is False


def test_traces_index_and_detail(bff_root: Path) -> None:
    write_trace(bff_root, "2026-04-01", "T1", [
        call_record(seq=1, service="llm", purpose="narrate", cost_usd=0.02, total_tokens=800),
        call_record(seq=2, service="arag", purpose="/api/v1/narrative/search", latency_ms=9000.0),
        call_record(seq=3, service="macrorag", purpose="/query", ok=False),
    ])

    idx = llmops.traces()
    assert idx["count"] == 1
    row = idx["traces"][0]
    assert row["run_id"] == "T1"
    assert row["n_calls"] == 3
    assert row["n_errors"] == 1
    assert set(row["services"]) == {"llm", "arag", "macrorag"}

    d = llmops.trace_detail("T1")
    assert d["available"] is True
    assert d["n_calls"] == 3
    assert d["total_cost_usd"] == 0.02
    assert d["by_service"]["macrorag"]["n_errors"] == 1
    assert len(d["calls"]) == 3

    assert llmops.trace_detail("NOPE")["available"] is False


def test_health_reads_both_rag_configs(bff_root: Path) -> None:
    write_rag_config(bff_root, "macro.json", {"enabled": True, "base_url": "http://m:8000", "breaker_threshold": 5})
    write_rag_config(bff_root, "arag.json", {"enabled": False, "base_url": "http://a:8001", "breaker_threshold": 3})

    h = llmops.health()
    names = {s["service"]: s for s in h["services"]}
    assert names["MacroRAG"]["enabled"] is True
    assert names["MacroRAG"]["breaker_threshold"] == 5
    assert names["ARag"]["status"] == "disabled"
