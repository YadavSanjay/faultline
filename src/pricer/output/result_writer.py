"""Persist pricing results.

Layout (per the design):
    data/pricer/<valuation_type>/<asset_class>/<trade_id>/<valuation_date>.json
    data/pricer/<valuation_type>/summary_<valuation_date>.csv   (run-level)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.common import storage

from ..ql.common.pricing_result import PricingResult

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _ret(location: str) -> Path | str:
    """Return a ``Path`` in local mode (callers/tests use Path methods) or the URI when remote."""
    return location if storage.is_remote_uri(location) else Path(location)


def result_dir(root: Path, valuation_type: str, asset_class: str, trade_id: str) -> str:
    """Location (local path or s3:// uri) of a trade's result directory; created if local."""
    path = storage.join(
        storage.data_base(root), "pricer", valuation_type, asset_class, trade_id)
    storage.makedirs(path)
    return path


def write_result(
    result: PricingResult,
    asset_class: str,
    valuation_type: str,
    root: Path,
) -> Path | str:
    d = result_dir(root, valuation_type, asset_class, result.trade_id)
    out = storage.join(d, f"{result.valuation_date.isoformat()}.json")
    payload = result.to_dict()
    payload["asset_class"] = asset_class
    payload["valuation_type"] = valuation_type
    storage.write_text_atomic(out, json.dumps(payload, indent=2, default=str))
    logger.info("Wrote result -> %s", out)
    return _ret(out)


def write_summary(
    rows: list[dict],
    valuation_type: str,
    valuation_date: date,
    root: Path,
    run_id: str | None = None,
    snapshot_id: str | None = None,
) -> Path | str:
    import csv
    import io

    base = storage.join(storage.data_base(root), "pricer", valuation_type)
    storage.makedirs(base)
    out = storage.join(base, f"summary_{valuation_date.isoformat()}.csv")
    fields = ["run_id", "snapshot_id", "trade_id", "asset_class", "trade_type",
              "currency", "pv", "n_warnings", "status"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for r in rows:
        row = {k: r.get(k, "") for k in fields}
        row["run_id"] = run_id or ""
        row["snapshot_id"] = snapshot_id or ""
        w.writerow(row)
    storage.write_text_atomic(out, buf.getvalue())
    logger.info("Wrote summary -> %s", out)
    return _ret(out)


def write_quality_report(
    rows: list[dict],
    valuation_type: str,
    valuation_date: date,
    root: Path,
    *,
    run_id: str | None = None,
    snapshot_id: str | None = None,
    policy: dict | None = None,
) -> Path | str:
    """Per-run data-quality report (P0c): per-trade warnings + block/fail status.

    Aggregates the staleness/contract warnings each trade accumulated and the
    governed-gate outcomes (ok / warnings / BLOCKED / FAILED) into one auditable JSON.
    """
    base = storage.join(storage.data_base(root), "pricer", valuation_type)
    storage.makedirs(base)
    out = storage.join(base, f"quality_{valuation_date.isoformat()}.json")

    def _status_kind(status: str) -> str:
        if status == "ok":
            return "ok"
        return "blocked" if str(status).startswith("BLOCKED") else "failed"

    trades: list[dict[str, Any]] = [
        {
            "trade_id": r.get("trade_id"),
            "asset_class": r.get("asset_class"),
            "status": r.get("status"),
            "n_warnings": len(r.get("warnings", []) or []),
            "warnings": r.get("warnings", []) or [],
        }
        for r in rows
    ]
    kinds = [_status_kind(r.get("status", "")) for r in rows]
    report = {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "valuation_date": valuation_date.isoformat(),
        "valuation_type": valuation_type,
        "generated_at": _utc_now(),
        "policy": policy or {},
        "summary": {
            "n_trades": len(rows),
            "n_ok": kinds.count("ok"),
            "n_blocked": kinds.count("blocked"),
            "n_failed": kinds.count("failed"),
            "n_with_warnings": sum(1 for t in trades if t["n_warnings"] > 0),
        },
        "trades": trades,
    }
    storage.write_text_atomic(out, json.dumps(report, indent=2, default=str))
    logger.info("Wrote data-quality report -> %s", out)
    return _ret(out)
