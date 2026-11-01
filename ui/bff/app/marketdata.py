"""Market-data reads for the UI (Phase 3) — a greenfield read layer over the files.

Market data is library-only (no service), so the BFF reads directly from disk:
feeds are the CSVs under ``data/marketdata/<asset_class>/``; each is enriched with its
source (from ``configs/marketdata/<asset>/*.json``), file provenance (sha256/size/mtime
from the latest snapshot manifest where present, else the file itself), a data-quality
verdict (from ``configs/marketdata/dq_gate.json``), and its real consumption — which
pricing/risk path reads it and the actual trades it prices (``md_consumption``), which
replaces the old asset-class-level trade count. A feed's DQ verdict of ``ungated`` means
no DQ rule is declared (nothing consumes it), NOT that it is unusable.
"""

from __future__ import annotations

import datetime as _dt
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb

from .md_consumption import feed_consumption
from .pricing import _portfolio_index
from .settings import data_dir, project_root

_DATE_NAMES = {"date", "as_of_date", "observation_date", "trade_date", "time", "datetime"}


def _md_dir() -> Path:
    return data_dir() / "marketdata"


# --- source index (filename -> source metadata) ---------------------------- #
@lru_cache(maxsize=1)
def _source_index() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    cfg_dir = project_root() / "configs" / "marketdata"
    for cfg_path in cfg_dir.glob("*/*.json"):
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        asset = cfg.get("asset_class", cfg_path.parent.name)
        for key, src in (cfg.get("sources", {}) or {}).items():
            if not isinstance(src, dict):
                continue
            files: list[str] = []
            if isinstance(src.get("output_file"), str):
                files.append(src["output_file"])
            of = src.get("output_files")
            if isinstance(of, dict):
                files.extend(str(v) for v in of.values() if isinstance(v, str))
            elif isinstance(of, list):
                files.extend(str(v) for v in of if isinstance(v, str))
            meta = {
                "source_key": key,
                "name": src.get("name", key),
                "base_url": src.get("base_url", ""),
                "auth": (src.get("auth", {}) or {}).get("type", ""),
                "timeout_s": src.get("timeout_s"),
                "rate_limit_delay_s": src.get("rate_limit_delay_s"),
                "retry": src.get("retry", {}),
                "description": src.get("description", ""),
                "enabled": src.get("enabled", True),
                "asset_class": asset,
            }
            for f in files:
                out[Path(f).name] = meta
    return out


