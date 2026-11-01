"""Portfolio / trade-economics versioning into the governed store (closes the P1c deferral).

The portfolio is the position set. Like market data (P0b), it needs an immutable,
content-addressed version so a run is reproducible and auditable: which exact positions
produced this PV / risk number? `compute_version` content-addresses the trade economics;
`PortfolioStore` persists each distinct version's full content (immutable, append-only) plus
a queryable manifest, and the runner stamps `portfolio_version` into provenance + the run
manifest. The source-of-truth `portfolio.json` stays change-controlled in git; this store is
the runtime audit trail of what was actually priced, and lets a past version be replayed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from src.common import storage

_MANIFEST_SCHEMA = pa.schema([
    ("portfolio_version", pa.string()),
    ("created_at", pa.string()),
    ("n_trades", pa.int64()),
    ("name", pa.string()),
    ("default_valuation_date", pa.string()),
    ("source", pa.string()),
])


def compute_version(raw: dict[str, Any]) -> str:
    """Content-address the trade economics (order-independent, 16-hex).

    Hashes only the `trades` list (the economic content), so documentation edits
    (description/assumptions) don't churn the version, but any change to a position —
    added/removed trade, edited notional/strike/params — yields a new version.
    """
    trades = raw.get("trades", [])
    blob = json.dumps(trades, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class PortfolioStore:
    def __init__(self, project_root: Path):
        base = storage.join(storage.data_base(project_root), "pricer", "store", "portfolio")
        self.versions_dir = storage.join(base, "versions")
        self.manifest_dir = storage.join(base, "manifest")

    def register(self, raw: dict[str, Any]) -> str:
        """Compute the version and persist it (content + manifest row) if new. Idempotent."""
        version = compute_version(raw)
        content = storage.join(self.versions_dir, f"{version}.json")
        if not storage.exists(content):
            storage.write_text_atomic(
                content, json.dumps({"portfolio_version": version, **raw}, indent=2))
            self._append_manifest(version, raw)
        return version

    def load_version(self, version: str) -> dict[str, Any]:
        path = storage.join(self.versions_dir, f"{version}.json")
        if not storage.exists(path):
            raise KeyError(f"portfolio version not found: {version}")
        return dict(json.loads(storage.read_text(path)))

    def versions(self, limit: int = 50) -> pd.DataFrame:
        sql = f"SELECT * FROM @SRC ORDER BY created_at DESC LIMIT {int(limit)}"
        return storage.query(
            sql, self.manifest_dir, recursive=False, read_parquet_extra=", union_by_name=true")

    # -- internals ----------------------------------------------------------- #
    def _append_manifest(self, version: str, raw: dict[str, Any]) -> None:
        record = {
            "portfolio_version": version,
            "created_at": datetime.now(UTC).isoformat(),
            "n_trades": len(raw.get("trades", [])),
            "name": str(raw.get("name", "")),
            "default_valuation_date": str(raw.get("default_valuation_date", "")),
            "source": "portfolio.json",
        }
        storage.write_dataset(
            pa.Table.from_pylist([record], schema=_MANIFEST_SCHEMA),
            self.manifest_dir, partition_cols=[],
            basename_template=f"{version}-{{i}}.parquet",
        )
