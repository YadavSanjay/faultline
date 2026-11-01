"""LLM cost & operations reads for the UI (Operations console, Phase 2) — stored-only.

The scenariogen LLM flow records what it spends but nothing aggregates it: per-run cost /
per-stage tokens live in ``data/pricer/scenario_llm/<run_id>/manifest.json`` (written by
``LLMScenarioArtifactStore``), and the dual-RAG call trace lives in
``data/pricer/scenario/<date>/trace/<run_id>.jsonl``. This module rolls both up across runs
so the Operations console can show total spend, per-stage / per-service breakdowns, a run
index, budget-vs-actual, and RAG service health — none of which had a cross-run surface.

All reads are best-effort and defensive: the LLM-manifest tree may not exist yet (no LLM
generation run has been executed), in which case the LLM rollups are simply empty while the
dual-RAG trace rollup still reflects real activity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.common.config.env import expand_str

from .settings import data_dir, project_root

_STAGE_KEYS = ("n_calls", "prompt_tokens", "completion_tokens", "cost_usd")


# --- LLM scenario-gen manifests -------------------------------------------- #
def _llm_runs_dir() -> Path:
    return data_dir() / "pricer" / "scenario_llm"


def _iter_manifests() -> list[tuple[str, dict[str, Any]]]:
    base = _llm_runs_dir()
    if not base.exists():
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        mf = d / "manifest.json"
        if not mf.exists():
            continue
        try:
            out.append((d.name, json.loads(mf.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


_LEGACY_PROVIDER = "openai"  # manifests written before multi-provider support carry no `llm` block


def _stage_providers(m: dict[str, Any]) -> dict[str, str]:
    """Stage -> provider for one run; a pre-multi-provider manifest ran every stage on openai."""
    stages = ((m.get("llm") or {}).get("stages")) or {}
    if stages:
        return {s: str(v.get("provider", "")) for s, v in stages.items()}
    return dict.fromkeys(((m.get("cost") or {}).get("per_stage") or {}), _LEGACY_PROVIDER)


def _per_provider(m: dict[str, Any]) -> dict[str, dict[str, float]]:
    """One run's per-stage cost rollup grouped by the provider each stage ran on."""
    providers = _stage_providers(m)
    out: dict[str, dict[str, float]] = {}
    for stage, vals in (((m.get("cost") or {}).get("per_stage")) or {}).items():
        row = out.setdefault(providers.get(stage) or "unknown", dict.fromkeys(_STAGE_KEYS, 0.0))
        for k in _STAGE_KEYS:
            row[k] += float(vals.get(k, 0) or 0)
    return out


def _run_row(run_id: str, m: dict[str, Any]) -> dict[str, Any]:
    cost = m.get("cost", {}) or {}
    tokens = int(cost.get("total_prompt_tokens", 0)) + int(cost.get("total_completion_tokens", 0))
    return {
        "run_id": m.get("run_id", run_id),
        "mode": m.get("mode", ""),
        "created_at": m.get("created_at", ""),
        "profile": (m.get("llm") or {}).get("profile") or _LEGACY_PROVIDER,
        "providers": sorted(set(_stage_providers(m).values())),
        "world_view_id": m.get("world_view_id", ""),
        "total_cost_usd": round(float(cost.get("total_cost_usd", 0.0)), 6),
        "n_llm_calls": int(cost.get("n_llm_calls", 0)),
        "n_tool_calls": int(cost.get("n_tool_calls", 0)),
        "total_tokens": tokens,
        "n_scenarios": len(m.get("calibrated", []) or []),
    }


def _add_stage(agg: dict[str, dict[str, float]], per_stage: dict[str, Any]) -> None:
    for stage, vals in (per_stage or {}).items():
        row = agg.setdefault(stage, dict.fromkeys(_STAGE_KEYS, 0.0))
        for k in _STAGE_KEYS:
            row[k] += float(vals.get(k, 0) or 0)


