"""Columnar results store + DuckDB query layer (P1c).

Every run appends one flat row per trade to a Hive-partitioned Parquet dataset
(`data/pricer/store/results/`, partitioned by valuation_type / valuation_date /
asset_class). The per-trade JSON and run summary stay as human-readable artifacts;
this store is the queryable system-of-record for analytics that loose files can't
answer: PV history of a trade, book PV by asset class, last run's warnings, etc.

Reads go through DuckDB SQL over the Parquet glob (no server, no full load). Multiple
runs on the same valuation date are appended as separate files; queries de-duplicate
to the latest run per (valuation_date, trade_id) by `priced_at`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from src.common import storage

_PARTITION_COLS = ["valuation_type", "valuation_date", "asset_class"]

# Explicit schema so the column types are stable across runs — e.g. `pv` stays
# float64 even for a run where every trade failed (all-null would otherwise infer a
# `null` column that clashes with float partitions at read time).
_SCHEMA = pa.schema([
    ("valuation_type", pa.string()),
    ("valuation_date", pa.string()),
    ("asset_class", pa.string()),
    ("trade_id", pa.string()),
    ("trade_type", pa.string()),
    ("currency", pa.string()),
    ("pv", pa.float64()),
    ("status", pa.string()),
    ("n_warnings", pa.int64()),
    ("run_id", pa.string()),
    ("snapshot_id", pa.string()),
    ("git_sha", pa.string()),
    ("quantlib_version", pa.string()),
    ("priced_at", pa.string()),
    ("measures_json", pa.string()),
])


class ResultStore:
    def __init__(self, project_root: Path):
        # Location string (local path or s3:// uri) — resolved via the dual-mode storage layer.
        # Local (incl. tests passing a temp project_root) reproduces the historical
        # <root>/data/pricer/store/results path exactly; AWS routes at the S3 data root.
        self.dataset_uri = storage.join(
            storage.data_base(project_root), "pricer", "store", "results")

    # -- write --------------------------------------------------------------- #
    def write_run(self, records: list[dict[str, Any]], run_id: str) -> str | None:
        """Append a run's per-trade records to the partitioned Parquet dataset."""
        if not records:
            return None
        table = pa.Table.from_pylist(records, schema=_SCHEMA)
        storage.write_dataset(
            table,
            self.dataset_uri,
            partition_cols=_PARTITION_COLS,
            basename_template=f"{run_id}-{{i}}.parquet",
        )
        return self.dataset_uri

    # -- read ---------------------------------------------------------------- #
    def _query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        """Run SQL over the Parquet dataset; empty store -> empty DataFrame.

        ``read_parquet_extra`` keeps partition columns (valuation_date, ...) as VARCHAR via
        ``hive_types_autocast=0`` so dates round-trip as the ISO strings we wrote, not
        auto-parsed Timestamps.
        """
        return storage.query(sql, self.dataset_uri, params=params)

    def pv_history(self, trade_id: str) -> pd.DataFrame:
        """Latest PV per valuation_date for one trade, oldest first."""
        return self._query(
            """
            SELECT valuation_date, pv, status, run_id, priced_at
            FROM (
                SELECT valuation_date, pv, status, run_id, priced_at,
                       row_number() OVER (PARTITION BY valuation_date ORDER BY priced_at DESC) AS rn
                FROM @SRC WHERE trade_id = ?
            ) WHERE rn = 1
            ORDER BY valuation_date
            """,
            [trade_id],
        )

    def _latest_run_per_trade(self, valuation_date: str) -> str:
        """SQL fragment: each trade's most recent row on `valuation_date`."""
        return (
            "SELECT * FROM (SELECT *, row_number() OVER "
            "(PARTITION BY trade_id ORDER BY priced_at DESC) AS rn "
            "FROM @SRC WHERE valuation_date = ?) WHERE rn = 1"
        )

    def book_pv_by_asset_class(self, valuation_date: str) -> pd.DataFrame:
        """Trade count and total PV per asset class for the latest run on a date."""
        return self._query(
            f"SELECT asset_class, count(*) AS n_trades, "
            f"sum(pv) AS total_pv FROM ({self._latest_run_per_trade(valuation_date)}) "
            f"GROUP BY asset_class ORDER BY asset_class",
            [valuation_date],
        )

    def latest_run(self, valuation_date: str) -> pd.DataFrame:
        """Every trade's latest result on a valuation date."""
        return self._query(
            f"SELECT trade_id, asset_class, trade_type, currency, pv, status, n_warnings, "
            f"run_id, snapshot_id FROM ({self._latest_run_per_trade(valuation_date)}) "
            f"ORDER BY trade_id",
            [valuation_date],
        )

    def trades_with_warnings(self, valuation_date: str) -> pd.DataFrame:
        """Trades that warned, blocked, or failed in the latest run on a date."""
        return self._query(
            f"SELECT trade_id, asset_class, status, n_warnings "
            f"FROM ({self._latest_run_per_trade(valuation_date)}) "
            f"WHERE n_warnings > 0 OR status <> 'ok' ORDER BY trade_id",
            [valuation_date],
        )
