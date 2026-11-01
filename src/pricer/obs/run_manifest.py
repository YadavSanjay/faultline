"""Per-run audit manifest (P3).

One row per run — who/what/when/which snapshot/code, pass-fail counts, total PV,
duration, and drift-alert count — appended to a Hive-partitioned Parquet dataset
(`data/pricer/store/runs/`) and queryable via DuckDB. This is the operational audit
trail: "did EOD complete, on which snapshot, how long, with what failures?".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from src.common import storage

_SCHEMA = pa.schema([
    ("valuation_date", pa.string()),
    ("run_id", pa.string()),
    ("valuation_type", pa.string()),
    ("snapshot_id", pa.string()),
    ("portfolio_version", pa.string()),
    ("git_sha", pa.string()),
    ("quantlib_version", pa.string()),
    ("started_at", pa.string()),
    ("finished_at", pa.string()),
    ("duration_s", pa.float64()),
    ("n_trades", pa.int64()),
    ("n_ok", pa.int64()),
    ("n_blocked", pa.int64()),
    ("n_failed", pa.int64()),
    ("n_warnings", pa.int64()),
    ("n_drift_alerts", pa.int64()),
    ("total_pv", pa.float64()),
    ("status", pa.string()),
])


@dataclass
class RunManifest:
    valuation_date: str
    run_id: str
    valuation_type: str
    snapshot_id: str
    portfolio_version: str
    git_sha: str
    quantlib_version: str
    started_at: str
    finished_at: str
    duration_s: float
    n_trades: int
    n_ok: int
    n_blocked: int
    n_failed: int
    n_warnings: int
    n_drift_alerts: int
    total_pv: float
    status: str

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


class RunManifestStore:
    def __init__(self, project_root: Path):
        self.dataset_uri = storage.join(
            storage.data_base(project_root), "pricer", "store", "runs")

    def write(self, manifest: RunManifest) -> str:
        table = pa.Table.from_pylist([manifest.as_record()], schema=_SCHEMA)
        storage.write_dataset(
            table, self.dataset_uri, partition_cols=["valuation_date"],
            basename_template=f"{manifest.run_id}-{{i}}.parquet",
        )
        return self.dataset_uri

    def recent(self, limit: int = 20) -> pd.DataFrame:
        # union_by_name tolerates schema evolution (older manifests predate portfolio_version).
        sql = f"SELECT * FROM @SRC ORDER BY finished_at DESC LIMIT {int(limit)}"
        return storage.query(
            sql, self.dataset_uri,
            read_parquet_extra=", hive_partitioning=true, hive_types_autocast=0, union_by_name=true")