# --- budget / model tiers -------------------------------------------------- #
def _llm_config() -> dict[str, Any]:
    path = project_root() / "configs" / "scenariogen" / "llm" / "llm.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _model_tiers(cfg: dict[str, Any]) -> dict[str, str]:
    """Stage -> model of the active profile; a pre-profiles llm.json's flat `model_tiers` as is."""
    if cfg.get("model_tiers"):
        return dict(cfg["model_tiers"])
    profiles = cfg.get("profiles", {}) or {}
    active = expand_str(str(cfg.get("active_profile", "openai")))
    return dict(profiles.get(active, {}) or {})


def _budget() -> dict[str, Any]:
    cfg = _llm_config()
    b = cfg.get("budgets", {}) or {}
    return {
        "max_total_cost_usd": float(b.get("max_total_cost_usd", 0.0)),
        "max_scenarios": int(b.get("max_scenarios", 0)),
        "max_tool_calls_per_phase": int(b.get("max_tool_calls_per_phase", 0)),
        "model_tiers": _model_tiers(cfg),
        "active_profile": expand_str(str(cfg.get("active_profile", _LEGACY_PROVIDER))),
        "source": "configs/scenariogen/llm/llm.json",
    }


# --- dual-RAG call traces -------------------------------------------------- #
def _scenario_dir() -> Path:
    return data_dir() / "pricer" / "scenario"


def _iter_trace_files() -> list[tuple[str, str, Path]]:
    """(valuation_date, run_id, path) for every stored call-trace JSONL."""
    base = _scenario_dir()
    if not base.exists():
        return []
    out: list[tuple[str, str, Path]] = []
    for date_dir in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        tdir = date_dir / "trace"
        if not tdir.exists():
            continue
        out.extend((date_dir.name, jf.stem, jf) for jf in sorted(tdir.glob("*.jsonl")))
    return out


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue
    return recs


def _slim_call(rec: dict[str, Any]) -> dict[str, Any]:
    """List-view of a call — metadata only, large bodies dropped to presence flags."""
    return {
        "seq": rec.get("seq"), "service": rec.get("service"), "purpose": rec.get("purpose"),
        "ok": rec.get("ok"), "latency_ms": rec.get("latency_ms"), "model": rec.get("model", ""),
        "scenario": rec.get("scenario", ""),
        "prompt_tokens": rec.get("prompt_tokens", 0), "completion_tokens": rec.get("completion_tokens", 0),
        "total_tokens": rec.get("total_tokens", 0), "cost_usd": rec.get("cost_usd", 0.0),
        "error": rec.get("error", ""),
    }


def _trace_rollup(path: Path) -> dict[str, Any]:
    """Aggregate one JSONL trace: totals + per-service rollup."""
    by_service: dict[str, dict[str, Any]] = {}
    n_calls = 0
    total_cost = total_latency = 0.0
    for rec in _read_jsonl(path):
        n_calls += 1
        svc = rec.get("service", "?")
        b = by_service.setdefault(svc, {"n_calls": 0, "n_errors": 0, "cost_usd": 0.0,
                                        "latency_ms": 0.0, "total_tokens": 0})
        b["n_calls"] += 1
        b["n_errors"] += 0 if rec.get("ok") else 1
        b["cost_usd"] = round(b["cost_usd"] + float(rec.get("cost_usd", 0.0) or 0.0), 6)
        b["latency_ms"] = round(b["latency_ms"] + float(rec.get("latency_ms", 0.0) or 0.0), 1)
        b["total_tokens"] += int(rec.get("total_tokens", 0) or 0)
        total_cost += float(rec.get("cost_usd", 0.0) or 0.0)
        total_latency += float(rec.get("latency_ms", 0.0) or 0.0)
    return {
        "n_calls": n_calls,
        "total_cost_usd": round(total_cost, 6),
        "total_latency_ms": round(total_latency, 1),
        "by_service": by_service,
    }


