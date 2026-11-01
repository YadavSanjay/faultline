"""DTCC/CFTC SDR trade records keyed on the CFTC ``Dissemination Identifier``.

Pure-pandas helpers for the SDR rates ingest (spec: ``docs/specs/sdr-dedup.md``): merge new records
into an existing set, applying CFTC Part 43 corrections and cancellations; decide which business
dates still need fetching; filter a raw daily file down to one currency's SOFR swap records; and
write/read record files with every value kept as an exact string. Storage I/O goes through
``src.common.storage``.

Every column is held as a string end to end. Identifiers are 19-digit integers above 2^53, and
``Original Dissemination Identifier`` is blank for new trades, so any numeric parse (int64 or the
float64 pandas falls back to around blanks) would corrupt them and break chain matching.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from src.common import storage

ID_COLUMNS: tuple[str, ...] = ("Dissemination Identifier", "Original Dissemination Identifier")

OUTPUT_COLUMNS: tuple[str, ...] = (
    "date",
    "Dissemination Identifier",
    "Original Dissemination Identifier",
    "Action type",
    "Event type",
    "Event timestamp",
    "Execution Timestamp",
    "Effective Date",
    "Expiration Date",
    "Cleared",
    "Platform identifier",
    "Package indicator",
    "Non-standardized term indicator",
    "Block trade election indicator",
    "UPI FISN",
    "UPI Underlier Name",
    "Notional currency-Leg 1",
    "Notional amount-Leg 1",
    "Fixed rate-Leg 1",
    "Fixed rate-Leg 2",
    "Spread-Leg 1",
    "Spread-Leg 2",
    "Fixed rate payment frequency period-Leg 1",
    "Fixed rate payment frequency period-Leg 2",
)

_DID, _ODID = ID_COLUMNS
_ACTION = "Action type"
_EVENT_TS = "Event timestamp"
_PREMIUM = "Option Premium Amount"


def _text(series: pd.Series) -> pd.Series:
    """Values as exact strings, with every kind of blank (None, NaN, NA, empty string) as ``None``."""
    def one(v: object) -> object:
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        s = str(v)
        return s if s != "" else None

    return series.map(one).astype("object")


def _with_output_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Adds any missing output column (blank); keeps extra columns after the output ones."""
    out = df.copy()
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = None
    extra = [c for c in out.columns if c not in OUTPUT_COLUMNS]
    return out[list(OUTPUT_COLUMNS) + extra]


def _is_legacy(df: pd.DataFrame) -> bool:
    """The collapsed pre-change layout: no ``Dissemination Identifier`` column."""
    return _DID not in df.columns


