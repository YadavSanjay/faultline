"""sdr-dedup C7: write_sdr_records / read_sdr_records keep identifiers as exact strings."""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.rates.sdr_records import read_sdr_records, write_sdr_records

DID = "Dissemination Identifier"
ODID = "Original Dissemination Identifier"


def _assert_ids_round_trip(back, expected_pairs):
    """`back` holds exactly `expected_pairs` [(did, odid|None)] with exact strings / missing blanks."""
    assert isinstance(back, pd.DataFrame)
    assert len(back) == len(expected_pairs)
    got = []
    for did, odid in zip(back[DID], back[ODID], strict=True):
        assert isinstance(did, str), f"identifier read back as {type(did).__name__}: {did!r}"
        if odid is None or (not isinstance(odid, str) and pd.isna(odid)):
            got.append((did, None))
        else:
            assert isinstance(odid, str), f"original identifier read back as {type(odid).__name__}: {odid!r}"
            got.append((did, odid))
    assert sorted(got, key=str) == sorted(expected_pairs, key=str)


@pytest.mark.criterion("C7")
def test_c7_literal_identifiers_round_trip(sdr, tmp_path):
    # oracle: the literal strings below. 2^53 = 9007199254740992; every value here is a 19-digit integer
    # far above it (two above 2^63 - 1 = 9223372036854775807), so a float/int64 parse would corrupt them.
    pairs = [
        ("1000000000000000001", None),
        ("9007199254740993001", "1000000000000000001"),
        ("9223372036854775809", None),
        ("9999999999999999999", "9223372036854775809"),
        ("1234567890123456789", None),
    ]
    df = sdr.frame(sdr.record(did, orig=odid, ts=f"2026-10-01T1{k}:00:00Z") for k, (did, odid) in enumerate(pairs))
    path = tmp_path / "dtcc_sdr_irs.csv"
    write_sdr_records(df, path)
    back = read_sdr_records(path)
    _assert_ids_round_trip(back, pairs)
    assert all(v != "nan" for v in back[ODID])  # blanks are missing values, not the string "nan"


@pytest.mark.criterion("C7")
@given(rows=st.lists(
    st.tuples(st.integers(min_value=10**18, max_value=10**19 - 1),
              st.none() | st.integers(min_value=10**18, max_value=10**19 - 1)),
    min_size=1, max_size=20, unique_by=lambda r: r[0]))
def test_c7_property_any_19_digit_identifier_round_trips(sdr, tmp_path_factory, rows):
    # oracle: the generated integers' own decimal strings, over [10^18, 10^19 - 1]; blanks stay missing.
    pairs = [(str(d), None if o is None else str(o)) for d, o in rows]
    df = sdr.frame(sdr.record(did, orig=odid) for did, odid in pairs)
    path = tmp_path_factory.mktemp("sdr") / "records.csv"
    write_sdr_records(df, str(path))
    back = read_sdr_records(str(path))
    _assert_ids_round_trip(back, pairs)
