"""Stress-scenario reads for the UI (Phase 4) — from the scenariogen stores (V1 stored-only).

The scenariogen runner writes a StressReport JSON per run
(``data/pricer/scenario/<date>/scenario_<run_id>.json``) with, per scenario x tier: the
materialized factor vector + provenance (drivers/overlays/covariance/macro), full-reval
P&L (total + by asset class + per trade), ES-under-shock capital, and plausibility. Version
specs live in the scenario store, sign-off in the sign-off store. The correlation matrix for
the shock-derivation heatmap is computed on demand (ENG-3: ``FactorCovariance.correlation()``
— a QuantLib-free numpy path).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
from src.scenariogen.report_views import (
    ablation_view,
    coverage_view,
    decision_view,
    definition_view,
    replay_view,
    select_report,
    signal_health_view,
)

from .settings import data_dir, project_root


# --- config (narratives, tiers) -------------------------------------------- #
@lru_cache(maxsize=1)
def _scenario_config() -> dict[str, Any]:
    path = project_root() / "configs" / "scenariogen" / "scenarios.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"scenarios": {}, "severity_tiers": {}}


def _tiers() -> list[str]:
    tiers = _scenario_config().get("severity_tiers", {})
    return sorted(tiers, key=lambda t: tiers[t])  # mild < adverse < severe by multiplier


# --- report selection ------------------------------------------------------ #
def _scenario_dir() -> Path:
    return data_dir() / "pricer" / "scenario"


def available_dates() -> list[str]:
    base = _scenario_dir()
    if not base.exists():
        return []
    return sorted((d.name for d in base.iterdir()
                   if d.is_dir() and (any(d.glob("scenario_*.json")) or any(d.glob("ablation_*.json")))),
                  reverse=True)


def _ablation_dates() -> list[str]:
    base = _scenario_dir()
    if not base.exists():
        return []
    return sorted((d.name for d in base.iterdir() if d.is_dir() and any(d.glob("ablation_*.json"))),
                  reverse=True)


def _date_reports(vdate: str | None, pattern: str) -> list[dict[str, Any]]:
    """Every decodable report matching ``pattern`` (``scenario_*.json`` / ``ablation_*.json``) for the date."""
    if not vdate:
        return []
    out: list[dict[str, Any]] = []
    for path in sorted((_scenario_dir() / vdate).glob(pattern)):
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rep, dict):
            out.append(rep)
    return out


def _newest_stress_report(vdate: str | None) -> dict[str, Any] | None:
    """The newest stress report of the date (``select_report``); None when none or the set is inconsistent."""
    try:
        rep = select_report(_date_reports(vdate, "scenario_*.json"))
    except ValueError:
        return None
    return dict(rep) if rep is not None else None


def _tier_outcome(name: str, tier: str, ran: bool, newest: dict[str, Any] | None) -> str:
    """``ran`` | ``held_out`` | ``stopped`` | ``not_run`` for a scenario tier, from the newest report."""
    if ran:
        return "ran"
    rep = newest or {}
    if any(h.get("scenario") == name and h.get("severity") == tier for h in rep.get("held_out") or []):
        return "held_out"
    if any(s.get("scenario") == name for s in rep.get("stopped") or []):
        return "stopped"
    return "not_run"


def _load_version_spec(version: str | None) -> dict[str, Any] | None:
    """The version-store content ``data/pricer/store/scenario/versions/<version>.json`` (None if absent)."""
    if not version or not all(c.isalnum() or c in "-_" for c in str(version)):
        return None
    path = data_dir() / "pricer" / "store" / "scenario" / "versions" / f"{version}.json"
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return spec if isinstance(spec, dict) else None


def _safe_definition(raw_spec: dict[str, Any], entry: dict[str, Any] | None = None,
                     stored_spec: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """``definition_view`` for display; None when a spec has an unreadable unit key."""
    try:
        return definition_view(raw_spec, entry=entry, stored_spec=stored_spec)
    except ValueError:
        return None


def _load_report(valuation_date: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """The most complete report (most scenarios; newest tie-break) for the date."""
    dates = available_dates()
    if not dates:
        return None, None
    vdate = valuation_date if valuation_date in dates else dates[0]
    best: dict[str, Any] | None = None
    best_key = (-1, 0.0)
    for path in (_scenario_dir() / vdate).glob("scenario_*.json"):
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        key = (len(rep.get("scenarios", [])), path.stat().st_mtime)
        if key > best_key:
            best_key, best = key, rep
    return vdate, best


def _report_index(rep: dict[str, Any] | None) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for s in (rep or {}).get("scenarios", []):
        out[(s.get("scenario"), s.get("severity"))] = s
    return out


def _merged_index(vdate: str | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Union of scenario entries across ALL reports for the date (newest mtime wins on conflict).

    Static and LLM-generated (source='llm') scenarios live in separate report files; merging so a
    small LLM report isn't hidden behind a larger static one.
    """
    if not vdate:
        return {}
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    seen: dict[tuple[str, str], float] = {}
    for path in (_scenario_dir() / vdate).glob("scenario_*.json"):
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        mtime = path.stat().st_mtime
        for s in rep.get("scenarios", []):
            key = (s.get("scenario"), s.get("severity"))
            if key not in idx or mtime > seen[key]:
                idx[key], seen[key] = s, mtime
    return idx


