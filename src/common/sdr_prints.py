"""Which DTCC SDR credit records are prices: the rule shared by ingest and pricing.

The marketdata credit files keep every disseminated record (new trades, lifecycle events,
novations, clearing records, corrections). Only some of them are prices. This module holds that
rule in one place so the pricer's CDS and bond adapters and the marketdata layer both use it
without depending on each other (the pricer never imports ``src.marketdata``).

A print is price-forming when it is a new trade: ``Action type`` ``NEWT`` with ``Event type``
``TRAD``. A ``CORR`` has a blank ``Event type``, so whether it corrects a new trade can't be told
from the stored record; corrections are excluded. Prints are windowed by execution date, and a
print disseminated after the valuation date is never used (no look-ahead).

Spec: ``docs/specs/credit-sdr-dedup.md`` (criteria C4, C5; locked at
``src.marketdata.credit.sdr_credit_records.price_forming_prints``, which delegates here).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def _text(series: pd.Series) -> pd.Series:
    """Values as exact strings, with every kind of blank (None, NaN, NA, empty string) as ``None``."""
    def one(v: object) -> object:
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        s = str(v)
        return s if s != "" else None

    return series.map(one).astype("object")


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    """``df[name]`` as exact strings, or an all-blank column when ``df`` lacks it."""
    if name in df.columns:
        return _text(df[name])
    return pd.Series(None, index=df.index, dtype="object")


def history_shortfall(records: pd.DataFrame, as_of: str, lookback_days: int) -> str | None:
    """The file's first record date when it starts after the window the pricer needs, else None.

    The ingest keeps a bounded history; a valuation date whose lookback reaches before the file's
    first record would quietly price from fewer prints. Callers turn a non-None result into a
    data-quality warning.

    Args:
        records: Stored credit records (with a ``date`` column).
        as_of: The valuation date (``YYYY-MM-DD``).
        lookback_days: The calendar-day window the pricer uses.

    Returns:
        The earliest stored ``date`` (``YYYY-MM-DD``) if it is later than the window start
        (``as_of - lookback_days``), else None (also None for an empty file).
    """
    dates = _column(records, "date").dropna()
    if dates.empty:
        return None
    first = str(dates.min())
    needed = (date.fromisoformat(as_of) - timedelta(days=int(lookback_days))).isoformat()
    return first if first > needed else None


def price_forming_prints(records: pd.DataFrame, as_of: str, lookback_days: int) -> pd.DataFrame:
    """Selects the new-trade prints executed in the lookback window and disseminated by ``as_of``.

    Args:
        records: Stored credit records.
        as_of: The valuation date (``YYYY-MM-DD``).
        lookback_days: Calendar-day window length; execution date ``d`` is kept when
            ``as_of - lookback_days < d <= as_of``.

    Returns:
        The ``NEWT``/``TRAD`` rows with ``date <= as_of`` and an execution date in the window, with
        their columns and values unchanged.
    """
    if records.empty:
        return records.copy()
    end = date.fromisoformat(as_of)
    start = end - timedelta(days=int(lookback_days))

    # format="ISO8601": the feed mixes whole and fractional seconds; inferring one format from the
    # first value would turn the others into NaT.
    executed = pd.to_datetime(_column(records, "Execution Timestamp"), errors="coerce", utc=True,
                              format="ISO8601")
    exec_day = executed.dt.normalize()            # UTC execution date; NaT compares False below
    lo = pd.Timestamp(start, tz="UTC")
    hi = pd.Timestamp(end, tz="UTC")
    disseminated = _column(records, "date").fillna("")
    mask = (
        (_column(records, "Action type") == "NEWT")
        & (_column(records, "Event type") == "TRAD")
        & (disseminated != "") & (disseminated <= as_of)
        & (exec_day > lo) & (exec_day <= hi)
    ).fillna(False).to_numpy(dtype=bool)
    return records[mask].copy()