@lru_cache(maxsize=1)
def _dq_config() -> dict[str, Any]:
    path = project_root() / "configs" / "marketdata" / "dq_gate.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _snapshot_index() -> dict[str, dict[str, Any]]:
    """Latest pricer market-data snapshot manifest -> {relpath: {sha256, size, mtime}}."""
    snap_dir = data_dir() / "pricer" / "snapshots"
    if not snap_dir.exists():
        return {}
    files = sorted(snap_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {}
    try:
        manifest = json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {f["path"]: f for f in manifest.get("files", [])}


def _csv_stats(path: Path) -> dict[str, Any]:
    key = (str(path), path.stat().st_mtime, path.stat().st_size)
    return _csv_stats_cached(key)


@lru_cache(maxsize=256)
def _csv_stats_cached(key: tuple[str, float, int]) -> dict[str, Any]:
    path = Path(key[0])
    con = duckdb.connect()
    try:
        cols_df = con.execute(
            f"DESCRIBE SELECT * FROM read_csv_auto('{path.as_posix()}', SAMPLE_SIZE=200)"
        ).df()
        columns = list(cols_df["column_name"])
        date_col = next((c for c in columns if c.lower() in _DATE_NAMES), None)
        if date_col is None:
            date_col = next((c for c in columns if "date" in c.lower()), None)
        select = "count(*) AS n"
        if date_col:
            select += f', min("{date_col}") AS dmin, max("{date_col}") AS dmax'
        row = con.execute(
            f"SELECT {select} FROM read_csv_auto('{path.as_posix()}')"
        ).fetchone()
    except Exception:
        return {"row_count": None, "columns": [], "date_col": None, "date_min": None, "date_max": None}
    finally:
        con.close()
    n = int(row[0]) if row and row[0] is not None else None
    dmin = str(row[1]) if (date_col and len(row) > 1 and row[1] is not None) else None
    dmax = str(row[2]) if (date_col and len(row) > 2 and row[2] is not None) else None
    return {"row_count": n, "columns": columns, "date_col": date_col,
            "date_min": dmin, "date_max": dmax}


def _staleness_days(date_max: str | None) -> int | None:
    if not date_max:
        return None
    try:
        d = _dt.date.fromisoformat(date_max[:10])
    except Exception:
        return None
    return (_dt.date.today() - d).days


def _dq_for(feed_id: str, stats: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Compute a DQ verdict + per-check results for a feed declared in the gate config."""
    cfg = _dq_config()
    files = cfg.get("files", {})
    if feed_id not in files:
        return "ungated", []
    rule = files[feed_id] or {}
    max_stale = rule.get("max_staleness_days", cfg.get("max_staleness_days"))
    required = rule.get("required_columns", [])
    min_rows = rule.get("min_rows", cfg.get("min_rows", 1))
    checks: list[dict[str, str]] = []

    n = stats.get("row_count")
    if n is None:
        checks.append({"check": "readable", "severity": "block", "message": "could not parse CSV"})
    elif n < int(min_rows):
        checks.append({"check": "min_rows", "severity": "block", "message": f"{n} rows < {min_rows}"})
    else:
        checks.append({"check": "min_rows", "severity": "ok", "message": f"{n} rows"})

    cols = {c.lower() for c in stats.get("columns", [])}
    missing = [c for c in required if c.lower() not in cols]
    if required:
        checks.append({
            "check": "required_columns",
            "severity": "block" if missing else "ok",
            "message": f"missing {missing}" if missing else "all present",
        })

    stale = _staleness_days(stats.get("date_max"))
    if max_stale is not None and stale is not None:
        sev = "warn" if stale > int(max_stale) else "ok"
        checks.append({"check": "staleness", "severity": sev,
                       "message": f"{stale}d old (max {max_stale}d)"})

    order = {"block": 3, "warn": 2, "ok": 1, "ungated": 0}
    worst = max((c["severity"] for c in checks), key=lambda s: order.get(s, 0), default="ok")
    return worst, checks


@lru_cache(maxsize=1)
def _trades_by_asset() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for tid, meta in _portfolio_index().items():
        ac = meta.get("asset_class", "")
        out.setdefault(ac, []).append({"trade_id": tid, "description": meta.get("description", "")})
    return out


def _iter_csvs() -> list[Path]:
    base = _md_dir()
    if not base.exists():
        return []
    return [p for p in base.rglob("*.csv") if "provenance" not in p.name]


def _feed_id(path: Path) -> str:
    return path.relative_to(_md_dir()).as_posix()


# --- public API ------------------------------------------------------------ #
def list_feeds() -> dict[str, Any]:
    feeds: list[dict[str, Any]] = []
    src_idx = _source_index()
    trades_by_ac = _trades_by_asset()
    portfolio = _portfolio_index()
    for path in sorted(_iter_csvs()):
        fid = _feed_id(path)
        asset = fid.split("/")[0]
        fname = path.name
        src = src_idx.get(fname)
        stats = _csv_stats(path)
        dq, _ = _dq_for(fid, stats)
        cons = feed_consumption(fid, portfolio)
        feeds.append({
            "id": fid,
            "asset_class": asset,
            "file": fname,
            "name": (src or {}).get("name") or fname.replace(".csv", "").replace("_", " "),
            "source": (src or {}).get("source_key", "—"),
            "enabled": (src or {}).get("enabled", True),
            "row_count": stats.get("row_count"),
            "date_min": stats.get("date_min"),
            "date_max": stats.get("date_max"),
            "staleness_days": _staleness_days(stats.get("date_max")),
            "dq": dq,
            # real feed->trade consumption (replaces the asset-class-level count)
            "consumed_by": cons["consumed_by"],
            "category": cons["category"],
            "role": cons["role"],
            "n_pv_trades": cons["n_pv_trades"],
            # legacy asset-class-level count, kept for back-compat (coarse; do not
            # read as real consumption — see consumed_by / n_pv_trades)
            "n_consumers": len(trades_by_ac.get(asset, [])),
            "updated": _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.UTC).isoformat(),
        })
    classes = sorted({f["asset_class"] for f in feeds})
    return {"feeds": feeds, "asset_classes": classes, "count": len(feeds)}


def feed_detail(feed_id: str) -> dict[str, Any] | None:
    path = _md_dir() / feed_id
    if not path.exists() or path.suffix != ".csv":
        return None
    asset = feed_id.split("/")[0]
    src = _source_index().get(path.name)
    stats = _csv_stats(path)
    dq, checks = _dq_for(feed_id, stats)
    snap = _snapshot_index().get(feed_id)
    return {
        "id": feed_id,
        "asset_class": asset,
        "file": path.name,
        "name": (src or {}).get("name") or path.name,
        "source": src or {"source_key": "—", "name": path.name},
        "provenance": {
            "sha256": (snap or {}).get("sha256"),
            "size_bytes": (snap or {}).get("size_bytes") or path.stat().st_size,
            "mtime": (snap or {}).get("mtime")
            or _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.UTC).isoformat(),
            "in_snapshot": snap is not None,
            "row_count": stats.get("row_count"),
            "columns": stats.get("columns"),
            "date_min": stats.get("date_min"),
            "date_max": stats.get("date_max"),
            "staleness_days": _staleness_days(stats.get("date_max")),
        },
        "dq": {"verdict": dq, "checks": checks, "gated": dq != "ungated",
               "note": "'ungated' means no DQ rule is declared for this feed (no "
               "pricing/risk path reads it) — not that it is unusable."},
        # real consumption: what actually reads this feed, and the trades it prices
        "consumption": feed_consumption(feed_id, _portfolio_index()),
        # legacy asset-class-level linkage (coarse); kept for back-compat
        "consumers": _trades_by_asset().get(asset, []),
    }


def feed_series(feed_id: str, limit: int = 500) -> dict[str, Any] | None:
    path = _md_dir() / feed_id
    if not path.exists() or path.suffix != ".csv":
        return None
    stats = _csv_stats(path)
    cols = stats.get("columns") or []
    con = duckdb.connect()
    try:
        # downsample large series so the payload stays small
        n = stats.get("row_count") or 0
        step = max(1, n // limit)
        rows = con.execute(
            f"SELECT * FROM (SELECT *, row_number() OVER () AS _rn "
            f"FROM read_csv_auto('{path.as_posix()}')) WHERE _rn % {step} = 0 LIMIT {limit}"
        ).df()
    except Exception:
        return {"id": feed_id, "columns": cols, "date_col": stats.get("date_col"), "rows": []}
    finally:
        con.close()
    rows = rows.drop(columns=[c for c in rows.columns if c == "_rn"], errors="ignore")
    records = json.loads(rows.to_json(orient="records", date_format="iso"))
    numeric = [c for c in rows.columns if str(rows[c].dtype).startswith(("int", "float"))]
    return {"id": feed_id, "columns": list(rows.columns), "date_col": stats.get("date_col"),
            "numeric_columns": numeric, "rows": records}


def feed_rows(
    feed_id: str,
    page: int = 1,
    page_size: int = 50,
    filters: dict[str, str] | None = None,
    sort_col: str | None = None,
    sort_dir: str = "asc",
) -> dict[str, Any] | None:
    """Raw feed rows for the data grid (MD-B6): server-side per-column filter,
    sort, and pagination via DuckDB — works for any CSV regardless of size/schema.

    Filters are case-insensitive substring matches on the string form of each
    column (so numeric/date columns filter too). Column names are whitelisted
    against the file's real header (no injection); filter *values* are bound
    parameters. Returns the page of rows plus the filtered total for pagination.
    """
    path = _md_dir() / feed_id
    if not path.exists() or path.suffix != ".csv":
        return None

    stats = _csv_stats(path)
    cols: list[str] = stats.get("columns") or []
    page = max(1, int(page))
    page_size = min(max(int(page_size), 5), 200)

    # WHERE from whitelisted columns + bound values
    clauses: list[str] = []
    params: list[str] = []
    for col, val in (filters or {}).items():
        if col in cols and str(val).strip():
            clauses.append(f'CAST("{col}" AS VARCHAR) ILIKE ?')
            params.append(f"%{val.strip()}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    order = ""
    if sort_col in cols:
        direction = "DESC" if str(sort_dir).lower() == "desc" else "ASC"
        order = f' ORDER BY "{sort_col}" {direction}'

    src = f"read_csv_auto('{path.as_posix()}')"
    offset = (page - 1) * page_size
    con = duckdb.connect()
    try:
        total = con.execute(f"SELECT count(*) FROM {src}{where}", params).fetchone()[0]
        df = con.execute(
            f"SELECT * FROM {src}{where}{order} LIMIT {page_size} OFFSET {offset}", params
        ).df()
    except Exception as exc:  # malformed filter/sort -> empty page, not a 500
        return {"id": feed_id, "columns": cols, "rows": [], "total": 0,
                "page": page, "page_size": page_size, "error": str(exc)}
    finally:
        con.close()

    records = json.loads(df.to_json(orient="records", date_format="iso"))
    total = int(total or 0)
    return {
        "id": feed_id,
        "columns": cols,
        "rows": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "n_pages": max(1, (total + page_size - 1) // page_size),
        "filtered": bool(clauses),
    }


def coverage() -> dict[str, Any]:
    """Asset-class-level feed↔trade linkage (a trade depends on its asset class's feeds)."""
    feeds = list_feeds()["feeds"]
    by_ac: dict[str, list[str]] = {}
    for f in feeds:
        by_ac.setdefault(f["asset_class"], []).append(f["id"])
    trades = [
        {"trade_id": tid, "description": m.get("description", ""), "asset_class": m.get("asset_class", "")}
        for tid, m in _portfolio_index().items()
    ]
    trades.sort(key=lambda t: int(t["trade_id"].split("_")[-1]) if t["trade_id"].split("_")[-1].isdigit() else 0)
    return {"trades": trades, "feeds_by_asset_class": by_ac, "granularity": "asset_class"}


_DQ_ORDER = {"block": 3, "warn": 2, "ungated": 1, "ok": 0}


def overview() -> dict[str, Any]:
    """All-feeds rollup for the Market Data landing (MD overview): freshness, DQ verdict,
    and real consumption summarised platform-wide and per asset class, plus a compact
    per-feed array for the freshness/DQ heatmap. Built from ``list_feeds`` (no new I/O
    beyond it), so it stays consistent with the catalog.
    """
    feeds = list_feeds()["feeds"]
    dq_counts: dict[str, int] = {"ok": 0, "warn": 0, "block": 0, "ungated": 0}
    cons_counts: dict[str, int] = {"both": 0, "pv": 0, "risk": 0, "none": 0}
    stale_cutoff = _dq_config().get("max_staleness_days")
    n_stale = 0
    max_stale = 0
    by_ac: dict[str, dict[str, Any]] = {}
    compact: list[dict[str, Any]] = []

    for f in feeds:
        dq = str(f.get("dq", "ungated"))
        dq_counts[dq] = dq_counts.get(dq, 0) + 1
        bucket = str(f.get("consumed_by", "none"))
        cons_counts[bucket] = cons_counts.get(bucket, 0) + 1
        sd = f.get("staleness_days")
        is_stale = bool(stale_cutoff is not None and sd is not None and sd > int(stale_cutoff))
        if is_stale:
            n_stale += 1
        if isinstance(sd, int):
            max_stale = max(max_stale, sd)

        ac = str(f.get("asset_class", ""))
        row = by_ac.setdefault(ac, {"asset_class": ac, "n_feeds": 0, "n_consumed": 0,
                                    "n_stale": 0, "worst_dq": "ok"})
        row["n_feeds"] += 1
        if bucket != "none":
            row["n_consumed"] += 1
        if is_stale:
            row["n_stale"] += 1
        if _DQ_ORDER.get(dq, 0) > _DQ_ORDER.get(row["worst_dq"], 0):
            row["worst_dq"] = dq

        compact.append({
            "id": f["id"], "asset_class": ac, "name": f.get("name"),
            "dq": dq, "staleness_days": sd, "date_max": f.get("date_max"),
            "consumed_by": bucket, "category": f.get("category"),
            "role": f.get("role"), "n_pv_trades": f.get("n_pv_trades", 0), "stale": is_stale,
        })

    n_consumed = cons_counts["both"] + cons_counts["pv"] + cons_counts["risk"]
    return {
        "n_feeds": len(feeds),
        "n_asset_classes": len({f["asset_class"] for f in feeds}),
        "n_consumed": n_consumed,
        "n_unused": cons_counts["none"],
        "n_stale": n_stale,
        "max_staleness_days": max_stale,
        "stale_cutoff_days": stale_cutoff,
        "dq_counts": dq_counts,
        "consumption_counts": cons_counts,
        "by_asset_class": sorted(by_ac.values(), key=lambda r: r["asset_class"]),
        "feeds": compact,
    }


def dependency() -> dict[str, Any]:
    """Feed→trade dependency matrix (MD-B4 precision upgrade over asset-class coverage):
    for every consumed feed, the exact trades that read it — and the reverse index, the
    feeds each trade depends on. Answers "which trades break if this feed goes stale?".

    Risk-only feeds are book-level VaR factors with no single-trade owner, so their
    ``trades`` list is empty and they are tagged ``risk_factor``.
    """
    portfolio = _portfolio_index()
    src_idx = _source_index()
    feed_rows: list[dict[str, Any]] = []
    trade_ix: dict[str, dict[str, Any]] = {}

    for path in sorted(_iter_csvs()):
        fid = _feed_id(path)
        cons = feed_consumption(fid, portfolio)
        if cons["consumed_by"] == "none":
            continue
        stats = _csv_stats(path)
        dq, _ = _dq_for(fid, stats)
        trades = cons["pv_trades"]
        feed_rows.append({
            "id": fid,
            "asset_class": fid.split("/")[0],
            "name": (src_idx.get(path.name) or {}).get("name") or path.name,
            "consumed_by": cons["consumed_by"],
            "category": cons["category"],
            "role": cons["role"],
            "dq": dq,
            "staleness_days": _staleness_days(stats.get("date_max")),
            "is_risk_factor": cons["consumed_by"] == "risk",
            "trades": trades,
            "n_trades": len(trades),
        })
        for t in trades:
            entry = trade_ix.setdefault(
                t["trade_id"],
                {"trade_id": t["trade_id"], "description": t["description"],
                 "asset_class": portfolio.get(t["trade_id"], {}).get("asset_class", ""), "feeds": []},
            )
            entry["feeds"].append(fid)

    trades = sorted(
        trade_ix.values(),
        key=lambda t: int(t["trade_id"].rsplit("_", 1)[-1]) if t["trade_id"].rsplit("_", 1)[-1].isdigit() else 0,
    )
    return {
        "feeds": feed_rows,
        "trades": trades,
        "n_feeds_consumed": len(feed_rows),
        "n_trades": len(trades),
    }


def dq_dashboard() -> dict[str, Any]:
    cfg = _dq_config()
    declared = list(cfg.get("files", {}).keys())
    rows: list[dict[str, Any]] = []
    counts = {"ok": 0, "warn": 0, "block": 0, "missing": 0}
    for fid in declared:
        path = _md_dir() / fid
        if not path.exists():
            rows.append({"id": fid, "verdict": "block", "checks": [
                {"check": "present", "severity": "block", "message": "file not found"}]})
            counts["block"] += 1
            counts["missing"] += 1
            continue
        stats = _csv_stats(path)
        verdict, checks = _dq_for(fid, stats)
        rows.append({"id": fid, "verdict": verdict, "checks": checks,
                     "staleness_days": _staleness_days(stats.get("date_max")),
                     "row_count": stats.get("row_count")})
        counts[verdict] = counts.get(verdict, 0) + 1
    return {"files": rows, "counts": counts, "max_staleness_days": cfg.get("max_staleness_days")}