def _merge_service(agg: dict[str, dict[str, Any]], by_service: dict[str, dict[str, Any]]) -> None:
    for svc, vals in by_service.items():
        row = agg.setdefault(svc, {"n_calls": 0, "n_errors": 0, "cost_usd": 0.0,
                                   "latency_ms": 0.0, "total_tokens": 0})
        row["n_calls"] += int(vals.get("n_calls", 0))
        row["n_errors"] += int(vals.get("n_errors", 0))
        row["cost_usd"] = round(row["cost_usd"] + float(vals.get("cost_usd", 0.0)), 6)
        row["latency_ms"] = round(row["latency_ms"] + float(vals.get("latency_ms", 0.0)), 1)
        row["total_tokens"] += int(vals.get("total_tokens", 0))


# --- public API ------------------------------------------------------------ #
def summary() -> dict[str, Any]:
    """Operations landing rollup: LLM spend (from manifests) + dual-RAG activity (from
    traces) + budget-vs-actual + spend-over-time."""
    manifests = _iter_manifests()
    budget = _budget()
    ceiling = budget["max_total_cost_usd"]

    per_stage: dict[str, dict[str, float]] = {}
    per_provider: dict[str, dict[str, float]] = {}
    per_run: list[dict[str, Any]] = []
    llm_cost = llm_tokens = 0.0
    n_llm_calls = n_tool_calls = 0
    for run_id, m in manifests:
        cost = m.get("cost", {}) or {}
        _add_stage(per_stage, cost.get("per_stage", {}))
        _add_stage(per_provider, _per_provider(m))
        row = _run_row(run_id, m)
        row["over_budget"] = ceiling > 0 and row["total_cost_usd"] > ceiling
        per_run.append(row)
        llm_cost += row["total_cost_usd"]
        llm_tokens += row["total_tokens"]
        n_llm_calls += row["n_llm_calls"]
        n_tool_calls += row["n_tool_calls"]

    # dual-RAG traces (real activity today; $0 for RAG-only runs)
    rag_service: dict[str, dict[str, Any]] = {}
    n_traces = rag_calls = 0
    rag_cost = rag_latency = 0.0
    for _date, _rid, path in _iter_trace_files():
        roll = _trace_rollup(path)
        n_traces += 1
        rag_calls += roll["n_calls"]
        rag_cost += roll["total_cost_usd"]
        rag_latency += roll["total_latency_ms"]
        _merge_service(rag_service, roll["by_service"])

    max_run = max((r["total_cost_usd"] for r in per_run), default=0.0)
    spend_over_time = sorted(
        ({"label": r["created_at"] or r["run_id"], "run_id": r["run_id"],
          "cost_usd": r["total_cost_usd"]} for r in per_run),
        key=lambda x: x["label"],
    )

    return {
        "budget": budget,
        "llm": {
            "n_runs": len(per_run),
            "total_cost_usd": round(llm_cost, 6),
            "total_tokens": int(llm_tokens),
            "n_llm_calls": n_llm_calls,
            "n_tool_calls": n_tool_calls,
            "per_stage": per_stage,
            "per_provider": per_provider,
            "per_run": per_run,
            "max_run_cost_usd": round(max_run, 6),
            "n_over_budget": sum(1 for r in per_run if r.get("over_budget")),
        },
        "rag": {
            "n_traces": n_traces,
            "n_calls": rag_calls,
            "total_cost_usd": round(rag_cost, 6),
            "total_latency_ms": round(rag_latency, 1),
            "by_service": rag_service,
        },
        "totals": {
            "total_cost_usd": round(llm_cost + rag_cost, 6),
            "n_runs": len(per_run),
            "n_traces": n_traces,
        },
        "spend_over_time": spend_over_time,
    }


def runs() -> dict[str, Any]:
    """Index of LLM scenario-generation runs (from the manifest store)."""
    rows = [_run_row(rid, m) for rid, m in _iter_manifests()]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return {"runs": rows, "count": len(rows), "budget": _budget()}