def _default_severity(severity: str | None, tiers: list[str]) -> str:
    """Use the requested severity as-is (a tier, or 'llm' for LLM scenarios); else default."""
    if severity:
        return severity
    return "adverse" if "adverse" in tiers else (tiers[0] if tiers else "")


# --- sign-off + versions --------------------------------------------------- #
@lru_cache(maxsize=1)
def _signoff_by_version() -> dict[str, dict[str, Any]]:
    d = data_dir() / "pricer" / "store" / "signoff"
    if not any(d.glob("*.parquet")):
        return {}
    con = duckdb.connect()
    try:
        # SELECT * + defensive access — older sign-off parquets have a partial schema.
        records = con.execute(
            f"SELECT * FROM read_parquet('{(d / '*.parquet').as_posix()}', union_by_name=true)"
        ).df().to_dict(orient="records")
    finally:
        con.close()
    records.sort(key=lambda r: r.get("seq", 0) if r.get("seq") is not None else 0)
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        ver = r.get("scenario_version")
        if not ver:
            continue
        out[str(ver)] = {
            "status": r.get("status"), "approver": r.get("approver"),
            "recorded_at": r.get("recorded_at"), "note": r.get("note") or "",
        }
    return out


def versions() -> dict[str, Any]:
    d = data_dir() / "pricer" / "store" / "scenario" / "manifest"
    if not any(d.glob("*.parquet")):
        return {"versions": []}
    con = duckdb.connect()
    try:
        df = con.execute(
            f"SELECT scenario_version, created_at, name, method, n_drivers, n_overlays, horizon_days "
            f"FROM read_parquet('{(d / '*.parquet').as_posix()}', union_by_name=true) "
            f"ORDER BY created_at DESC"
        ).df()
    finally:
        con.close()
    signoff = _signoff_by_version()
    rows = df.to_dict(orient="records")
    for r in rows:
        r["signoff"] = signoff.get(str(r["scenario_version"]), {}).get("status", "pending")
    return {"versions": rows}


def signoff() -> dict[str, Any]:
    return {"records": _signoff_by_version()}


def record_signoff(scenario_version: str, scenario: str, status: str, approver: str,
                   note: str = "") -> dict[str, Any]:
    """Gated write: record an approve/reject decision for a scenario version to the sign-off
    store (append-only, latest-wins), then invalidate the read cache. Returns the new record
    or an error the caller surfaces as a 400."""
    status = str(status).strip().lower()
    if status not in ("approved", "rejected"):
        return {"ok": False, "error": f"status must be 'approved' or 'rejected', got {status!r}"}
    if not scenario_version:
        return {"ok": False, "error": "scenario_version is required"}
    try:
        from src.scenariogen.signoff import SignoffStore

        rec = SignoffStore(project_root()).record(scenario_version, scenario, status, approver, note)
    except Exception as exc:
        return {"ok": False, "error": f"could not record sign-off: {exc}"}
    _signoff_by_version.cache_clear()
    return {"ok": True, "record": rec.as_dict()}


# --- factor unit / origin helpers ------------------------------------------ #
def _display(factor: str, move: float) -> dict[str, Any]:
    if factor.startswith("ir:") or factor == "credit":
        return {"unit": "bp", "value": round(move * 1e4, 1)}
    if factor.startswith(("eq:", "fx:")):
        return {"unit": "%", "value": round(move * 100, 2)}
    if factor.startswith("eqvol"):
        return {"unit": "pts", "value": round(move * 100, 2)}
    if factor.startswith("ratesvol"):
        return {"unit": "rel", "value": round(move, 4)}
    return {"unit": "", "value": round(move, 4)}


def _origin(factor: str, drivers: dict, overlays: dict, macro_factors: set[str],
            arag_factors: set[str] = frozenset()) -> str:
    if factor in macro_factors:
        return "macro"
    if factor in arag_factors:
        return "arag"
    if factor in drivers:
        return "driver"
    if factor in overlays:
        return "overlay"
    return "derived"


def _macro_prov(r: dict[str, Any]) -> dict[str, Any]:
    """The effective MacroRAG provenance block for a scenario result, source-aware.

    Dual-RAG runs store it under ``provenance.dualrag.macro`` (the classic path uses
    ``provenance.macro``). Reading the wrong key made ``used_macro`` always False for dual-RAG
    scenarios — the source of the stale "MacroRAG not used / disabled" UI messages.
    """
    prov = (r.get("materialized", {}) or {}).get("provenance", {}) or {}
    if r.get("source") == "dualrag":
        return (prov.get("dualrag") or {}).get("macro") or {}
    return prov.get("macro") or {}


