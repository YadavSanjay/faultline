"""System / Ops reads for the UI (Phase 5) — the technical lens.

All over data the batch runners already wrote (run manifests + the snapshot manifests +
config), no engine calls. Traces are **manifest-level** in V1 (the locked decision):
per-stage spans need a log sink and are a fast follow. Provides the run trace, the
cross-module pipeline DAG (runs linked by snapshot id), a static module/architecture map,
the effective config (secrets masked), and a provenance pivot.
"""

from __future__ import annotations

import json
from typing import Any

import duckdb

from .settings import data_dir, project_root, service_url
from .stores import recent_runs

_SECRET_HINTS = ("key", "secret", "token", "password", "credential")


def _parse(extra: Any) -> dict[str, Any]:
    if isinstance(extra, dict):
        return extra
    if isinstance(extra, str) and extra:
        try:
            return json.loads(extra)
        except Exception:
            return {}
    return {}


def _find_run(run_id: str) -> dict[str, Any] | None:
    for r in recent_runs(limit=500):
        if r["run_id"] == run_id:
            return r
    return None


# --- spans (ENG-4 span-level trace) ---------------------------------------- #
_STAGE_ORDER = ["snapshot", "portfolio_load", "price_portfolio", "drift", "persist"]


def _read_spans(run_id: str) -> list[dict[str, Any]] | None:
    """Collected timing spans for a run (pricer runs only, for now)."""
    p = data_dir() / "pricer" / "store" / "spans" / f"{run_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("spans", [])
    except Exception:
        return None


def _aggregate_spans(spans: list[dict[str, Any]]) -> dict[str, Any]:
    stage_ms: dict[str, float] = {}
    by_ac: dict[str, dict[str, Any]] = {}
    for s in spans:
        if s.get("span") == "price":
            ac = str(s.get("asset_class", "?"))
            g = by_ac.setdefault(ac, {"asset_class": ac, "n": 0, "total_ms": 0.0})
            g["n"] += 1
            g["total_ms"] += float(s.get("duration_ms", 0.0))
        else:
            stage_ms[str(s.get("span"))] = float(s.get("duration_ms", 0.0))
    stages = [{"span": k, "duration_ms": round(stage_ms[k], 2)} for k in _STAGE_ORDER if k in stage_ms]
    stages += [{"span": k, "duration_ms": round(v, 2)} for k, v in stage_ms.items() if k not in _STAGE_ORDER]
    by_ac_list = sorted(
        ({"asset_class": g["asset_class"], "n": g["n"], "total_ms": round(g["total_ms"], 2),
          "mean_ms": round(g["total_ms"] / g["n"], 2)} for g in by_ac.values()),
        key=lambda x: -x["total_ms"])
    return {"available": True, "stages": stages, "by_asset_class": by_ac_list, "n_spans": len(spans)}


# --- run trace (S3) -------------------------------------------------------- #
def _pricer_trades(run_id: str) -> list[dict[str, Any]]:
    glob = (data_dir() / "pricer" / "store" / "results" / "**" / "*.parquet").as_posix()
    if not any((data_dir() / "pricer" / "store" / "results").glob("**/*.parquet")):
        return []
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT trade_id, asset_class, pv, status FROM read_parquet('{glob}', "
            f"hive_partitioning=true, hive_types_autocast=0) WHERE run_id = ? ORDER BY trade_id",
            [run_id],
        ).df().to_dict(orient="records")
    except Exception:
        rows = []
    finally:
        con.close()
    return rows


