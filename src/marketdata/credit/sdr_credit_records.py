"""DTCC/CFTC SDR credit records (spec: ``docs/specs/credit-sdr-dedup.md``).

Filters a raw daily credit file down to the configured names with every value kept as an exact
string, and selects the price-forming prints (new trades) that feed the CDS spread medians. Merging
and storage reuse the rates SDR helpers (``src.marketdata.rates.sdr_records``).

The price-forming rule itself lives in ``src.common.sdr_prints`` (shared with the pricer, which
never imports ``src.marketdata``); ``price_forming_prints`` here is the spec's locked entry point and
delegates to it.
"""

from __future__ import annotations

import pandas as pd

from src.common.sdr_prints import price_forming_prints as _shared_price_forming_prints

CREDIT_OUTPUT_COLUMNS: tuple[str, ...] = (
    "date",
    "Dissemination Identifier",
    "Original Dissemination Identifier",
    "Action type",
    "Event type",
    "Event timestamp",
    "Execution Timestamp",
    "Effective Date",
    "Expiration Date",
    "Underlying Asset Name",
    "UPI Underlier Name",
    "UPI FISN",
    "Notional currency-Leg 1",
    "Notional amount-Leg 1",
    "Spread-Leg 1",
    "Fixed rate-Leg 1",
    "Price",
    "Price notation",
    "Cleared",
    "Platform identifier",
    "Package indicator",
)


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


def filter_credit_records(
    raw: pd.DataFrame,
    day: str,
    filter_column: str,
    include_keywords: list[str],
    exclude_keywords: list[str],
) -> pd.DataFrame:
    """Keeps the credit records of the configured names from a raw daily SDR file.

    Args:
        raw: One day's raw CFTC Part 43 credit records.
        day: The business date (``YYYY-MM-DD``) stamped into the ``date`` column.
        filter_column: The column matched against the keywords.
        include_keywords: A row is kept when ``filter_column`` contains any of these
            (case-insensitive substring).
        exclude_keywords: A row is dropped when ``filter_column`` contains any of these
            (case-insensitive substring).

    Returns:
        The kept rows (every action type) with exactly ``CREDIT_OUTPUT_COLUMNS``, values as exact
        strings.
    """
    name = _column(raw, filter_column).fillna("").str.lower()
    included = pd.Series(False, index=raw.index)
    for k in (k.lower() for k in include_keywords if k):
        included |= name.str.contains(k, regex=False)
    excluded = pd.Series(False, index=raw.index)
    for k in (k.lower() for k in exclude_keywords if k):
        excluded |= name.str.contains(k, regex=False)
    mask = (included & ~excluded & (name != "")).to_numpy(dtype=bool)

    out = pd.DataFrame(index=raw.index[mask])
    for col in CREDIT_OUTPUT_COLUMNS:
        out[col] = day if col == "date" else _column(raw, col)[out.index]
    return out.reset_index(drop=True)


def price_forming_prints(records: pd.DataFrame, as_of: str, lookback_days: int) -> pd.DataFrame:
    """Selects the new-trade prints executed in the lookback window and disseminated by ``as_of``.

    Args:
        records: Stored credit records (``CREDIT_OUTPUT_COLUMNS``).
        as_of: The valuation date (``YYYY-MM-DD``).
        lookback_days: Calendar-day window length; execution date ``d`` is kept when
            ``as_of - lookback_days < d <= as_of``.

    Returns:
        The ``NEWT``/``TRAD`` rows with ``date <= as_of`` and an execution date in the window, with
        their columns and values unchanged.
    """
    # The rule lives in src.common so the pricer can use it without importing src.marketdata.
    return _shared_price_forming_prints(records, as_of, lookback_days)