# --- catalog --------------------------------------------------------------- #
def _llm_cell(name: str, severity: str, r: dict[str, Any]) -> dict[str, Any]:
    cap = r.get("capital") or {}
    llm = r.get("llm") or {}
    return {
        "scenario": name,
        "severity": severity,
        "source": "llm",
        "total_pnl": r.get("total_pnl"),
        "capital_delta": cap.get("capital_delta"),
        "plausibility_status": (r.get("plausibility") or {}).get("status"),
        "scenario_version": r.get("scenario_version", ""),
        "n_trades": len(r.get("trades", [])),
        "world_view_id": llm.get("world_view_id", ""),
        "mode": llm.get("mode", ""),
        "run_id": llm.get("run_id", ""),
        "total_cost_usd": llm.get("total_cost_usd"),
        "trigger": llm.get("trigger", ""),
    }


def catalog(valuation_date: str | None) -> dict[str, Any]:
    vdate, rep = _load_report(valuation_date)
    cfg = _scenario_config()
    specs = cfg.get("scenarios", {})
    tiers = _tiers()
    idx = _merged_index(vdate)
    signoff = _signoff_by_version()
    newest = _newest_stress_report(vdate)

    cells: list[dict[str, Any]] = []
    for name in specs:
        spec = specs[name]
        for tier in tiers:
            r = idx.get((name, tier))
            ver = (r or {}).get("scenario_version", "")
            cap = (r or {}).get("capital") or {}
            r_macro = _macro_prov(r) if r else {}
            # A replay's raw horizon is a placeholder; show the report-resolved one (None if unresolved).
            definition = _safe_definition(spec, entry=r)
            cells.append({
                "scenario": name,
                "severity": tier,
                "narrative": spec.get("narrative", ""),
                "horizon_days": definition["horizon_days"] if definition is not None else None,
                "ran": r is not None,
                "outcome": _tier_outcome(name, tier, r is not None, newest),
                "total_pnl": (r or {}).get("total_pnl"),
                "capital_delta": cap.get("capital_delta"),
                "plausibility_status": ((r or {}).get("plausibility") or {}).get("status"),
                "scenario_version": ver,
                "used_macro": bool(r_macro.get("used_macro", False)),
                "n_trades": len((r or {}).get("trades", [])),
                "signoff": signoff.get(ver, {}).get("status", "pending"),
            })

    # LLM-generated scenarios (source='llm') aren't in the static specs-x-tiers grid — surface them
    # as their own ranked list (worst loss first).
    llm_scenarios = [_llm_cell(nm, sev, r) for (nm, sev), r in idx.items() if r.get("source") == "llm"]
    llm_scenarios.sort(key=lambda c: c["total_pnl"] if c["total_pnl"] is not None else 0.0)

    return {
        "valuation_date": vdate,
        "available_dates": available_dates(),
        "scenarios": list(specs),
        "tiers": tiers,
        "cells": cells,
        "llm_scenarios": llm_scenarios,
        "covariance": (rep or {}).get("covariance", {}),
        "run_id": (rep or {}).get("run_id"),
        "snapshot_id": (rep or {}).get("snapshot_id"),
        "generated_at": (rep or {}).get("generated_at"),
        "limits": _loss_limits(),
    }


def _loss_limits() -> dict[str, float | None]:
    """The book's stress-loss thresholds (USD, positive) from configs/scenariogen/limits.json.

    Read fresh on each call so a limit change shows without a BFF restart; a missing or
    unreadable file yields nulls (the UI then shows no thresholds).
    """
    path = project_root() / "configs" / "scenariogen" / "limits.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"loss_limit": None, "loss_warning": None}
    return {k: (float(cfg[k]) if cfg.get(k) is not None else None) for k in ("loss_limit", "loss_warning")}


