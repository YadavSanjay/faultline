"""Synthetic CFTC Part 43 SDR records for the sdr-dedup spec tests.

No DTCC data is committed (oracles.md: DTCC terms). Every record here is made up: identifiers are
19-digit integers held as strings, event timestamps are ISO-8601 UTC strings, rates are decimals
(0.0375 = 3.75%), notionals are plain currency units. Blank fields are ``None``.

Helpers are exposed through the session-scoped ``sdr`` fixture (session scope so hypothesis tests
can use it).
"""

from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

# The output schema exactly as written in docs/specs/sdr-dedup.md (Interface), typed out here so the
# tests do not take their expectations from the module under test.
SPEC_OUTPUT_COLUMNS: tuple[str, ...] = (
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
SPEC_ID_COLUMNS: tuple[str, ...] = ("Dissemination Identifier", "Original Dissemination Identifier")
DID = "Dissemination Identifier"
ODID = "Original Dissemination Identifier"


def record(did: str, action: str = "NEWT", orig: str | None = None, ts: str = "2026-10-01T14:00:00Z",
           date: str = "2026-10-01", fields: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """One synthetic USD SOFR OIS swap record with every output column filled."""
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


def frame(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """A frame with exactly the output columns (an empty frame when there are no records)."""
    return pd.DataFrame(list(records), columns=list(SPEC_OUTPUT_COLUMNS))


def empty() -> pd.DataFrame:
    """An empty record set with the output columns."""
    return frame([])


def _cell(value: Any) -> str:
    """Canonical string of one cell: missing (None/NaN/NA/'') becomes ''."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def canon(df: pd.DataFrame, columns: Iterable[str] = SPEC_OUTPUT_COLUMNS) -> list[tuple[str, ...]]:
    """Order-insensitive canonical form: rows as string tuples over `columns`, sorted by identifier.

    Raises (fails the test) when `df` is not a frame or lacks one of `columns`.
    """
    cols = list(columns)
    assert isinstance(df, pd.DataFrame), f"expected a DataFrame, got {type(df).__name__}"
    missing = [c for c in cols if c not in df.columns]
    assert not missing, f"columns missing from result: {missing}"
    rows = [tuple(_cell(v) for v in row) for row in df[cols].itertuples(index=False, name=None)]
    return sorted(rows)


def ids(df: pd.DataFrame) -> list[str]:
    """Sorted identifiers in `df` (duplicates kept, so a row-count error shows)."""
    assert isinstance(df, pd.DataFrame), f"expected a DataFrame, got {type(df).__name__}"
    return sorted(_cell(v) for v in df[DID])


@pytest.fixture(scope="session")
def sdr() -> SimpleNamespace:
    """Record builders and order-insensitive comparison helpers."""
    return SimpleNamespace(record=record, frame=frame, empty=empty, canon=canon, ids=ids, cell=_cell,
                           OUTPUT_COLUMNS=SPEC_OUTPUT_COLUMNS, ID_COLUMNS=SPEC_ID_COLUMNS, DID=DID, ODID=ODID)
