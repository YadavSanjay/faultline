"""Synthetic CFTC Part 43 SDR credit records for the credit-sdr-dedup spec tests.

No DTCC data is committed (oracles.md: DTCC terms). Every record here is made up: identifiers are
19-digit integers held as strings, timestamps are ISO-8601 UTC strings, spreads are decimal strings
("0.005935" = 59.35 bp). Blank fields are ``None``.

Helpers are exposed through the session-scoped ``cr`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

# CREDIT_OUTPUT_COLUMNS exactly as written in docs/specs/credit-sdr-dedup.md (Interface), typed out
# here so the tests do not take their expectations from the module under test.
SPEC_CREDIT_COLUMNS: tuple[str, ...] = (
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

# The sdr-dedup OUTPUT_COLUMNS exactly as written in docs/specs/sdr-dedup.md (Interface), for C1.
SPEC_RATES_COLUMNS: tuple[str, ...] = (
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

DID = "Dissemination Identifier"
ODID = "Original Dissemination Identifier"


def credit_record(did: str, action: str = "NEWT", orig: str | None = None, ts: str = "2026-10-01T14:00:00Z",
                  date: str = "2026-10-01", event: str | None = "TRAD", exec_ts: str | None = None,
                  fields: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """One synthetic single-name CDS record with every credit output column filled."""
    rec: dict[str, str | None] = {
        "date": date,
        DID: did,
        ODID: orig,
        "Action type": action,
        "Event type": event,
        "Event timestamp": ts,
        "Execution Timestamp": ts if exec_ts is None else exec_ts,
        "Effective Date": "2026-09-22",
        "Expiration Date": "2031-12-20",
        "Underlying Asset Name": "ORACLE CORPORATION",
        "UPI Underlier Name": "ORACLE CORP",
        "UPI FISN": "NA/Snr CDS ORCL USD",
        "Notional currency-Leg 1": "USD",
        "Notional amount-Leg 1": "10000000",
        "Spread-Leg 1": "0.005935",
        "Fixed rate-Leg 1": "0.01",
        "Price": "102.35",
        "Price notation": "PERCENT",
        "Cleared": "N",
        "Platform identifier": "XOFF",
        "Package indicator": "FALSE",
    }
    rec.update(fields or {})
    return rec


def rates_record(did: str, action: str = "NEWT", orig: str | None = None, ts: str = "2026-10-01T14:00:00Z",
                 date: str = "2026-10-01", fields: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """One synthetic USD SOFR OIS swap record with every sdr-dedup output column filled."""
    rec: dict[str, str | None] = {
        "date": date,
        DID: did,
        ODID: orig,
        "Action type": action,
        "Event type": "TRAD",
        "Event timestamp": ts,
        "Execution Timestamp": ts,
        "Effective Date": "2026-10-05",
        "Expiration Date": "2031-10-05",
        "Cleared": "Y",
        "Platform identifier": "XOFF",
        "Package indicator": "FALSE",
        "Non-standardized term indicator": "FALSE",
        "Block trade election indicator": "FALSE",
        "UPI FISN": "NA/O Fix Flt OIS USD",
        "UPI Underlier Name": "USD-SOFR-OIS Compound",
        "Notional currency-Leg 1": "USD",
        "Notional amount-Leg 1": "100000000",
        "Fixed rate-Leg 1": "0.0375",
        "Fixed rate-Leg 2": None,
        "Spread-Leg 1": None,
        "Spread-Leg 2": None,
        "Fixed rate payment frequency period-Leg 1": "1Y",
        "Fixed rate payment frequency period-Leg 2": "1Y",
    }
    rec.update(fields or {})
    return rec


def frame(records: Iterable[dict[str, Any]], columns: Iterable[str] = SPEC_CREDIT_COLUMNS) -> pd.DataFrame:
    """A frame with exactly `columns` (an empty frame when there are no records)."""
    return pd.DataFrame(list(records), columns=list(columns))


def cell(value: Any) -> str:
    """Canonical string of one cell: missing (None/NaN/NA/'') becomes ''. Never truth-tests NA."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def is_blank(value: Any) -> bool:
    """True for None, NaN, pd.NA and ''."""
    return cell(value) == ""


def canon(df: Any, columns: Iterable[str]) -> list[tuple[str, ...]]:
    """Order-insensitive canonical form: rows as string tuples over `columns`, sorted.

    Fails the test when `df` is not a frame or lacks one of `columns`.
    """
    cols = list(columns)
    assert isinstance(df, pd.DataFrame), f"expected a DataFrame, got {type(df).__name__}"
    missing = [c for c in cols if c not in df.columns]
    assert not missing, f"columns missing from result: {missing}"
    rows = [tuple(cell(v) for v in row) for row in df[cols].itertuples(index=False, name=None)]
    return sorted(rows)


def ids(df: Any) -> list[str]:
    """Sorted identifiers in `df` (duplicates kept, so a row-count error shows)."""
    assert isinstance(df, pd.DataFrame), f"expected a DataFrame, got {type(df).__name__}"
    assert DID in df.columns, f"{DID!r} missing from result columns {list(df.columns)}"
    return sorted(cell(v) for v in df[DID])


@pytest.fixture(scope="session")
def cr() -> SimpleNamespace:
    """Record builders and order-insensitive comparison helpers."""
    return SimpleNamespace(credit_record=credit_record, rates_record=rates_record, frame=frame, canon=canon,
                           ids=ids, cell=cell, is_blank=is_blank, CREDIT_COLUMNS=SPEC_CREDIT_COLUMNS,
                           RATES_COLUMNS=SPEC_RATES_COLUMNS, DID=DID, ODID=ODID)