# --- detail (shock derivation) --------------------------------------------- #
def detail(name: str, severity: str | None, valuation_date: str | None) -> dict[str, Any]:
    vdate, rep = _load_report(valuation_date)
    cfg = _scenario_config().get("scenarios", {}).get(name, {})
    tiers = _tiers()
    sev = _default_severity(severity, tiers)
    r = _merged_index(vdate).get((name, sev))
    if r is None:
        return _not_run_detail(name, sev, cfg, vdate, tiers)

    source = r.get("source", "static")
    llm = r.get("llm") or {}
    mat = r.get("materialized", {})
    prov = mat.get("provenance", {})
    drivers = prov.get("drivers", {}) or {}
    overlays = prov.get("overlays", {}) or {}
    dualrag = prov.get("dualrag") or {}
    macro = _macro_prov(r)          # source-aware: dualrag.macro for dual-RAG, else prov.macro
    macro_sources = macro.get("sources", {}) if isinstance(macro, dict) else {}
    arag_factors: set[str] = set()
    if source == "llm":
        # LLM driver origins come from the calibrated legs (macro/equity/overlay/driver).
        macro_factors = {leg["factor"] for leg in llm.get("legs", []) if leg.get("origin") == "macro"}
    else:
        macro_factors = {k for k, v in macro_sources.items() if (v or {}).get("origin") == "macrorag"}
        if source == "dualrag":     # ARag company legs (idiosyncratic) get their own origin tag
            arag_factors = set((dualrag.get("arag") or {}).get("company_drivers", {}))

    vector = []
    for f, move in (mat.get("vector") or {}).items():
        disp = _display(f, move)
        vector.append({"factor": f, "move": move,
                       "origin": _origin(f, drivers, overlays, macro_factors, arag_factors),
                       "unit": disp["unit"], "display": disp["value"]})
    vector.sort(key=lambda v: (v["origin"] != "driver", v["origin"] != "overlay", -abs(v["move"])))

    sv = r.get("scenario_version", "")
    if source == "llm":
        narrative, trigger = "", llm.get("trigger", "")
        macro_block = {"used_macro": True, "world_view_id": llm.get("world_view_id", ""), "sources": {}}
    else:
        narrative, trigger = cfg.get("narrative", ""), cfg.get("trigger", "")
        macro_block = (
            {"used_macro": bool(macro.get("used_macro")), "summary": macro.get("macro_summary", ""),
             "world_view_id": macro.get("world_view_id", ""), "sources": macro_sources}
            if macro else {"used_macro": False}
        )
    return {
        "available": True,
        "scenario": name,
        "severity": sev,
        "source": source,
        "llm": llm if source == "llm" else None,
        "narrative_available": source == "llm",  # full narrative served by /{name}/narrative
        "attribution_available": r.get("attribution") is not None,  # dual-RAG value-add (P4)
        "trace_available": source == "dualrag",   # captured call-trace (P4)
        "narrative": narrative,
        "trigger": trigger,
        "method": prov.get("method"),
        "horizon_days": prov.get("horizon_days"),
        "severity_scale": prov.get("severity_scale"),
        "scenario_version": sv,
        "valuation_date": vdate,
        "generated_at": (rep or {}).get("generated_at"),
        "snapshot_id": (rep or {}).get("snapshot_id"),
        "covariance": prov.get("covariance", {}),
        "drivers": {k: _display(k, v) for k, v in drivers.items()},
        "overlays": {k: _display(k, v) for k, v in overlays.items()},
        "vector": vector,
        "plausibility": r.get("plausibility"),
        "capital": r.get("capital"),
        "total_pnl": r.get("total_pnl"),
        "signoff": _signoff_by_version().get(sv, {"status": "pending"}),
        "macro": macro_block,
        "tiers": tiers,
        "outcome": "ran",
        # Replay window block (start, end, n_observed, excluded idiosyncratic moves, ...) or None.
        "replay": prov.get("replay"),
    }


def _not_run_detail(name: str, sev: str, cfg: dict[str, Any], vdate: str | None,
                    tiers: list[str]) -> dict[str, Any]:
    """Detail of a tier absent from every report of the date: why, plus the definition to show.

    The outcome comes from the newest stress report (held out by the gate, stopped fail-loud, or not
    run). A held-out tier's replay-resolved spec is read from the version store by its
    ``scenario_version``, so a replay still shows its drivers and real horizon.
    """
    newest = _newest_stress_report(vdate) or {}
    held = next((h for h in newest.get("held_out") or []
                 if h.get("scenario") == name and h.get("severity") == sev), None)
    stopped = next((s for s in newest.get("stopped") or [] if s.get("scenario") == name), None)
    stored = _load_version_spec(held.get("scenario_version")) if held is not None else None
    definition = _safe_definition(cfg, stored_spec=stored)
    out: dict[str, Any] = {
        "available": False, "scenario": name, "severity": sev, "narrative": cfg.get("narrative", ""),
        "trigger": cfg.get("trigger", ""), "valuation_date": vdate, "tiers": tiers,
        "definition": definition,
    }
    if held is not None:
        findings = held.get("findings") or []
        n_fail = sum(1 for f in findings if f.get("severity") == "fail")
        n_flag = sum(1 for f in findings if f.get("severity") == "flag")
        out.update({"outcome": "held_out", "findings": findings,
                    "scenario_version": held.get("scenario_version", ""),
                    "reason": f"held out by the plausibility gate ({n_fail} fail, {n_flag} flag)"})
    elif stopped is not None:
        out.update({"outcome": "stopped", "stopped": stopped,
                    "reason": f"stopped: {stopped.get('reason', '')}"
                              + (f" ({stopped['detail']})" if stopped.get("detail") else "")})
    else:
        out.update({"outcome": "not_run", "reason": "not run in the latest report"})
    if definition is not None:
        out.update({
            "horizon_days": definition["horizon_days"],
            "method": (stored or cfg).get("method"),
            "drivers": {k: _display(k, v) for k, v in definition["drivers"].items()},
            "overlays": {k: _display(k, v) for k, v in definition["overlays"].items()},
            "replay": definition["replay"],
        })
    return out