def _stages(module: str, counts: dict[str, Any], extra: dict[str, Any]) -> list[dict[str, str]]:
    """Manifest-level narrative stages (not spans — see module docstring)."""
    def g(k: str) -> Any:
        return counts.get(k)

    if module == "pricer":
        return [
            {"stage": "market-data snapshot", "detail": f"snapshot {extra.get('snapshot_id', '')[:12]}"},
            {"stage": "portfolio loaded", "detail": f"{g('n_trades')} trades"},
            {"stage": "priced", "detail": f"{g('n_ok')} ok / {g('n_blocked') or 0} blk / {g('n_failed') or 0} fail"},
            {"stage": "drift / govern", "detail": f"{g('n_drift_alerts') or 0} drift alerts"},
            {"stage": "persisted", "detail": "results store + manifest"},
        ]
    if module == "risk":
        return [
            {"stage": "sensitivities", "detail": f"{g('n_sensitivities')} trades, bump-and-revalue"},
            {"stage": "scenarios", "detail": f"{g('n_scenarios')} stress scenarios"},
            {"stage": "VaR + backtest", "detail": f"backtest {extra.get('backtest_zone', '—')}"},
            {"stage": "limits", "detail": f"{g('n_limit_breaches') or 0} breaches"},
            {"stage": "report written", "detail": "risk report JSON"},
        ]
    if module == "scenariogen":
        return [
            {"stage": "covariance Σ", "detail": f"cov {extra.get('cov_id', '')[:12]}"},
            {"stage": "materialize + gate",
             "detail": f"{g('n_run')} run / {g('n_held_gate') or 0} gated / {g('n_held_signoff') or 0} held"},
            {"stage": "stress + capital", "detail": f"{g('n_breaches') or 0} limit breaches"},
            {"stage": "report + dashboard", "detail": "stress report + result store"},
        ]
    return [{"stage": "run", "detail": module}]


def _module_command(module: str, date: str) -> str:
    cmd = {
        "pricer": "python -m src.pricer.runner",
        "risk": "python -m src.pricer.risk_runner --explain taiwan_geopolitical",
        "scenariogen": "python -m src.scenariogen.runner --var-lookback 60",
    }.get(module, f"python -m src.{module}.runner")
    return f"{cmd} --valuation-date {date}"


def run_trace(run_id: str) -> dict[str, Any]:
    run = _find_run(run_id)
    if run is None:
        return {"available": False, "run_id": run_id, "reason": "run not found in manifests"}
    counts = run.get("counts", {})
    module = run["module"]
    # recent_runs flattens counts but not extra_json; re-read extra for risk/scenariogen.
    raw_extra = _run_extra(run_id, module)
    snapshot_id = run.get("snapshot_id") or raw_extra.get("snapshot_id", "")
    raw_extra.setdefault("snapshot_id", snapshot_id)
    stages = _stages(module, counts, raw_extra)
    out: dict[str, Any] = {
        "available": True,
        "run": run,
        "extra": raw_extra,
        "stages": stages,
        "trace_level": "manifest",
        "reproduce": {
            "git_sha": run.get("git_sha"),
            "snapshot_id": snapshot_id,
            "factor_snapshot_id": raw_extra.get("factor_snapshot_id", ""),
            "command": _module_command(module, run.get("date", "")),
            "recipe": [
                f"git checkout {run.get('git_sha', '').replace('-dirty', '')}",
                "set PYTHONPATH + conda activate py312",
                _module_command(module, run.get("date", "")),
            ],
        },
    }
    if module == "pricer":
        out["trades"] = _pricer_trades(run_id)
    spans = _read_spans(run_id)
    if spans:
        out["spans"] = _aggregate_spans(spans)
        out["trace_level"] = "span"
    return out


def _run_extra(run_id: str, module: str) -> dict[str, Any]:
    if module == "pricer":
        return {}
    g = (data_dir() / module / "store" / "runs" / "**" / "*.parquet").as_posix()
    if not any((data_dir() / module / "store" / "runs").glob("**/*.parquet")):
        return {}
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT extra_json FROM read_parquet('{g}', hive_partitioning=true, "
            f"hive_types_autocast=0, union_by_name=true) WHERE run_id = ?",
            [run_id],
        ).fetchall()
    except Exception:
        rows = []
    finally:
        con.close()
    return _parse(rows[0][0]) if rows else {}


# --- pipeline DAG (S4) ----------------------------------------------------- #
def pipeline(valuation_date: str | None) -> dict[str, Any]:
    runs = recent_runs(limit=200)
    if valuation_date:
        runs = [r for r in runs if r["date"] == valuation_date]
    # group by market-data snapshot
    groups: dict[str, dict[str, Any]] = {}
    for r in runs:
        snap = r.get("snapshot_id") or _run_extra(r["run_id"], r["module"]).get("snapshot_id", "") or "(unknown)"
        grp = groups.setdefault(snap, {"snapshot_id": snap, "runs": []})
        grp["runs"].append({"module": r["module"], "run_id": r["run_id"], "status": r["status"],
                            "date": r["date"], "finished_at": r["finished_at"]})
    # annotate snapshots from manifests
    snap_dir = data_dir() / "pricer" / "snapshots"
    for snap_id, grp in groups.items():
        manifest = snap_dir / f"{snap_id}.json"
        if manifest.exists():
            try:
                m = json.loads(manifest.read_text(encoding="utf-8"))
                grp["file_count"] = m.get("file_count")
                grp["created_at"] = m.get("created_at")
            except Exception:
                pass
    return {"valuation_date": valuation_date, "snapshots": list(groups.values())}


