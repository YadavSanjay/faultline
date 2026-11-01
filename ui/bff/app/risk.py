"""Risk reads for the UI (Phase 2) — all from the stored RiskReport JSON (V1 stored-only).

The risk batch writes one report per run to ``data/pricer/risk/<date>/risk_<run_id>.json``
with everything the UI needs: per-trade sensitivities, aggregated book sensitivities, named
scenarios, sensitivity-based VaR/ES (incl. the ``pnl_vector`` persisted by ENG-2 for the
histogram), optional full-reval VaR, P&L-explain, backtest, and limit breaches. The BFF
serves the latest report for a date and a histogram-focused slice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pricing import _portfolio_index
from .settings import data_dir, project_root


def _risk_dir() -> Path:
    return data_dir() / "pricer" / "risk"


def _limits_config() -> dict[str, Any]:
    """The book's configured risk limits (``risk.limits`` in pricer_config)."""
    path = project_root() / "configs" / "pricer" / "pricer_config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    limits = cfg.get("risk", {}).get("limits", {}) or {}
    return {k: v for k, v in limits.items() if not k.startswith("_")}


def available_dates() -> list[str]:
    base = _risk_dir()
    if not base.exists():
        return []
    dates = [d.name for d in base.iterdir() if d.is_dir() and any(d.glob("risk_*.json"))]
    return sorted(dates, reverse=True)


def _latest_report_path(valuation_date: str) -> Path | None:
    d = _risk_dir() / valuation_date
    if not d.exists():
        return None
    files = sorted(d.glob("risk_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _load(valuation_date: str | None) -> tuple[str | None, dict[str, Any] | None]:
    dates = available_dates()
    if not dates:
        return None, None
    vdate = valuation_date if valuation_date in dates else dates[0]
    path = _latest_report_path(vdate)
    if path is None:
        return vdate, None
    try:
        return vdate, json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return vdate, None


def report(valuation_date: str | None) -> dict[str, Any]:
    """Full risk report for RK-B1/B2/B4/B5/B6, enriched with trade descriptions + dates."""
    vdate, rep = _load(valuation_date)
    if rep is None:
        return {"available": False, "valuation_date": vdate, "available_dates": available_dates()}

    pf = _portfolio_index()
    for s in rep.get("sensitivities", []) or []:
        meta = pf.get(s.get("trade_id"), {})
        s["description"] = meta.get("description", "")
    rep["available"] = True
    rep["available_dates"] = available_dates()
    return rep


def limits(valuation_date: str | None) -> dict[str, Any]:
    """Full limit-utilisation view (RK-B6): every configured limit with its observed value,
    ceiling, utilisation and headroom — breached or not — so the control shows headroom even
    when the book is within mandate. Observed values mirror ``src/pricer/risk/limits.py``
    (VaR loss per level; abs aggregate book sensitivity, summing per-bucket keys).
    """
    vdate, rep = _load(valuation_date)
    if rep is None:
        return {"available": False, "valuation_date": vdate, "available_dates": available_dates()}

    cfg = _limits_config()
    var_block = (rep.get("var") or {}).get("var", {})
    book_sens: dict[str, float] = rep.get("book_sensitivities", {}) or {}
    rows: list[dict[str, Any]] = []

    for lvl, ceiling in (cfg.get("var", {}) or {}).items():
        observed = var_block.get(str(lvl))
        if observed is None:
            continue
        rows.append(_limit_row(f"VaR[{lvl}]", "var", str(lvl), float(observed), float(ceiling)))

    for factor, ceiling in (cfg.get("sensitivities", {}) or {}).items():
        if factor in book_sens:
            observed = abs(float(book_sens[factor]))
        else:
            observed = abs(sum(float(v) for k, v in book_sens.items() if k.startswith(f"{factor}:")))
        rows.append(_limit_row(factor, "sensitivity", factor, observed, float(ceiling)))

    rows.sort(key=lambda r: r["utilization"], reverse=True)
    n_breach = sum(1 for r in rows if r["breached"])
    worst = rows[0]["utilization"] if rows else 0.0
    return {
        "available": True,
        "valuation_date": rep.get("valuation_date", vdate),
        "available_dates": available_dates(),
        "run_id": rep.get("run_id"),
        "snapshot_id": rep.get("snapshot_id"),
        "n_limits": len(rows),
        "n_breaches": n_breach,
        "max_utilization": worst,
        "limits": rows,
    }


def _limit_row(name: str, kind: str, key: str, observed: float, ceiling: float) -> dict[str, Any]:
    util = (observed / ceiling) if ceiling else 0.0
    return {
        "name": name,
        "kind": kind,
        "key": key,        # config key: VaR confidence level, or sensitivity factor name
        "observed": observed,
        "limit": ceiling,
        "utilization": util,
        "headroom": ceiling - observed,
        "breached": observed > ceiling,
    }


_KIND_BUCKET = {"var": "var", "sensitivity": "sensitivities"}


def update_limit(kind: str, key: str, ceiling: float) -> dict[str, Any]:
    """Gated write: set the ceiling of an existing risk limit in ``pricer_config.json``
    (``risk.limits.<bucket>.<key>``). Only edits keys that already exist, to avoid a typo
    creating a junk limit. Returns the old/new ceiling for the audit record."""
    bucket = _KIND_BUCKET.get(kind)
    if bucket is None:
        return {"ok": False, "error": f"kind must be 'var' or 'sensitivity', got {kind!r}"}
    try:
        ceiling_f = float(ceiling)
    except (TypeError, ValueError):
        return {"ok": False, "error": "ceiling must be a number"}
    if ceiling_f <= 0:
        return {"ok": False, "error": "ceiling must be positive"}

    path = project_root() / "configs" / "pricer" / "pricer_config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"ok": False, "error": f"could not read config: {exc}"}

    limits = cfg.setdefault("risk", {}).setdefault("limits", {})
    section = limits.get(bucket)
    if not isinstance(section, dict) or key not in section:
        return {"ok": False, "error": f"no existing {kind} limit '{key}' to edit"}

    old = float(section[key])
    section[key] = ceiling_f
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"ok": True, "kind": kind, "key": key, "old_ceiling": old, "new_ceiling": ceiling_f}


def var_construction(valuation_date: str | None) -> dict[str, Any]:
    """Histogram-focused slice (RK-B3b): the daily P&L vector + VaR/ES cut-lines + factors."""
    _vdate, rep = _load(valuation_date)
    var = (rep or {}).get("var") if rep else None
    if not var:
        return {"available": False, "reason": "no sensitivity-VaR in the latest risk run"}
    return {
        "available": True,
        "valuation_date": var.get("valuation_date"),
        "lookback_days": var.get("lookback_days"),
        "horizon_days": var.get("horizon_days"),
        "n_scenarios": var.get("n_scenarios"),
        "pnl_vector": var.get("pnl_vector", []),
        "var": var.get("var", {}),
        "es": var.get("es", {}),
        "factor_stdev": var.get("factor_stdev", {}),
        "book_sensitivities": var.get("book_sensitivities", {}),
        "missing_factors": var.get("missing_factors", []),
        "decomposition": var.get("decomposition", {}),
        "run_id": (rep or {}).get("run_id"),
        "snapshot_id": (rep or {}).get("snapshot_id"),
        "factor_snapshot_id": (rep or {}).get("factor_snapshot_id", ""),
    }
