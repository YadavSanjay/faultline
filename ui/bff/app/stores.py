"""Read access over the platform's stored artifacts (Parquet runs + risk JSON).

Phase 0 needs two reads, both over data already written by the batch runners — no engine
calls, no live pricing:

* ``recent_runs`` — the cross-module run history (``data/*/store/runs/``), normalized to a
  single shape across the pricer's richer manifest and the generic per-module manifest.
* ``governance_posture`` — a control-room rollup derived from the latest pricer run
  manifest and the latest risk-run JSON (limit breaches + backtest zone). Controls that
  need a live ``/governance/verify`` (golden, IPV, inventory) are reported as ``not_run``
  in V1 rather than fabricated.
"""

from __future__ import annotations

import json
import math
from typing import Any

import duckdb

from .settings import data_dir

_PRICER_COUNT_COLS = (
    "n_trades", "n_ok", "n_blocked", "n_failed", "n_warnings", "n_drift_alerts")


def _runs_glob() -> str:
    return (data_dir() / "*" / "store" / "runs" / "**" / "*.parquet").as_posix()


def _has_runs() -> bool:
    return any((data_dir()).glob("*/store/runs/**/*.parquet"))


def _clean(value: Any) -> Any:
    """JSON-safe scalar (DuckDB/pandas give NaN/NaT/numpy types)."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    return value


def _module_from_filename(filename: str) -> str:
    parts = filename.replace("\\", "/").split("/")
    if "data" in parts:
        i = parts.index("data")
        if i + 1 < len(parts):
            return parts[i + 1]
    return "unknown"


def _normalize_run(row: dict[str, Any]) -> dict[str, Any]:
    module = _clean(row.get("module")) or _module_from_filename(str(row.get("filename", "")))
    date = _clean(row.get("valuation_date")) or _clean(row.get("partition")) or ""

    counts: dict[str, Any] = {}
    if _clean(row.get("counts_json")):
        try:
            counts = json.loads(row["counts_json"])
        except Exception:
            counts = {}
    for col in _PRICER_COUNT_COLS:
        v = _clean(row.get(col))
        if v is not None:
            counts[col] = v

    return {
        "module": module,
        "run_id": _clean(row.get("run_id")),
        "date": str(date),
        "started_at": _clean(row.get("started_at")),
        "finished_at": _clean(row.get("finished_at")),
        "duration_s": _clean(row.get("duration_s")),
        "status": _clean(row.get("status")) or "unknown",
        "git_sha": _clean(row.get("git_sha")) or "",
        "snapshot_id": _clean(row.get("snapshot_id")) or "",
        "total_pv": _clean(row.get("total_pv")),
        "counts": counts,
    }


def recent_runs(limit: int = 50, module: str | None = None) -> list[dict[str, Any]]:
    """Cross-module run history. Each module's manifests are read separately because the
    pricer partitions by ``valuation_date=`` and the generic modules by ``partition=`` —
    DuckDB hive-partitioning can't union mismatched keys in one glob."""
    runs_root = data_dir()
    module_dirs = sorted(p for p in runs_root.glob("*/store/runs") if p.is_dir())
    records: list[dict[str, Any]] = []
    con = duckdb.connect()
    try:
        for d in module_dirs:
            if not any(d.glob("**/*.parquet")):
                continue
            glob = (d / "**" / "*.parquet").as_posix()
            try:
                rows = con.execute(
                    f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true, "
                    f"hive_types_autocast=0, union_by_name=true, filename=true) "
                    f"ORDER BY finished_at DESC LIMIT {int(limit) * 4}"
                ).df().to_dict(orient="records")
            except Exception:
                rows = []
            records.extend(rows)
    finally:
        con.close()

    runs = [_normalize_run(r) for r in records]
    runs.sort(key=lambda r: r.get("finished_at") or "", reverse=True)
    if module:
        runs = [r for r in runs if r["module"] == module]
    return runs[: int(limit)]