# --- module / architecture map (S5) ---------------------------------------- #
def modules() -> dict[str, Any]:
    nodes = [
        {"id": "marketdata", "label": "Market Data", "route": "/marketdata",
         "desc": "Per-source downloaders -> CSVs (rates, equity, credit, fx, options, bonds, stress)."},
        {"id": "common", "label": "common", "route": "/system/config",
         "desc": "Shared obs (logging/metrics/tracing), config, provenance, snapshots, DQ gate."},
        {"id": "pricer.input", "label": "pricer / input", "route": None,
         "desc": "Pure domain trades + market-data requirements (no QuantLib)."},
        {"id": "pricer.loader", "label": "pricer / loader", "route": None,
         "desc": "Resolve requirements -> curves/vols; content-addressed snapshot."},
        {"id": "pricer.ql", "label": "pricer / ql", "route": "/pricing",
         "desc": "QuantLib pricers per asset class -> PricingResult."},
        {"id": "pricer.risk", "label": "pricer / risk", "route": "/risk",
         "desc": "Bump-and-revalue sensitivities, VaR/ES, scenarios, backtest, limits."},
        {"id": "pricer.governance", "label": "pricer / governance", "route": "/governance",
         "desc": "Model inventory, IPV, golden gate."},
        {"id": "pricer.output", "label": "pricer / output", "route": None,
         "desc": "Per-trade JSON + Parquet results store (DuckDB)."},
        {"id": "scenariogen", "label": "scenariogen", "route": "/scenarios",
         "desc": "Covariance -> conditional-covariance scenarios -> ES-under-shock + plausibility + sign-off."},
    ]
    edges = [
        {"from": "marketdata", "to": "pricer.loader"},
        {"from": "pricer.input", "to": "pricer.loader"},
        {"from": "pricer.loader", "to": "pricer.ql"},
        {"from": "pricer.ql", "to": "pricer.output"},
        {"from": "pricer.ql", "to": "pricer.risk"},
        {"from": "pricer.ql", "to": "pricer.governance"},
        {"from": "pricer.risk", "to": "scenariogen"},
        {"from": "common", "to": "pricer.ql"},
    ]
    flow = ["marketdata", "pricer.loader", "pricer.ql", "pricer.risk", "scenariogen"]
    return {"nodes": nodes, "edges": edges, "flow": flow}


# --- config explorer (S6) -------------------------------------------------- #
def _mask(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if any(h in k.lower() for h in _SECRET_HINTS):
            out[k] = "***" if v else None
        else:
            out[k] = v
    return out


def config() -> dict[str, Any]:
    cfg_path = project_root() / "configs" / "pricer" / "pricer_config.json"
    try:
        model_config = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        model_config = {}
    settings: dict[str, Any] = {}
    try:
        from src.pricer.settings import get_settings

        settings = _mask(get_settings().model_dump())
    except Exception as exc:
        settings = {"error": str(exc)}
    return {
        "model_config": model_config,
        "settings": settings,
        "upstream_service": service_url(),
        "note": ("Model params are change-controlled (config_hash); settings are the operational "
                 "env overlay (PRICER_*). Secrets masked."),
    }


# --- provenance pivot (L3) ------------------------------------------------- #
def provenance(snapshot_id: str | None, run_id: str | None,
               portfolio_version: str | None) -> dict[str, Any]:
    runs = recent_runs(limit=300)
    matched: list[dict[str, Any]] = []
    for r in runs:
        extra = _run_extra(r["run_id"], r["module"]) if r["module"] != "pricer" else {}
        snap = r.get("snapshot_id") or extra.get("snapshot_id", "")
        if (
            (snapshot_id and snap == snapshot_id)
            or (run_id and r["run_id"] == run_id)
            or (portfolio_version and extra.get("portfolio_version") == portfolio_version)
        ):
            matched.append(r)
    return {
        "query": {"snapshot_id": snapshot_id, "run_id": run_id, "portfolio_version": portfolio_version},
        "runs": matched,
        "count": len(matched),
    }
