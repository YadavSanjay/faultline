"""Scenario stress dashboards & result store (S5).

Two layers over the per-scenario stress results:

  StressDashboard      in-memory summary for humans/logs: a scenario x severity matrix of
                       P&L and capital, a worst-first ranking, limit breaches, and a
                       markdown rendering.
  ScenarioResultStore  the queryable data layer a real dashboard (Grafana/notebook) reads:
                       one flat row per (scenario, severity) appended to a Hive-partitioned
                       Parquet dataset, queried via DuckDB (mirrors the pricer ResultStore).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from src.common import storage

from .stress import ScenarioStressResult

_SEVERITY_ORDER = {"mild": 0, "adverse": 1, "severe": 2}

_SCHEMA = pa.schema([
    ("valuation_date", pa.string()),
    ("run_id", pa.string()),
    ("generated_at", pa.string()),
    ("scenario", pa.string()),
    ("severity", pa.string()),
    ("scenario_version", pa.string()),
    ("total_pnl", pa.float64()),
    ("capital_delta", pa.float64()),
    ("es_base", pa.float64()),
    ("es_shocked", pa.float64()),
    ("capital_level", pa.float64()),
    ("plausibility_status", pa.string()),
    ("used_macro", pa.bool_()),
    ("world_view_id", pa.string()),
    ("n_trades", pa.int64()),
])


def stress_rows(results: list[ScenarioStressResult], run_id: str) -> list[dict[str, Any]]:
    """Flatten stress results into one analytics row per (scenario, severity)."""
    now = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    for r in results:
        cap = r.capital
        macro = r.materialized.get("provenance", {}).get("macro", {})
        rows.append({
            "valuation_date": r.valuation_date, "run_id": run_id, "generated_at": now,
            "scenario": r.scenario, "severity": r.severity,
            "scenario_version": r.scenario_version,
            "total_pnl": float(r.total_pnl),
            "capital_delta": float(cap.capital_delta) if cap else 0.0,
            "es_base": float(cap.es_base) if cap else 0.0,
            "es_shocked": float(cap.es_shocked) if cap else 0.0,
            "capital_level": float(cap.level) if cap else 0.0,
            "plausibility_status": r.plausibility.status if r.plausibility else "",
            "used_macro": bool(macro.get("used_macro", False)),
            "world_view_id": str(macro.get("world_view_id", "")),
            "n_trades": len(r.results),
        })
    return rows


class StressDashboard:
    def __init__(self, results: list[ScenarioStressResult], run_id: str = ""):
        self.df = pd.DataFrame(stress_rows(results, run_id))

    def _pivot(self, value: str) -> pd.DataFrame:
        if self.df.empty:
            return pd.DataFrame()
        m = self.df.pivot_table(index="scenario", columns="severity", values=value,
                                aggfunc="first")
        cols = sorted(m.columns, key=lambda s: _SEVERITY_ORDER.get(s, 99))
        return m[cols]

    def pnl_matrix(self) -> pd.DataFrame:
        return self._pivot("total_pnl")

    def capital_matrix(self) -> pd.DataFrame:
        return self._pivot("capital_delta")

    def ranking(self) -> pd.DataFrame:
        """Scenario/severity rows ordered worst-loss first."""
        if self.df.empty:
            return self.df
        return self.df.sort_values("total_pnl").reset_index(drop=True)

    def breaches(self, loss_limit: float | None = None,
                 capital_limit: float | None = None) -> pd.DataFrame:
        """Rows whose loss or capital increase exceeds a limit."""
        if self.df.empty:
            return self.df
        mask = pd.Series(False, index=self.df.index)
        if loss_limit is not None:
            mask |= -self.df["total_pnl"] >= loss_limit
        if capital_limit is not None:
            mask |= self.df["capital_delta"] >= capital_limit
        return self.df[mask].sort_values("total_pnl").reset_index(drop=True)

    def to_markdown(self) -> str:
        """Compact scenario x severity P&L / capital tables for logs or a report."""
        if self.df.empty:
            return "_(no scenarios)_"
        pnl, cap = self.pnl_matrix(), self.capital_matrix()
        return (f"P&L by scenario x severity:\n{_fmt_matrix(pnl)}\n\n"
                f"Capital delta by scenario x severity:\n{_fmt_matrix(cap)}")


def _fmt_matrix(m: pd.DataFrame) -> str:
    if m.empty:
        return "  (none)"
    header = "scenario".ljust(24) + "".join(c.rjust(16) for c in m.columns)
    lines = [header]
    for scen, row in m.iterrows():
        cells = "".join(("" if pd.isna(v) else f"{v:,.0f}").rjust(16) for v in row)
        lines.append(str(scen).ljust(24) + cells)
    return "\n".join(lines)


class ScenarioResultStore:
    def __init__(self, project_root: Path):
        self.dataset_uri = storage.join(
            storage.data_base(project_root), "pricer", "store", "scenario_results")

    def write_run(self, rows: list[dict[str, Any]], run_id: str) -> str | None:
        if not rows:
            return None
        storage.write_dataset(
            pa.Table.from_pylist(rows, schema=_SCHEMA),
            self.dataset_uri, partition_cols=["valuation_date"],
            basename_template=f"{run_id}-{{i}}.parquet",
        )
        return self.dataset_uri

    def _query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        return storage.query(sql, self.dataset_uri, params=params)

    def _latest(self, valuation_date: str) -> str:
        """SQL fragment: latest run per (scenario, severity) on a date."""
        return ("SELECT * FROM (SELECT *, row_number() OVER (PARTITION BY scenario, severity "
                "ORDER BY generated_at DESC) AS rn FROM @SRC WHERE valuation_date = ?) WHERE rn = 1")

    def latest_run(self, valuation_date: str) -> pd.DataFrame:
        return self._query(
            f"SELECT scenario, severity, total_pnl, capital_delta, plausibility_status, "
            f"used_macro FROM ({self._latest(valuation_date)}) "
            f"ORDER BY scenario, severity", [valuation_date])

    def worst_scenarios(self, valuation_date: str, limit: int = 10) -> pd.DataFrame:
        return self._query(
            f"SELECT scenario, severity, total_pnl, capital_delta "
            f"FROM ({self._latest(valuation_date)}) ORDER BY total_pnl ASC LIMIT {int(limit)}",
            [valuation_date])
