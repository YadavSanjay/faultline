"""Pricing reads for the UI (Phase 1) — all from stored artifacts (V1 stored-only).

Book/run aggregates reuse the platform's tested ``ResultStore`` (DuckDB over the Parquet
results store — no QuantLib import). Per-trade detail is read from the richer per-trade
JSON the runner writes (``data/pricer/PV/<asset_class>/<trade_id>/<date>.json``), enriched
with the trade's portfolio description.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import httpx
from src.pricer.output.result_store import ResultStore

from .settings import data_dir, project_root, service_url

_VALUATION_TYPE = "PV"


def _store() -> ResultStore:
    return ResultStore(project_root())


# --- portfolio index (descriptions / economics) ---------------------------- #
@lru_cache(maxsize=1)
def _portfolio_index() -> dict[str, dict[str, Any]]:
    path = project_root() / "src" / "pricer" / "portfolio" / "portfolio.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for t in raw.get("trades", []):
        out[t["trade_id"]] = {
            "description": t.get("description", ""),
            "asset_class": t.get("asset_class", ""),
            "trade_type": t.get("trade_type", ""),
            "currency": t.get("currency", ""),
            "notional": t.get("notional"),
            "params": t.get("params", {}),
        }
    return out


def _clean(v: Any) -> Any:
    import math

    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        import numpy as np

        if isinstance(v, np.generic):
            return v.item()
    except Exception:
        pass
    return v


def _results_glob() -> str:
    return (data_dir() / "pricer" / "store" / "results" / "**" / "*.parquet").as_posix()


def available_dates() -> list[str]:
    """Distinct PV valuation dates in the store, newest first."""
    if not any((data_dir() / "pricer" / "store" / "results").glob("**/*.parquet")):
        return []
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT valuation_date FROM read_parquet('{_results_glob()}', "
            f"hive_partitioning=true, hive_types_autocast=0) "
            f"WHERE valuation_type = ? ORDER BY valuation_date DESC",
            [_VALUATION_TYPE],
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def _status_kind(status: str) -> str:
    if status == "ok":
        return "ok"
    return "blocked" if str(status).startswith("BLOCKED") else "failed"


def book(valuation_date: str | None) -> dict[str, Any]:
    """Book valuation for a date: totals, by-asset-class, and the per-trade list with
    deltas vs the prior available date."""
    dates = available_dates()
    if not dates:
        return {"valuation_date": None, "available_dates": [], "by_asset_class": [], "trades": []}
    vdate = valuation_date if valuation_date in dates else dates[0]

    store = _store()
    by_ac_df = store.book_pv_by_asset_class(vdate)
    runs_df = store.latest_run(vdate)
    pf = _portfolio_index()

    # prior-date PV per trade for deltas ("today's movers").
    prior = next((d for d in dates if d < vdate), None)
    prior_pv: dict[str, float] = {}
    if prior is not None:
        for r in store.latest_run(prior).to_dict(orient="records"):
            prior_pv[str(r["trade_id"])] = _clean(r.get("pv"))

    trades: list[dict[str, Any]] = []
    counts = {"ok": 0, "blocked": 0, "failed": 0, "warnings": 0}
    for r in runs_df.to_dict(orient="records"):
        tid = str(r["trade_id"])
        pv = _clean(r.get("pv"))
        status = str(r.get("status", ""))
        kind = _status_kind(status)
        counts[kind] = counts.get(kind, 0) + 1
        nw = int(_clean(r.get("n_warnings")) or 0)
        counts["warnings"] += nw
        pp = prior_pv.get(tid)
        trades.append({
            "trade_id": tid,
            "description": pf.get(tid, {}).get("description", ""),
            "asset_class": _clean(r.get("asset_class")),
            "trade_type": _clean(r.get("trade_type")),
            "currency": _clean(r.get("currency")),
            "pv": pv,
            "prior_pv": pp,
            "delta": (pv - pp) if (pv is not None and pp is not None) else None,
            "status": status,
            "status_kind": kind,
            "n_warnings": nw,
            "run_id": _clean(r.get("run_id")),
            "snapshot_id": _clean(r.get("snapshot_id")),
        })

    by_ac = [
        {"asset_class": _clean(r["asset_class"]), "n_trades": int(_clean(r["n_trades"]) or 0),
         "total_pv": _clean(r["total_pv"])}
        for r in by_ac_df.to_dict(orient="records")
    ]
    total_pv = sum((a["total_pv"] or 0.0) for a in by_ac)

    return {
        "valuation_date": vdate,
        "prior_date": prior,
        "available_dates": dates,
        "total_pv": total_pv,
        "by_asset_class": by_ac,
        "counts": counts,
        "trades": trades,
    }


def book_history() -> dict[str, Any]:
    """Book-level & per-asset-class PV time-series across every stored valuation date, with
    period-over-period P&L (the book-scope companion to the single-trade ``pv_history``).

    Reads the results store directly via DuckDB (same Parquet glob the ``book`` view uses),
    so no engine change is needed. For each valuation date we keep each trade's latest run
    (dedupe by ``priced_at``), then aggregate PV overall and by asset class. ``pnl`` is the
    change in total PV vs the prior stored date (null for the first date).
    """
    if not any((data_dir() / "pricer" / "store" / "results").glob("**/*.parquet")):
        return {"dates": [], "asset_classes": [], "total": [], "series": {}}

    latest = (
        "SELECT valuation_date, asset_class, trade_id, pv FROM ("
        "SELECT valuation_date, asset_class, trade_id, pv, "
        "row_number() OVER (PARTITION BY valuation_date, trade_id ORDER BY priced_at DESC) AS rn "
        f"FROM read_parquet('{_results_glob()}', hive_partitioning=true, hive_types_autocast=0) "
        "WHERE valuation_type = ?) WHERE rn = 1"
    )
    con = duckdb.connect()
    try:
        totals = con.execute(
            f"SELECT valuation_date, sum(pv) AS total_pv, count(*) AS n_trades "
            f"FROM ({latest}) GROUP BY valuation_date ORDER BY valuation_date",
            [_VALUATION_TYPE],
        ).fetchall()
        by_ac = con.execute(
            f"SELECT valuation_date, asset_class, sum(pv) AS total_pv "
            f"FROM ({latest}) GROUP BY valuation_date, asset_class",
            [_VALUATION_TYPE],
        ).fetchall()
    finally:
        con.close()

    dates = [str(r[0]) for r in totals]
    total: list[dict[str, Any]] = []
    prior_pv: float | None = None
    for r in totals:
        pv = _clean(r[1])
        total.append({
            "valuation_date": str(r[0]),
            "total_pv": pv,
            "n_trades": int(_clean(r[2]) or 0),
            "pnl": (pv - prior_pv) if (pv is not None and prior_pv is not None) else None,
        })
        prior_pv = pv if pv is not None else prior_pv

    # per-asset-class series aligned to `dates` (null where an asset class is absent on a date)
    date_ix = {d: i for i, d in enumerate(dates)}
    asset_classes = sorted({str(r[1]) for r in by_ac})
    series: dict[str, list[float | None]] = {ac: [None] * len(dates) for ac in asset_classes}
    for r in by_ac:
        series[str(r[1])][date_ix[str(r[0])]] = _clean(r[2])

    return {"dates": dates, "asset_classes": asset_classes, "total": total, "series": series}


def trade_detail(trade_id: str, valuation_date: str | None) -> dict[str, Any] | None:
    """Full per-trade result (pv, leg_pvs, measures, diagnostics, warnings, provenance)."""
    pf = _portfolio_index().get(trade_id)
    if pf is None:
        return None
    dates = available_dates()
    vdate = valuation_date if (valuation_date and valuation_date in dates) else (dates[0] if dates else None)
    if vdate is None:
        return None
    asset_class = pf["asset_class"]
    path = data_dir() / "pricer" / _VALUATION_TYPE / asset_class / trade_id / f"{vdate}.json"
    if not path.exists():
        # fall back to any stored date for this trade
        candidates = sorted((data_dir() / "pricer" / _VALUATION_TYPE / asset_class / trade_id).glob("*.json"))
        if not candidates:
            return None
        path = candidates[-1]
    result = json.loads(path.read_text(encoding="utf-8"))
    result["description"] = pf["description"]
    result["notional"] = pf["notional"]
    result["params"] = pf["params"]
    result["available_dates"] = dates
    return result


# trade types with a lineage explain-pass (mirrors src/pricer/lineage._DISPATCH;
# duplicated here to keep the BFF free of QuantLib imports).
_LINEAGE_SUPPORTED = {
    "ois_swap", "stock", "corp_bond", "ust_bond", "equity_option", "cds",
    "fx_forward", "xccy_swap", "european_swaption", "bermudan_swaption", "cap",
}


def lineage(trade_id: str, valuation_date: str | None) -> dict[str, Any]:
    """Calculation-lineage payload for a trade (PR-B3), read from the stored explain JSON.

    Returns ``{available: false, ...}`` (200) when there's no lineage for the trade — either
    its instrument isn't exposed in V1, or the lineage runner hasn't been run — so the UI can
    show a friendly message rather than a 404.
    """
    pf = _portfolio_index().get(trade_id, {})
    trade_type = pf.get("trade_type", "")
    base = data_dir() / "pricer" / "lineage"

    # pick the date: requested if present, else the newest date dir holding this trade.
    path: Path | None = None
    if valuation_date and (base / valuation_date / f"{trade_id}.json").exists():
        path = base / valuation_date / f"{trade_id}.json"
    elif base.exists():
        for d in sorted((p.name for p in base.iterdir() if p.is_dir()), reverse=True):
            cand = base / d / f"{trade_id}.json"
            if cand.exists():
                path = cand
                break

    if path is None:
        reason = (
            "lineage isn't exposed for this instrument yet (V1 covers swaps and cash equity)"
            if trade_type not in _LINEAGE_SUPPORTED
            else "no lineage generated yet — run `python -m src.pricer.lineage_runner`"
        )
        return {"available": False, "trade_id": trade_id, "trade_type": trade_type,
                "description": pf.get("description", ""), "reason": reason}

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["available"] = True
    payload["description"] = pf.get("description", "")
    payload["notional"] = pf.get("notional")
    return payload


# --- pricing methodology docs (docs/pricing/*) ----------------------------- #
# Per trade_type, the methodology docs to surface (filenames under docs/pricing/methodology,
# without extension), in display order. Foundations is appended for every trade.
_METHODOLOGY_DOCS: dict[str, list[str]] = {
    "stock": ["10-cash-equity"],
    "equity_option": ["11-equity-options", "01-sofr-multicurve"],
    "corp_bond": ["20-bonds", "01-sofr-multicurve"],
    "ust_bond": ["20-bonds"],
    "ois_swap": ["30-rates-swaps", "01-sofr-multicurve"],
    "european_swaption": ["31-swaptions", "01-sofr-multicurve"],
    "bermudan_swaption": ["32-bermudan-swaption", "01-sofr-multicurve"],
    "cap": ["33-caps", "01-sofr-multicurve"],
    "fx_forward": ["40-fx", "01-sofr-multicurve"],
    "xccy_swap": ["40-fx", "01-sofr-multicurve"],
    "cds": ["50-credit-cds", "01-sofr-multicurve"],
}

# Short tab labels for the methodology docs.
_DOC_LABELS: dict[str, str] = {
    "00-foundations": "Foundations",
    "01-sofr-multicurve": "SOFR curve",
    "10-cash-equity": "Cash equity",
    "11-equity-options": "Equity options",
    "20-bonds": "Bonds",
    "30-rates-swaps": "Swaps",
    "31-swaptions": "Swaptions",
    "32-bermudan-swaption": "Bermudan",
    "33-caps": "Caps",
    "40-fx": "FX",
    "50-credit-cds": "Credit / CDS",
}


def _first_heading(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def trade_docs(trade_id: str) -> dict[str, Any] | None:
    """Pricing write-up for a trade (the markdown under ``docs/pricing/``), as ordered
    sections the UI renders in tabs: the per-trade page, the instrument methodology doc(s),
    and the shared Foundations doc. Returns ``{available: false}`` (not 404) when no docs
    exist, so the UI can show a friendly message."""
    pf = _portfolio_index().get(trade_id)
    if pf is None:
        return None
    trade_type = pf.get("trade_type", "")
    base = project_root() / "docs" / "pricing"
    sections: list[dict[str, Any]] = []

    def _add(path: Path, label: str, *, sid: str) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return
        sections.append({"id": sid, "label": label, "title": _first_heading(text), "markdown": text})

    # 1. the per-trade write-up
    _add(base / "trades" / f"{trade_id}.md", "This trade", sid="trade")
    # 2. instrument methodology doc(s)
    for stem in _METHODOLOGY_DOCS.get(trade_type, []):
        _add(base / "methodology" / f"{stem}.md", _DOC_LABELS.get(stem, stem), sid=stem)
    # 3. shared foundations, always last
    _add(base / "methodology" / "00-foundations.md", _DOC_LABELS["00-foundations"], sid="00-foundations")

    return {
        "available": bool(sections),
        "trade_id": trade_id,
        "trade_type": trade_type,
        "description": pf.get("description", ""),
        "reason": "" if sections else "no pricing write-up found under docs/pricing/ for this trade",
        "sections": sections,
    }


def model_inventory() -> dict[str, Any]:
    """Governed model registry (PR-B5) read from config, with portfolio coverage."""
    path = project_root() / "configs" / "pricer" / "model_inventory.json"
    try:
        models = json.loads(path.read_text(encoding="utf-8")).get("models", {})
    except Exception:
        models = {}
    traded = {t.get("trade_type") for t in _portfolio_index().values() if t.get("trade_type")}
    present = set(models)
    covered = sorted(traded & present)
    missing = sorted(traded - present)
    return {
        "n_models": len(models),
        "models": models,
        "coverage": {"covered": covered, "missing": missing, "n_traded": len(traded)},
    }


def live_price(trade_id: str, valuation_date: str | None) -> dict[str, Any]:
    """Live re-price of a trade against the upstream pricer service's in-memory snapshot
    (POST /price) — the fresh value vs the stored batch result. Degrades gracefully when the
    service is down (the normal local case), so the UI can prompt the user to start it."""
    url = f"{service_url()}/price"
    payload: dict[str, Any] = {"trade_id": trade_id, "valuation_type": _VALUATION_TYPE}
    if valuation_date:
        payload["valuation_date"] = valuation_date
    try:
        resp = httpx.post(url, json=payload, timeout=20.0)
    except Exception as exc:  # service not running is the normal local case
        return {"available": False, "service": "down", "url": url, "error": str(exc)}
    if resp.status_code == 404:
        return {"available": False, "service": "up", "error": f"trade not found: {trade_id}"}
    if resp.status_code >= 400:
        return {"available": False, "service": "up",
                "error": f"pricer returned {resp.status_code}: {resp.text[:300]}"}
    try:
        result = resp.json()
    except Exception as exc:
        return {"available": False, "service": "up", "error": f"bad response: {exc}"}
    return {
        "available": True,
        "service": "up",
        "trade_id": trade_id,
        "priced_at": datetime.now(UTC).isoformat(),
        "snapshot_id": (result.get("provenance") or {}).get("market_data_snapshot_id")
        or result.get("snapshot_id"),
        "result": result,
    }


def pv_history(trade_id: str) -> dict[str, Any]:
    df = _store().pv_history(trade_id)
    history = [
        {"valuation_date": _clean(r["valuation_date"]), "pv": _clean(r["pv"]),
         "status": _clean(r["status"]), "run_id": _clean(r["run_id"]),
         "priced_at": _clean(r["priced_at"])}
        for r in df.to_dict(orient="records")
    ]
    return {"trade_id": trade_id, "history": history}