def results(name: str, severity: str | None, valuation_date: str | None) -> dict[str, Any]:
    vdate, _rep = _load_report(valuation_date)
    tiers = _tiers()
    sev = _default_severity(severity, tiers)
    r = _merged_index(vdate).get((name, sev))
    if r is None:
        return {"available": False, "scenario": name, "severity": sev}
    return {
        "available": True,
        "scenario": name,
        "severity": sev,
        "source": r.get("source", "static"),
        "total_pnl": r.get("total_pnl"),
        "pnl_by_asset_class": r.get("pnl_by_asset_class", {}),
        "capital": r.get("capital"),
        "trades": r.get("trades", []),
    }


def narrative(name: str, severity: str | None, valuation_date: str | None) -> dict[str, Any]:
    """The 1-2 page markdown narrative for an LLM-generated scenario.

    Reads ``data/pricer/scenario_llm/<run_id>/narratives/<slug>.md`` (the run_id + narrative filename
    are in the scenario's ``llm`` provenance block). Mirrors the pricing "How it's priced" markdown
    pattern; returns ``{available: false}`` (not 404) for static scenarios or when no file exists.
    """
    vdate, _rep = _load_report(valuation_date)
    tiers = _tiers()
    sev = _default_severity(severity, tiers)
    r = _merged_index(vdate).get((name, sev))
    if r is None or r.get("source") != "llm":
        return {"available": False, "scenario": name, "severity": sev,
                "reason": "no LLM-generated narrative for this scenario"}
    llm = r.get("llm") or {}
    run_id, fname = llm.get("run_id"), llm.get("narrative")
    if not run_id or not fname:
        return {"available": False, "scenario": name, "severity": sev, "reason": "narrative provenance missing"}
    path = data_dir() / "pricer" / "scenario_llm" / str(run_id) / "narratives" / str(fname)
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        return {"available": False, "scenario": name, "severity": sev, "run_id": run_id,
                "reason": "narrative file not found"}
    return {
        "available": True,
        "scenario": name,
        "severity": sev,
        "run_id": run_id,
        "world_view_id": llm.get("world_view_id", ""),
        "mode": llm.get("mode", ""),
        "cost": {"total_cost_usd": llm.get("total_cost_usd"),
                 "n_llm_calls": llm.get("n_llm_calls"), "n_tool_calls": llm.get("n_tool_calls")},
        "markdown": markdown,
    }