def _latest_risk_report() -> dict[str, Any] | None:
    risk_dir = data_dir() / "pricer" / "risk"
    if not risk_dir.exists():
        return None
    files = sorted(risk_dir.glob("*/risk_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def latest_governance_report() -> dict[str, Any] | None:
    gov_dir = data_dir() / "pricer" / "governance"
    if not gov_dir.exists():
        return None
    files = sorted(gov_dir.glob("*/governance_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def _tile(status: str, headline: str, detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"status": status, "headline": headline, "detail": detail, **extra}


def governance_posture(valuation_date: str | None = None) -> dict[str, Any]:
    """Derive a control-room posture from stored artifacts (no engine calls)."""
    pricer_runs = recent_runs(limit=20, module="pricer")
    if valuation_date:
        pricer_runs = [r for r in pricer_runs if r["date"] == valuation_date]
    latest = pricer_runs[0] if pricer_runs else None

    tiles: dict[str, dict[str, Any]] = {}
    severities: list[str] = []

    # --- Pricing run health (real) -------------------------------------------- #
    if latest:
        c = latest["counts"]
        n_failed = int(c.get("n_failed", 0) or 0)
        n_blocked = int(c.get("n_blocked", 0) or 0)
        n_drift = int(c.get("n_drift_alerts", 0) or 0)
        n_ok = int(c.get("n_ok", 0) or 0)
        n_trades = int(c.get("n_trades", 0) or 0)
        if n_failed:
            st = "red"
        elif n_blocked or n_drift:
            st = "amber"
        else:
            st = "green"
        severities.append(st)
        tiles["run_health"] = _tile(
            st, f"{n_ok}/{n_trades} priced",
            f"{n_blocked} blocked · {n_failed} failed · {n_drift} drift",
            run_id=latest["run_id"], date=latest["date"], snapshot_id=latest["snapshot_id"])
    else:
        tiles["run_health"] = _tile("unknown", "no pricer run found", "run the pricer to populate")

    # --- Risk limits + backtest (real, from stored risk JSON) ----------------- #
    risk = _latest_risk_report()
    if risk:
        breaches = risk.get("limit_breaches") or []
        zone = ((risk.get("backtest") or {}).get("zone")) or "unknown"
        zone_sev = {"green": "green", "amber": "amber", "red": "red"}.get(zone, "unknown")
        limit_sev = "red" if breaches else "green"
        severities.extend([s for s in (zone_sev, limit_sev) if s != "unknown"])
        tiles["limits"] = _tile(
            limit_sev, f"{len(breaches)} breaches",
            "VaR / sensitivity limits", run_id=risk.get("run_id"))
        tiles["backtest"] = _tile(
            zone_sev, f"{zone} zone",
            "VaR backtest traffic light", run_id=risk.get("run_id"))
    else:
        tiles["limits"] = _tile("unknown", "no risk run found")
        tiles["backtest"] = _tile("unknown", "no risk run found")

    # --- Governance controls (real, from the stored governance report) -------- #
    gov = latest_governance_report()
    if gov:
        golden = gov.get("golden", {})
        g_st = "green" if golden.get("passed") else "red"
        tiles["golden"] = _tile(g_st, "pass" if golden.get("passed") else f"{golden.get('n_breaks', 0)} breaks",
                                f"{golden.get('n_checked', 0)} checked", run_id=gov.get("run_id"))
        ipv = gov.get("ipv", {})
        i_st = "green" if ipv.get("all_passed") else "red"
        n_total = int(ipv.get("n_total", 0) or 0)
        n_failed = int(ipv.get("n_failed", 0) or 0)
        tiles["ipv"] = _tile(i_st, f"{n_total - n_failed}/{n_total} controls",
                             f"{n_failed} failed", run_id=gov.get("run_id"))
        mi = gov.get("model_inventory", {})
        missing = mi.get("missing", []) or []
        m_st = "green" if not missing else "red"
        tiles["model_inventory"] = _tile(m_st, "all covered" if not missing else f"{len(missing)} gaps",
                                         f"{len(mi.get('covered', []))} types covered")
        severities.extend([g_st, i_st, m_st])
    else:
        for key, label in (("golden", "Golden regression"), ("ipv", "Independent price verification"),
                           ("model_inventory", "Model inventory coverage")):
            tiles[key] = _tile("not_run", "not run", f"{label} — run the governance runner")

    order = {"red": 3, "amber": 2, "green": 1, "unknown": 0}
    overall = max(severities, key=lambda s: order[s]) if severities else "unknown"
    return {"valuation_date": valuation_date or (latest["date"] if latest else None),
            "overall": overall, "tiles": tiles}