def merge_sdr_records(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merges new SDR records into an existing set, keyed on ``Dissemination Identifier``.

    New data wins on an identical identifier; ``CORR`` keeps only the latest record of a chain and
    ``EROR`` removes its whole chain. An existing frame without a ``Dissemination Identifier``
    column is legacy and is discarded.

    A chain is identified by its root: ``Original Dissemination Identifier`` when present, else the
    record's own identifier. Its versions are the root record and its ``CORR`` records; the one
    with the largest ``Event timestamp`` (ISO-8601 UTC, so string order is time order) survives.
    Lifecycle records (``MODI``, ``TERM`` and any other action type) are kept as they are.

    Args:
        existing: Records already on disk (may be empty or legacy).
        new: Freshly downloaded records.

    Returns:
        The merged, correction-resolved record set.
    """
    return _with_output_columns(merge_sdr_chains(existing, new)).reset_index(drop=True)


def sdr_dates_to_fetch(existing: pd.DataFrame, dates: list[str]) -> list[str]:
    """Returns the business dates that still need fetching, in input order.

    Args:
        existing: Records already on disk (may be empty or legacy).
        dates: Candidate dates (``YYYY-MM-DD``) in the window.

    Returns:
        Every date when ``existing`` is empty or legacy, else the dates absent from
        ``existing["date"]``.
    """
    if existing.empty or _is_legacy(existing) or "date" not in existing.columns:
        return list(dates)
    have = {str(d) for d in _text(existing["date"]).dropna()}
    return [d for d in dates if d not in have]


def filter_sofr_swap_records(
    raw: pd.DataFrame, day: str, underlier_keywords: list[str], currency: str
) -> pd.DataFrame:
    """Keeps the non-option SOFR swap records of one currency from a raw daily SDR file.

    Args:
        raw: One day's raw CFTC Part 43 records.
        day: The business date (``YYYY-MM-DD``) stamped into the ``date`` column.
        underlier_keywords: Keywords matched case-insensitively against ``UPI Underlier Name``.
        currency: Required ``Notional currency-Leg 1``.

    Returns:
        The kept rows (every action type) with exactly ``OUTPUT_COLUMNS``.
    """
    def col(name: str) -> pd.Series:
        if name in raw.columns:
            return _text(raw[name])
        return pd.Series(None, index=raw.index, dtype="object")

    underlier = col("UPI Underlier Name").fillna("").str.lower()
    keywords = [k.lower() for k in underlier_keywords if k]
    named = pd.Series(False, index=raw.index)
    for k in keywords:
        named |= underlier.str.contains(k, regex=False)
    mask = (col("Notional currency-Leg 1") == currency).fillna(False) & named & col(_PREMIUM).isna()

    out = pd.DataFrame(index=raw.index[mask.to_numpy(dtype=bool)])
    for name in OUTPUT_COLUMNS:
        out[name] = day if name == "date" else col(name)[out.index]
    return out.reset_index(drop=True)


def write_sdr_records(df: pd.DataFrame, path: Path | str) -> None:
    """Writes SDR records to CSV with identifier columns kept as exact strings.

    The write is atomic (via the storage façade), so a failed run never leaves a half-written file.

    Args:
        df: Records to write.
        path: Destination location (local path or storage URI).
    """
    out = df.copy()
    for col in ID_COLUMNS:
        if col in out.columns:
            out[col] = _text(out[col])
    storage.write_text_atomic(str(path), out.to_csv(index=False))


def read_sdr_records(path: Path | str) -> pd.DataFrame:
    """Reads SDR records with identifier columns as exact strings (blanks as missing).

    Args:
        path: Source location (local path or storage URI).

    Returns:
        The records as written; an empty frame when the location doesn't exist.
    """
    loc = str(path)
    if not storage.exists(loc):
        return pd.DataFrame()
    return storage.read_csv(loc, dtype=str, keep_default_na=False, na_values=[""])


def cancelled_sidecar(path: Path | str) -> str:
    """Location of the cancelled-roots sidecar for a record file (``<file>.cancelled.csv``).

    Args:
        path: The record file's location.

    Returns:
        The sidecar location.
    """
    return f"{path}.cancelled.csv"


def roll_sdr_window(
    existing: pd.DataFrame,
    new: pd.DataFrame,
    known_cancelled: pd.DataFrame,
    start: str,
    merge: Callable[[pd.DataFrame, pd.DataFrame], pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Merges a run's records into a rolling window and keeps cancelled trades cancelled.

    The merge drops ``EROR`` rows once applied, so a day fetched late (after a transient failure)
    would bring a cancelled trade back. The cancelled chain roots are therefore kept separately
    (``root``, ``date``) and applied after every merge. They age out with the window: a cancelled
    trade is never dated later than its ``EROR``, so it has left the window too.

    Args:
        existing: Records already on disk (may be empty or legacy).
        new: Records fetched this run.
        known_cancelled: Previously stored cancelled roots (may be empty).
        start: Window start (``YYYY-MM-DD``); older records and cancellations are dropped.
        merge: ``merge_sdr_records`` (rates schema) or ``merge_sdr_chains`` (inputs' own columns).

    Returns:
        The windowed, cancellation-applied records, and the updated cancelled roots.
    """
    merged = merge(existing, new)
    merged = merged[merged["date"].fillna("") >= start].reset_index(drop=True)

    eror = new[new[_ACTION].fillna("").str.upper() == "EROR"] if _ACTION in new.columns else new.iloc[0:0]
    eror_odid = eror[_ODID] if _ODID in eror.columns else pd.Series(None, index=eror.index, dtype="object")
    fresh = pd.DataFrame({
        "root": _text(eror_odid.where(eror_odid.notna(), eror[_DID])),
        "date": _text(eror["date"]),
    })
    frames = [f for f in (known_cancelled, fresh) if not f.empty and {"root", "date"} <= set(f.columns)]
    if frames:
        cancelled = pd.concat(frames, ignore_index=True)
        cancelled = cancelled[cancelled["root"].notna() & (cancelled["date"].fillna("") >= start)]
        cancelled = (cancelled.sort_values("date", kind="stable")
                     .drop_duplicates("root", keep="last").reset_index(drop=True))
    else:
        cancelled = pd.DataFrame(columns=["root", "date"])

    if not merged.empty and not cancelled.empty:
        odid = _text(merged[_ODID]) if _ODID in merged.columns else pd.Series(None, index=merged.index)
        root = odid.where(odid.notna(), _text(merged[_DID]))
        merged = merged[~root.isin(set(cancelled["root"]))].reset_index(drop=True)
    return merged, cancelled


def merge_sdr_chains(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merges new SDR records into an existing set, keeping exactly the inputs' columns.

    Same chain semantics as ``merge_sdr_records`` (keyed on ``Dissemination Identifier``; ``EROR``
    removes its chain, the latest ``CORR`` by ``Event timestamp`` wins, lifecycle records are kept),
    but no output columns are added: the result has the union of the columns of ``new`` and of a
    non-legacy ``existing``. A legacy ``existing`` (no ``Dissemination Identifier``) is discarded.

    Args:
        existing: Records already on disk (may be empty or legacy).
        new: Freshly downloaded records.

    Returns:
        The merged, correction-resolved record set, every value as an exact string.
    """
    frames = [f for f in ([] if _is_legacy(existing) else [existing]) + [new] if len(f.columns)]
    columns = list(dict.fromkeys(c for f in reversed(frames) for c in f.columns))
    nonempty = [f for f in frames if not f.empty]
    if not nonempty:
        return pd.DataFrame(columns=columns)
    combined = pd.concat(nonempty, ignore_index=True).reindex(columns=columns)

    def col(name: str) -> pd.Series:
        if name in combined.columns:
            return _text(combined[name])
        return pd.Series(None, index=combined.index, dtype="object")

    for name in ID_COLUMNS:
        if name in combined.columns:
            combined[name] = _text(combined[name])
    combined = combined[col(_DID).notna()]
    # `new` comes after `existing` in the concat, so keep="last" is "new wins".
    combined = combined.drop_duplicates(subset=[_DID], keep="last")

    did, odid = col(_DID), col(_ODID)
    root = odid.where(odid.notna(), did)
    action = col(_ACTION).fillna("").str.upper()

    # EROR cancels its whole chain; the EROR row itself is not kept.
    cancelled = set(root[action == "EROR"])
    keep = ~root.isin(cancelled)

    # CORR: among a chain's versions (root record + its CORRs) keep only the latest.
    is_version = (did == root) | (action == "CORR")
    corrected = set(root[action == "CORR"]) - cancelled
    versions = combined[keep & is_version & root.isin(corrected)]
    if not versions.empty:
        ts = col(_EVENT_TS)[versions.index].fillna("")
        order = pd.DataFrame({"root": root[versions.index], "ts": ts}, index=versions.index)
        latest = order.sort_values("ts", kind="stable").groupby("root").tail(1).index
        superseded = versions.index.difference(latest)
        keep &= ~combined.index.isin(superseded)

    return combined[keep].reset_index(drop=True)
