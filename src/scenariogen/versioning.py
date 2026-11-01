"""Scenario-definition versioning into a governed store (S3).

A stress number must be reproducible from pinned inputs (design §9). Like the portfolio
(P1c) and market data (P0b), a scenario *definition* gets an immutable, content-addressed
version. `compute_scenario_version` hashes only the economic content (drivers + overlays +
method + horizon) — narrative/trigger doc edits don't churn the version, but any change to
a shock magnitude does. `ScenarioStore` persists each distinct version's full spec
(immutable, append-only) plus a queryable manifest, mirroring `PortfolioStore`.

A materialized scenario's full identity is ``{scenario_version, severity, cov_id}``: those
three reproduce the exact factor vector that priced the book.
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
    ("scenario_version", pa.string()),
    ("created_at", pa.string()),
    ("name", pa.string()),
    ("method", pa.string()),
    ("n_drivers", pa.int64()),
    ("n_overlays", pa.int64()),
    ("horizon_days", pa.int64()),
])

# The economic fields that define a scenario (everything else is documentation).
# "severity" (the anchoring lead + direction) changes the economics of a tier, so it is part of the version.
_ECONOMIC_KEYS = ("drivers", "overlays", "method", "horizon_days", "severity")


def compute_scenario_version(spec: dict[str, Any]) -> str:
    """Content-address a scenario definition's economic content (16-hex, order-independent)."""
    economic = {k: spec[k] for k in _ECONOMIC_KEYS if k in spec}
    blob = json.dumps(economic, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class ScenarioStore:
    def __init__(self, project_root: Path):
        base = storage.join(storage.data_base(project_root), "pricer", "store", "scenario")
        self.versions_dir = storage.join(base, "versions")
        self.manifest_dir = storage.join(base, "manifest")

    def register(self, name: str, spec: dict[str, Any]) -> str:
        """Compute the version and persist it (content + manifest row) if new. Idempotent."""
        version = compute_scenario_version(spec)
        content = storage.join(self.versions_dir, f"{version}.json")
        if not storage.exists(content):
            storage.write_text_atomic(
                content, json.dumps({"scenario_version": version, "name": name, **spec}, indent=2))
            self._append_manifest(version, name, spec)
        return version

    def load_version(self, version: str) -> dict[str, Any]:
        path = storage.join(self.versions_dir, f"{version}.json")
        if not storage.exists(path):
            raise KeyError(f"scenario version not found: {version}")
        return dict(json.loads(storage.read_text(path)))

    def versions(self, limit: int = 50) -> pd.DataFrame:
        sql = f"SELECT * FROM @SRC ORDER BY created_at DESC LIMIT {int(limit)}"
        return storage.query(
            sql, self.manifest_dir, recursive=False, read_parquet_extra=", union_by_name=true")

    # -- internals ----------------------------------------------------------- #
    def _append_manifest(self, version: str, name: str, spec: dict[str, Any]) -> None:
        record = {
            "scenario_version": version,
            "created_at": datetime.now(UTC).isoformat(),
            "name": name,
            "method": str(spec.get("method", "")),
            "n_drivers": len(spec.get("drivers", {})),
            "n_overlays": len(spec.get("overlays", {})),
            "horizon_days": int(spec.get("horizon_days", 0)),
        }
        storage.write_dataset(
            pa.Table.from_pylist([record], schema=_MANIFEST_SCHEMA),
            self.manifest_dir, partition_cols=[],
            basename_template=f"{version}-{{i}}.parquet",
        )