def run_detail(run_id: str) -> dict[str, Any]:
    """One LLM run's manifest: cost rollup, per-stage tokens, models, and scenario names."""
    for rid, m in _iter_manifests():
        if rid == run_id or m.get("run_id") == run_id:
            cost = m.get("cost", {}) or {}
            budget = _budget()
            total = round(float(cost.get("total_cost_usd", 0.0)), 6)
            return {
                "available": True,
                **_run_row(rid, m),
                "models": m.get("models", {}),
                "stage_providers": _stage_providers(m),
                "sdk_versions": (m.get("llm") or {}).get("sdk_versions", {}),
                "discovery": m.get("discovery", ""),
                "ranking": m.get("ranking", []),
                "scenarios": [c.get("name", "") for c in (m.get("calibrated", []) or [])],
                "cost": {
                    "total_cost_usd": total,
                    "total_prompt_tokens": int(cost.get("total_prompt_tokens", 0)),
                    "total_completion_tokens": int(cost.get("total_completion_tokens", 0)),
                    "n_llm_calls": int(cost.get("n_llm_calls", 0)),
                    "n_tool_calls": int(cost.get("n_tool_calls", 0)),
                    "per_stage": cost.get("per_stage", {}),
                    "per_provider": _per_provider(m),
                },
                "budget": {"max_total_cost_usd": budget["max_total_cost_usd"],
                           "over_budget": budget["max_total_cost_usd"] > 0 and total > budget["max_total_cost_usd"]},
            }
    return {"available": False, "run_id": run_id, "reason": "no LLM generation run with that id"}


def traces() -> dict[str, Any]:
    """Cross-run index of dual-RAG call traces (the LLM/RAG activity captured on disk)."""
    rows: list[dict[str, Any]] = []
    for date, run_id, path in _iter_trace_files():
        roll = _trace_rollup(path)
        rows.append({
            "valuation_date": date,
            "run_id": run_id,
            "n_calls": roll["n_calls"],
            "total_cost_usd": roll["total_cost_usd"],
            "total_latency_ms": roll["total_latency_ms"],
            "services": sorted(roll["by_service"].keys()),
            "n_errors": sum(s["n_errors"] for s in roll["by_service"].values()),
        })
    return {"traces": rows, "count": len(rows)}


def trace_detail(run_id: str) -> dict[str, Any]:
    """One dual-RAG trace's per-call list + rollup (drilldown for the run explorer)."""
    for date, rid, path in _iter_trace_files():
        if rid == run_id:
            roll = _trace_rollup(path)
            return {
                "available": True,
                "run_id": run_id,
                "valuation_date": date,
                "n_calls": roll["n_calls"],
                "total_cost_usd": roll["total_cost_usd"],
                "total_latency_ms": roll["total_latency_ms"],
                "by_service": roll["by_service"],
                "calls": [_slim_call(r) for r in _read_jsonl(path)],
            }
    return {"available": False, "run_id": run_id, "reason": "no trace with that run id"}


def _conn_health(filename: str, name: str, role: str) -> dict[str, Any]:
    path = project_root() / "configs" / "scenariogen" / filename
    try:
        conn = json.loads(path.read_text(encoding="utf-8")).get("connection", {})
    except Exception:
        conn = {}
    enabled = bool(conn.get("enabled", False))
    return {
        "service": name,
        "role": role,
        "enabled": enabled,
        "status": "configured" if enabled else "disabled",
        "base_url": conn.get("base_url", ""),
        "breaker_threshold": conn.get("breaker_threshold"),
        "timeout_s": conn.get("timeout_s"),
        "max_attempts": conn.get("max_attempts"),
    }


def health() -> dict[str, Any]:
    """RAG service posture (config-level) for MacroRAG + ARag, with breaker settings."""
    return {
        "services": [
            _conn_health("macro.json", "MacroRAG", "systematic macro-leg drivers (world-view synthesis)"),
            _conn_health("arag.json", "ARag", "idiosyncratic company overlays (narrative search + facts)"),
        ],
        "note": "Config-level status. Live health needs the RAG services running; fallback/breaker "
                "counters (scenariogen_macro_fallback_total, scenariogen_arag_stop_total) are exposed "
                "on /api/system/metrics when the pricer service is up.",
    }
