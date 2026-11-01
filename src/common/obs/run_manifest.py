"""Generic per-run audit manifest — one row per run, for any module.

Each module appends one row (who/what/when, code version, status, free-form counts and
metrics) to a Hive-partitioned Parquet dataset at `data/<module>/store/runs/`, queryable
via DuckDB. A cross-module query unions `data/*/store/runs/` to answer "what ran across the
whole pipeline today, and did it pass?".

The schema is fixed and small; variable per-run detail lives in the JSON `counts_json` /
`metrics_json` / `extra_json` columns, so heterogeneous modules coexist without schema
churn (and DuckDB's `union_by_name` tolerates older rows).

`pricer` keeps its own richer typed manifest; the other three modules use this one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from src.common import storage

_SCHEMA = pa.schema([
    ("module", pa.string()),
    ("run_id", pa.string()),
    ("partition", pa.string()),
    ("started_at", pa.string()),
    ("finished_at", pa.string()),
    ("duration_s", pa.float64()),
    ("status", pa.string()),
    ("git_sha", pa.string()),
    ("config_hash", pa.string()),
    ("counts_json", pa.string()),
    ("metrics_json", pa.string()),
    ("extra_json", pa.string()),
])


@dataclass
class RunManifest:
    module: str
    run_id: str
    started_at: str
    finished_at: str
    duration_s: float
    status: str                                   # ok | partial | failed
    partition: str = ""                           # hive key; defaults to started_at date
    git_sha: str = ""
    config_hash: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "run_id": self.run_id,
            "partition": self.partition or self.started_at[:10],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "status": self.status,
            "git_sha": self.git_sha,
            "config_hash": self.config_hash,
            "counts_json": json.dumps(self.counts, default=str),
            "metrics_json": json.dumps(self.metrics, default=str),
            "extra_json": json.dumps(self.extra, default=str),
        }


class RunManifestStore:
    def __init__(self, project_root: Path, module: str):
        self.module = module
        self.dataset_uri = storage.join(
            storage.data_base(project_root), module, "store", "runs")

    def write(self, manifest: RunManifest) -> str:
        table = pa.Table.from_pylist([manifest.as_record()], schema=_SCHEMA)
        storage.write_dataset(
            table, self.dataset_uri, partition_cols=["partition"],
            basename_template=f"{manifest.run_id}-{{i}}.parquet",
        )
        return self.dataset_uri

    def recent(self, limit: int = 20) -> pd.DataFrame:
        sql = f"SELECT * FROM @SRC ORDER BY finished_at DESC LIMIT {int(limit)}"
        return storage.query(
            sql, self.dataset_uri,
            read_parquet_extra=", hive_partitioning=true, hive_types_autocast=0, union_by_name=true")
