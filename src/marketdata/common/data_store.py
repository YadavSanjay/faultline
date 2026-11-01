"""
CSV-based local data store for market data.

Output layout:
    data/marketdata/<asset_class>/<filename>.csv

All paths are resolved relative to the project root so the code works from any
working directory.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from src.common import storage
from src.common.provenance import git_sha

from .config_loader import get_project_root

logger = logging.getLogger(__name__)

# A file location handed to the data store: a local ``Path`` (the historical form, still what
# local runs and tests pass) or a location string / ``s3://`` URI in AWS mode.
Loc = str | Path

_GIT_SHA: str | None = None


def _cached_git_sha() -> str:
    """Resolve the git SHA once per process (avoids a git call per CSV written)."""
    global _GIT_SHA
    if _GIT_SHA is None:
        _GIT_SHA = git_sha(get_project_root())
    return _GIT_SHA


def _basename(loc: str) -> str:
    return loc.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def get_output_path(asset_class: str, filename: str) -> Path | str:
    """Full location for an output file (parent dirs created for local).

    Returns a ``Path`` in local mode (downloaders/tests use Path methods on it) or an ``s3://``
    URI in AWS mode. Local resolves to the historical ``<repo>/data/marketdata/<class>/<file>``.
    """
    loc = storage.data_path("marketdata", asset_class, filename)
    storage.makedirs_for(loc)
    return loc if storage.is_remote_uri(loc) else Path(loc)


def write_provenance(path: Loc, df: pd.DataFrame, source_url: str = "") -> None:
    """Write a `<file>.provenance.json` sidecar describing what was just persisted.

    Best-effort: a provenance failure must never break a download. Captures git SHA, fetch
    time, row count, columns, the file content hash, and (if a `date` column is present) the
    covered date range. `source_url` is optional — pass it from the downloader when known.
    """
    loc = str(path)
    try:
        prov = {
            "file": _basename(loc),
            "source_url": source_url,
            "fetched_at": datetime.now(UTC).isoformat(),
            "git_sha": _cached_git_sha(),
            "row_count": len(df),
            "columns": [str(c) for c in df.columns],
            "content_sha256": storage.sha256(loc),
        }
        if "date" in df.columns and len(df):
            dates = df["date"].astype(str)
            prov["date_min"] = str(dates.min())
            prov["date_max"] = str(dates.max())
        storage.write_text_atomic(f"{loc}.provenance.json", json.dumps(prov, indent=2))
    except Exception as exc:  # provenance must never break a download
        logger.warning("provenance sidecar failed for %s: %s", loc, exc)


def save_csv(df: pd.DataFrame, path: Loc) -> None:
    """Write DataFrame to CSV (full overwrite) + a provenance sidecar."""
    if df.empty:
        logger.warning("save_csv: empty DataFrame — nothing written to %s", path)
        return
    df.to_csv(str(path), index=False)
    logger.info("Saved %d rows -> %s", len(df), path)
    write_provenance(path, df)


def load_csv(path: Loc) -> pd.DataFrame:
    """Load CSV to DataFrame. Returns empty DataFrame if file does not exist."""
    loc = str(path)
    if not storage.exists(loc):
        return pd.DataFrame()
    df = pd.read_csv(loc)
    logger.debug("Loaded %d rows <- %s", len(df), loc)
    return df


def upsert_csv(
    df_new: pd.DataFrame,
    path: Loc,
    date_col: str = "date",
    key_cols: list[str] | None = None,
) -> None:
    """
    Merge df_new into the existing CSV, deduplicate on key_cols, overwrite file.

    key_cols defaults to [date_col]. Pass key_cols=["date","ticker"] for
    multi-instrument long-format CSVs. New data wins on conflict.
    """
    if df_new.empty:
        logger.warning("upsert_csv: nothing to write to %s", path)
        return

    dedup_cols = key_cols if key_cols else [date_col]

    existing = load_csv(path)
    if existing.empty:
        save_csv(df_new, path)
        return

    combined = pd.concat([existing, df_new], ignore_index=True)
    combined[date_col] = pd.to_datetime(combined[date_col], errors="coerce")
    combined = (
        combined
        .dropna(subset=[date_col])
        .sort_values(date_col, kind="stable")  # stable: keep="last" must see df_new after existing on date ties
        .drop_duplicates(subset=dedup_cols, keep="last")
    )
    combined[date_col] = combined[date_col].dt.strftime("%Y-%m-%d")
    save_csv(combined, path)
    logger.info("Upserted -> %s (%d rows total)", path, len(combined))


def already_downloaded_dates(path: Loc, date_col: str = "date") -> set[str]:
    """Return the set of date strings already in a CSV file."""
    df = load_csv(path)
    if df.empty or date_col not in df.columns:
        return set()
    return set(df[date_col].astype(str).unique())