# --- dual-RAG: value-add attribution + call trace + cost (P4) -------------- #
def _merged_with_run(vdate: str | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Like ``_merged_index`` but also records which report (run_id) each entry came from."""
    if not vdate:
        return {}
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    seen: dict[tuple[str, str], float] = {}
    for path in (_scenario_dir() / vdate).glob("scenario_*.json"):
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rid, mtime = rep.get("run_id"), path.stat().st_mtime
        for s in rep.get("scenarios", []):
            key = (s.get("scenario"), s.get("severity"))
            if key not in idx or mtime > seen[key]:
                idx[key], seen[key] = {"r": s, "run_id": rid}, mtime
    return idx


def _scenario_run(vdate: str | None, name: str) -> dict[str, Any] | None:
    """The winning entry + run_id for a scenario (attribution/trace are tier-independent)."""
    idx = _merged_with_run(vdate)
    for sev in ("adverse", "severe", "mild"):
        if (name, sev) in idx:
            return idx[(name, sev)]
    for (n, _s), e in idx.items():
        if n == name:
            return e
    return None


def _read_trace(vdate: str, run_id: str, name: str | None = None) -> list[dict[str, Any]]:
    """Read the per-run call-trace JSONL; optionally filter to one scenario's calls."""
    path = _scenario_dir() / vdate / "trace" / f"{run_id}.jsonl"
    if not path.exists():
        return []
    recs: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if name is None or rec.get("scenario") in (name, "", None):
            recs.append(rec)
    # If the trace is scenario-tagged, keep only this scenario; else (untagged) keep all.
    if name is not None and any(r.get("scenario") for r in recs):
        recs = [r for r in recs if r.get("scenario") == name]
    return recs


def _slim_call(rec: dict[str, Any]) -> dict[str, Any]:
    """List-view of a call — metadata only, large bodies replaced by presence flags."""
    return {
        "seq": rec.get("seq"), "service": rec.get("service"), "purpose": rec.get("purpose"),
        "ok": rec.get("ok"), "latency_ms": rec.get("latency_ms"), "model": rec.get("model", ""),
        "prompt_tokens": rec.get("prompt_tokens", 0), "completion_tokens": rec.get("completion_tokens", 0),
        "total_tokens": rec.get("total_tokens", 0), "cost_usd": rec.get("cost_usd", 0.0),
        "prompt_hash": rec.get("prompt_hash", ""), "error": rec.get("error", ""),
        "has_request": bool(rec.get("request")), "has_response": bool(rec.get("response")),
        "has_prompt": bool(rec.get("system") or rec.get("user")),
    }


def attribution(name: str, valuation_date: str | None) -> dict[str, Any]:
    """The dual-RAG value-add attribution + narrative for a scenario (what each service added)."""
    vdate, _rep = _load_report(valuation_date)
    entry = _scenario_run(vdate, name)
    if entry is None or entry["r"].get("attribution") is None:
        return {"available": False, "scenario": name,
                "reason": "no dual-RAG attribution (run with --dual-rag)"}
    r = entry["r"]
    return {
        "available": True, "scenario": name, "valuation_date": vdate,
        "run_id": entry["run_id"], "severity": r.get("severity"),
        "attribution": r.get("attribution"), "narrative": r.get("narrative"),
    }


def trace(name: str, valuation_date: str | None) -> dict[str, Any]:
    """The captured call-trace (slimmed list + rollup) for a scenario's dual-RAG generation."""
    vdate, _rep = _load_report(valuation_date)
    entry = _scenario_run(vdate, name)
    if entry is None:
        return {"available": False, "scenario": name, "reason": "scenario not in the latest report"}
    recs = _read_trace(vdate or "", entry["run_id"], name)
    by_service: dict[str, dict[str, Any]] = {}
    total_cost = total_latency = 0.0
    for rec in recs:
        b = by_service.setdefault(rec.get("service", "?"),
                                  {"n_calls": 0, "n_errors": 0, "cost_usd": 0.0, "latency_ms": 0.0})
        b["n_calls"] += 1
        b["n_errors"] += 0 if rec.get("ok") else 1
        b["cost_usd"] = round(b["cost_usd"] + rec.get("cost_usd", 0.0), 6)
        b["latency_ms"] = round(b["latency_ms"] + rec.get("latency_ms", 0.0), 1)
        total_cost += rec.get("cost_usd", 0.0)
        total_latency += rec.get("latency_ms", 0.0)
    return {
        "available": True, "scenario": name, "valuation_date": vdate, "run_id": entry["run_id"],
        "n_calls": len(recs), "total_cost_usd": round(total_cost, 6),
        "total_latency_ms": round(total_latency, 1), "by_service": by_service,
        "calls": [_slim_call(r) for r in recs],
    }


def trace_call(name: str, seq: int, valuation_date: str | None) -> dict[str, Any]:
    """A single call's full record — request, response, and (LLM) system+user prompts."""
    vdate, _rep = _load_report(valuation_date)
    entry = _scenario_run(vdate, name)
    if entry is None:
        return {"available": False, "scenario": name, "seq": seq}
    for rec in _read_trace(vdate or "", entry["run_id"], name):
        if rec.get("seq") == seq:
            return {"available": True, "scenario": name, "run_id": entry["run_id"], "call": rec}
    return {"available": False, "scenario": name, "seq": seq, "reason": "call not found"}


def cost(name: str, valuation_date: str | None) -> dict[str, Any]:
    """Per-call + per-service + total cost for a scenario (LLM priced; RAG calls at $0)."""
    t = trace(name, valuation_date)
    if not t.get("available"):
        return {"available": False, "scenario": name}
    return {
        "available": True, "scenario": name, "run_id": t["run_id"],
        "total_cost_usd": t["total_cost_usd"], "by_service": t["by_service"],
        "per_call": [{"seq": c["seq"], "service": c["service"], "purpose": c["purpose"],
                      "cost_usd": c["cost_usd"], "total_tokens": c["total_tokens"],
                      "latency_ms": c["latency_ms"]} for c in t["calls"]],
    }


# --- correlation heatmap (ENG-3) ------------------------------------------- #
@lru_cache(maxsize=1)
def correlation() -> dict[str, Any]:
    """Compute Σ's correlation matrix for the shock-derivation heatmap (QuantLib-free)."""
    try:
        from src.pricer.config import load_pricer_config
        from src.pricer.risk.factor_history import FactorHistory
        from src.scenariogen.config import load_covariance_config
        from src.scenariogen.covariance import covariance_from_history

        root = project_root()
        config = load_pricer_config()
        cov_cfg = load_covariance_config(root)
        history = FactorHistory(root, config)
        cov = covariance_from_history(
            history,
            lookback=int(cov_cfg.get("lookback_days", 250)),
            window=str(cov_cfg.get("window", "full")),
            ewma_lambda=float(cov_cfg.get("ewma_lambda", 0.94)),
            crisis_windows=[tuple(w) for w in cov_cfg.get("crisis_windows", [])] or None,
        )
        corr = cov.correlation()
        return {
            "available": True,
            "factors": list(cov.factors),
            "correlation": [[round(float(corr[i][j]), 4) for j in range(len(cov.factors))]
                            for i in range(len(cov.factors))],
            "cov_id": cov.cov_id,
            "window": cov.window,
            "n_obs": cov.n_obs,
        }
    except Exception as exc:
        return {"available": False, "reason": f"could not compute covariance: {exc}"}


# --- reverse stress (S5) --------------------------------------------------- #
def _reverse_dates() -> list[str]:
    base = _scenario_dir()
    if not base.exists():
        return []
    return sorted((d.name for d in base.iterdir() if d.is_dir() and any(d.glob("reverse_*.json"))),
                  reverse=True)


def _load_reverse(valuation_date: str | None) -> tuple[str | None, dict[str, Any] | None]:
    dates = _reverse_dates()
    if not dates:
        return None, None
    vdate = valuation_date if valuation_date in dates else dates[0]
    files = sorted((_scenario_dir() / vdate).glob("reverse_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return vdate, None
    try:
        return vdate, json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return vdate, None


def _scenario_desc(name: str) -> str:
    return _scenario_config().get("scenarios", {}).get(name, {}).get("description", "")


def reverse(valuation_date: str | None) -> dict[str, Any]:
    """Reverse-stress results (S5): per scenario, the severity λ that drives the full-reval
    loss to the loss limit — ranked most-fragile-first (lowest λ to breach). "What breaks us?"."""
    vdate, rep = _load_reverse(valuation_date)
    if rep is None:
        return {"available": False, "valuation_date": vdate, "available_dates": _reverse_dates(),
                "reason": "no reverse-stress run yet — run `python -m src.scenariogen.runner --reverse-stress`"}
    results = []
    for r in rep.get("results", []):
        mat = r.get("materialized", {})
        results.append({
            "scenario": r.get("scenario"),
            "description": _scenario_desc(r.get("scenario", "")),
            "reachable": bool(r.get("reachable")),
            "severity_scale": r.get("severity_scale"),
            "achieved_loss": r.get("achieved_loss"),
            "target_loss": r.get("target_loss"),
            "iterations": r.get("iterations"),
            "n_drivers": len(mat.get("drivers", {}) or {}),
        })
    # most fragile first: reachable (lower λ = more fragile) before unreachable
    results.sort(key=lambda x: (not x["reachable"], x["severity_scale"] if x["severity_scale"] is not None else 1e9))
    return {
        "available": True,
        "valuation_date": vdate,
        "available_dates": _reverse_dates(),
        "run_id": rep.get("run_id"),
        "snapshot_id": rep.get("snapshot_id"),
        "generated_at": rep.get("generated_at"),
        "target_loss": rep.get("target_loss"),
        "n_scenarios": len(results),
        "n_reachable": sum(1 for r in results if r["reachable"]),
        "results": results,
    }


def reverse_detail(name: str, valuation_date: str | None) -> dict[str, Any]:
    """One scenario's reverse-stress detail incl. the materialized breaking-shock vector."""
    vdate, rep = _load_reverse(valuation_date)
    if rep is None:
        return {"available": False, "scenario": name, "valuation_date": vdate}
    entry = next((r for r in rep.get("results", []) if r.get("scenario") == name), None)
    if entry is None:
        return {"available": False, "scenario": name, "valuation_date": vdate,
                "reason": "scenario not in the reverse-stress run"}
    mat = entry.get("materialized", {})
    return {
        "available": True,
        "scenario": name,
        "description": _scenario_desc(name),
        "valuation_date": vdate,
        "run_id": rep.get("run_id"),
        "target_loss": entry.get("target_loss"),
        "reachable": bool(entry.get("reachable")),
        "severity_scale": entry.get("severity_scale"),
        "achieved_loss": entry.get("achieved_loss"),
        "iterations": entry.get("iterations"),
        "drivers": mat.get("drivers", {}),
        "vector": mat.get("vector", {}),
        "provenance": mat.get("provenance", {}),
    }


# --- macro health ---------------------------------------------------------- #
def macro_health() -> dict[str, Any]:
    path = project_root() / "configs" / "scenariogen" / "macro.json"
    try:
        conn = json.loads(path.read_text(encoding="utf-8")).get("connection", {})
    except Exception:
        conn = {}
    enabled = bool(conn.get("enabled", False))
    return {
        "enabled": enabled,
        "status": "configured" if enabled else "disabled",
        "base_url": conn.get("base_url", ""),
        "note": "MacroRAG drives macro-leg drivers when enabled; scenarios fall back to static drivers otherwise.",
    }


# --- layer ablation waterfall + signal health (step 15f) ------------------- #
def _resolve_date(valuation_date: str | None, dates: list[str]) -> str | None:
    if not dates:
        return None
    return valuation_date if valuation_date in dates else dates[0]


def _ablation_report(valuation_date: str | None, severity: str | None,
                     run_id: str | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The selected ablation report of the date plus the response header (or a ``reason``)."""
    dates = _ablation_dates()
    vdate = _resolve_date(valuation_date, dates)
    reports = _date_reports(vdate, "ablation_*.json")
    runs = sorted(({"run_id": r.get("run_id"), "severity": r.get("severity"),
                    "generated_at": r.get("generated_at")} for r in reports),
                  key=lambda x: str(x["generated_at"] or ""), reverse=True)
    head: dict[str, Any] = {"valuation_date": vdate, "available_dates": dates, "runs": runs}
    if not reports:
        return {**head, "reason": "no ablation run for this date (run src.scenariogen.ablation_runner)"}, None
    candidates = [r for r in reports if severity is None or r.get("severity") == severity]
    try:
        rep = select_report(candidates, run_id=run_id)
    except ValueError as exc:
        return {**head, "reason": str(exc)}, None
    if rep is None:
        want = f"run {run_id}" if run_id else f"severity {severity}" if severity else "any run"
        return {**head, "reason": f"no ablation report for {want} on {vdate}"}, None
    return head, dict(rep)


def ablation(valuation_date: str | None, severity: str | None = None,
             run_id: str | None = None) -> dict[str, Any]:
    """The layer-ablation view (per-scenario waterfall + grouped Shapley + cost per step) of one run."""
    head, rep = _ablation_report(valuation_date, severity, run_id)
    if rep is None:
        return {"available": False, **head}
    try:
        view = ablation_view(rep)
    except ValueError as exc:
        return {"available": False, **head, "reason": str(exc)}
    return {"available": True, "available_dates": head["available_dates"], "runs": head["runs"], **view}


def ablation_scenario(name: str, valuation_date: str | None, severity: str | None = None,
                      run_id: str | None = None) -> dict[str, Any]:
    """One scenario's waterfall from the selected ablation run, plus the run-level fields."""
    out = ablation(valuation_date, severity, run_id)
    if not out["available"]:
        return {**out, "scenario": name}
    scenarios = out.pop("scenarios")
    match = next((w for w in scenarios if w["scenario"] == name), None)
    if match is None:
        return {**out, "available": False, "scenario": name,
                "reason": f"{name} is not in ablation run {out.get('run_id')}"}
    return {**out, "scenario": name, "waterfall": match}


def signal_health(valuation_date: str | None, run_id: str | None = None) -> dict[str, Any]:
    """The signal-health view of the date's newest stress report carrying it (else the newest report)."""
    dates = available_dates()
    vdate = _resolve_date(valuation_date, dates)
    reports = _date_reports(vdate, "scenario_*.json")
    empty: dict[str, Any] = {"available": False, "run_id": None, "valuation_date": vdate,
                             "generated_at": None, "overall": None, "counts": None, "sources": [],
                             "held_out": [], "stopped": [], "attention": 0, "available_dates": dates}
    try:
        newest = select_report(reports, run_id=run_id)   # also validates the whole set (duplicate run_id)
        # A report can carry the key with a None block (signal health not computed): only a real block
        # counts, else fall back to the newest report so held-out and stopped still show.
        with_block = [r for r in reports if r.get("signal_health") is not None]
        rep = None if run_id is not None else select_report(with_block, require="signal_health")
        if rep is None:
            rep = newest
    except ValueError as exc:
        return {**empty, "reason": str(exc)}
    if rep is None:
        return {**empty, "reason": f"no stress report{' ' + run_id if run_id else ''} for {vdate}"}
    return {**signal_health_view(rep), "available_dates": dates}


# --- measurement: coverage, decision impact, event replay ------------------ #
def _measurement_dates() -> list[str]:
    """Valuation dates with a coverage or decision-impact report, newest first."""
    base = _scenario_dir()
    if not base.exists():
        return []
    return sorted((d.name for d in base.iterdir()
                   if d.is_dir() and (any(d.glob("coverage_*.json")) or any(d.glob("decision_*.json")))),
                  reverse=True)


def _replay_reports() -> list[dict[str, Any]]:
    """Every decodable event-replay report (``data/scenariogen/event_replay/``; not per valuation date)."""
    out: list[dict[str, Any]] = []
    for path in sorted((data_dir() / "scenariogen" / "event_replay").glob("event_replay_*.json")):
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(rep, dict):
            out.append(rep)
    return out


def _measurement_block(reports: list[dict[str, Any]], view: Callable[[Mapping[str, Any]], dict[str, Any]],
                       missing: str) -> dict[str, Any]:
    """The view of the newest report (``select_report``) with its ids, or ``{"available": False, "reason"}``."""
    try:
        rep = select_report(reports)
        if rep is None:
            return {"available": False, "reason": missing}
        body = view(rep)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:   # a malformed report
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    head = {"available": True, "run_id": rep.get("run_id"), "stress_run_id": rep.get("stress_run_id"),
            "generated_at": rep.get("generated_at"), "tier": rep.get("tier")}
    return {**head, **body}


def measurement(valuation_date: str | None) -> dict[str, Any]:
    """Coverage and decision-impact views of the date's newest reports, and the newest event-replay view."""
    dates = _measurement_dates()
    vdate = _resolve_date(valuation_date, dates)
    on = f" for {vdate}" if vdate else ""
    return {
        "valuation_date": vdate, "available_dates": dates,
        "coverage": _measurement_block(_date_reports(vdate, "coverage_*.json"), coverage_view,
                                       f"no coverage report{on} (run src.scenariogen.coverage_runner)"),
        "decision": _measurement_block(_date_reports(vdate, "decision_*.json"), decision_view,
                                       f"no decision-impact report{on} (run src.scenariogen.decision_impact_runner)"),
        "replay": _measurement_block(_replay_reports(), replay_view,
                                     "no event-replay report (run src.scenariogen.event_replay_runner)"),
    }
