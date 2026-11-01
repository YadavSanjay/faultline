"""Analyst sign-off workflow for stress scenarios (S5, design §9 governance).

A scenario definition is content-addressed by `scenario_version` (S3). Before it can drive a
*production* stress run, an analyst must sign it off. `SignoffStore` is an append-only audit
log of approve/reject decisions keyed by `scenario_version`; the latest decision for a version
wins. The runner's `--require-signoff` gate consults `is_approved` and holds unapproved
versions out of the run — the same GATE discipline as plausibility (S3) and macro freshness
(S4), but for human approval.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import pyarrow as pa

from src.common import storage

from .errors import ScenarioGenerationError
from .versioning import compute_scenario_version

if TYPE_CHECKING:                     # the catalog pulls in factor history; keep this module light at run time
    from src.scenariogen.catalog import ScenarioCatalog

_STATUSES = ("approved", "rejected")
# ``seq`` given to records written before the column existed: they rank before every sequenced record.
_LEGACY_SEQ = -1

_SCHEMA = pa.schema([
    ("scenario_version", pa.string()),
    ("scenario", pa.string()),
    ("status", pa.string()),
    ("approver", pa.string()),
    ("recorded_at", pa.string()),
    ("note", pa.string()),
    ("seq", pa.int64()),
])


@dataclass(frozen=True)
class SignoffRecord:
    scenario_version: str
    scenario: str
    status: str               # approved | rejected
    approver: str
    recorded_at: str
    note: str = ""
    seq: int = 0              # monotonic write order; "latest wins" sorts on this, not the
                             # wall clock (coarse OS timers can't separate rapid sign-offs)

    def as_dict(self) -> dict[str, Any]:
        return {"scenario_version": self.scenario_version, "scenario": self.scenario,
                "status": self.status, "approver": self.approver,
                "recorded_at": self.recorded_at, "note": self.note, "seq": self.seq}


class SignoffStore:
    def __init__(self, project_root: Path):
        self.dataset_uri = storage.join(
            storage.data_base(project_root), "pricer", "store", "signoff")

    def record(self, scenario_version: str, scenario: str, status: str, approver: str,
               note: str = "") -> SignoffRecord:
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {_STATUSES}, got {status!r}")
        rec = SignoffRecord(scenario_version, scenario, status, approver,
                            datetime.now(UTC).isoformat(), note, seq=next_seq(self._read()))
        storage.write_dataset(
            pa.Table.from_pylist([rec.as_dict()], schema=_SCHEMA),
            self.dataset_uri, partition_cols=[],
            basename_template=f"{scenario_version}-{_ts()}-{uuid.uuid4().hex[:8]}-{{i}}.parquet",
        )
        return rec

    def history(self, scenario_version: str | None = None) -> pd.DataFrame:
        df = self._read()
        if df.empty or scenario_version is None:
            return df
        return df[df["scenario_version"] == scenario_version].reset_index(drop=True)

    def latest(self, scenario_version: str) -> SignoffRecord | None:
        df = self.history(scenario_version)
        if df.empty:
            return None
        row = df.iloc[0]                                  # history() is ordered newest first
        return SignoffRecord(row["scenario_version"], row["scenario"], row["status"],
                             row["approver"], row["recorded_at"], row.get("note", "") or "",
                             int(row["seq"]))

    def is_approved(self, scenario_version: str) -> bool:
        rec = self.latest(scenario_version)
        return rec is not None and rec.status == "approved"

    def _read(self) -> pd.DataFrame:
        # No ORDER BY in the query: records written before ``seq`` existed have no such column, and
        # ordering by it there fails. ``order_records`` ranks those legacy records instead.
        df = storage.query("SELECT * FROM @SRC", self.dataset_uri,
                           recursive=False, read_parquet_extra=", union_by_name=true")
        return order_records(df)


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")


def _api_key_safe_user() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def order_records(df: pd.DataFrame) -> pd.DataFrame:
    """Orders sign-off records newest first, tolerating legacy records without ``seq``.

    A record with no ``seq`` value (the column is absent or the value is missing) is a legacy
    record and gets ``seq = -1``. Records are ordered by ``seq`` descending, then by
    ``recorded_at`` descending (ISO-8601 UTC strings compared as text).

    Args:
        df: Sign-off records with the sign-off schema columns and, when present, ``seq``
            (integers, or floats with ``NaN`` for legacy rows). Not modified.

    Returns:
        A new frame with an integer ``seq`` column, the input's other columns and values
        unchanged, ordered newest first, with the index reset to ``0..n-1``. An empty input
        gives an empty frame with a ``seq`` column.
    """
    out = df.copy()
    seq = (pd.to_numeric(out["seq"], errors="coerce") if "seq" in out.columns
           else pd.Series(float("nan"), index=out.index))
    out["seq"] = seq.fillna(_LEGACY_SEQ).astype("int64")
    if out.empty:
        return out.reset_index(drop=True)
    return out.sort_values(["seq", "recorded_at"], ascending=False, kind="mergesort").reset_index(drop=True)


def next_seq(df: pd.DataFrame) -> int:
    """Returns the sequence number for the next sign-off record.

    Args:
        df: Existing sign-off records; the ``seq`` column may be absent or hold missing values
            for legacy records.

    Returns:
        One more than the largest ``seq`` among sequenced records, or 0 when there are none.
    """
    if "seq" not in df.columns:
        return 0
    seq = pd.to_numeric(df["seq"], errors="coerce").dropna()
    seq = seq[seq > _LEGACY_SEQ]                 # an already-ordered frame marks legacy rows -1
    return int(seq.max()) + 1 if not seq.empty else 0


def scenario_versions(catalog: ScenarioCatalog) -> dict[str, str]:
    """Returns the version of every resolvable scenario and sensitivity in a catalog.

    The version is ``compute_scenario_version`` of the name's resolved spec (``catalog.spec(name)``),
    so a historical replay is versioned by its episode drivers and horizon, as the stress runner
    registers it, and an authored scenario keeps the hash of its config spec.

    Args:
        catalog: The scenario catalog, with replays resolved against the factor history.

    Returns:
        Name to 16-hex scenario version, for ``catalog.names()`` then ``catalog.sensitivity_names()``
        in that order. A name whose spec can't be resolved (``catalog.spec`` raises
        ``ScenarioGenerationError``) is left out.
    """
    versions: dict[str, str] = {}
    for name in [*catalog.names(), *catalog.sensitivity_names()]:
        try:
            versions[name] = compute_scenario_version(catalog.spec(name))
        except ScenarioGenerationError:     # e.g. a replay window the on-disk history does not cover
            continue
    return versions
