"""signoff-legacy-seq C1: sign-off records ordered newest first, legacy records (no ``seq``) last.

Oracle: Requirement 1 by hand: a record without a ``seq`` value gets ``seq = -1``; records are ordered by
``seq`` descending, then ``recorded_at`` descending as text; every other column and value is kept, the
index is reset and the input is left alone. Properties: the output is a permutation of the input rows, its
``seq`` is non-increasing, and shuffling the input doesn't change it when ``(seq, recorded_at)`` is distinct.
"""

from __future__ import annotations

import math
from itertools import pairwise

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.signoff import order_records

SCHEMA = ["scenario_version", "scenario", "status", "approver", "recorded_at", "note"]


def _ts(day: int, hour: int = 9) -> str:
    return f"2026-06-{day:02d}T{hour:02d}:00:00.000000+00:00"


def _frame(rows: list[dict[str, object]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def _row(version: str, recorded_at: str, status: str = "approved", note: str = "") -> dict[str, object]:
    return {"scenario_version": version, "scenario": "ai_bust", "status": status, "approver": "analyst",
            "recorded_at": recorded_at, "note": note or version}


def _assert_seq_integer(out: pd.DataFrame) -> None:
    assert "seq" in out.columns
    assert pd.api.types.is_integer_dtype(out["seq"])
    assert not pd.api.types.is_bool_dtype(out["seq"])


def _assert_range_index(out: pd.DataFrame) -> None:
    assert list(out.index) == list(range(len(out)))


@pytest.mark.criterion("C1")
def test_c1_point_union_read_float_seq_with_nan_legacy_rows():
    # oracle: Requirement 1 by hand. A duckdb union_by_name read gives seq as float with NaN for the legacy
    # rows. Sequenced: v2 (seq 2), v4 (seq 1), v0 (seq 0) -> in that order; legacy v1 (06-03) and v3 (06-20)
    # follow, newest recorded_at first: v3, v1. Legacy seq becomes -1.
    rows = [
        {**_row("v0", _ts(10)), "seq": 0.0},
        {**_row("v1", _ts(3)), "seq": float("nan")},
        {**_row("v2", _ts(12)), "seq": 2.0},
        {**_row("v3", _ts(20)), "seq": float("nan")},
        {**_row("v4", _ts(11), status="rejected"), "seq": 1.0},
    ]
    df = _frame(rows, [*SCHEMA, "seq"])
    out = order_records(df)
    assert list(out["scenario_version"]) == ["v2", "v4", "v0", "v3", "v1"]
    assert list(out["seq"]) == [2, 1, 0, -1, -1]
    _assert_seq_integer(out)
    _assert_range_index(out)
    assert list(out["status"]) == ["approved", "rejected", "approved", "approved", "approved"]
    assert list(out["recorded_at"]) == [_ts(12), _ts(11), _ts(10), _ts(20), _ts(3)]
    assert list(out["note"]) == ["v2", "v4", "v0", "v3", "v1"]
    assert set(SCHEMA) <= set(out.columns)


@pytest.mark.criterion("C1")
def test_c1_point_seq_column_absent():
    # oracle: Requirement 1 by hand: with no seq column every record is legacy (seq -1), so the order is
    # recorded_at descending as text: 06-21 > 06-05 15:00 > 06-05 09:00 > 06-01.
    rows = [_row("a", _ts(5, 9)), _row("b", _ts(21)), _row("c", _ts(1)), _row("d", _ts(5, 15))]
    df = _frame(rows, SCHEMA)
    out = order_records(df)
    assert list(out["scenario_version"]) == ["b", "d", "a", "c"]
    assert list(out["seq"]) == [-1, -1, -1, -1]
    _assert_seq_integer(out)
    _assert_range_index(out)
    for col in SCHEMA:
        assert sorted(out[col]) == sorted(df[col])


@pytest.mark.criterion("C1")
def test_c1_point_integer_seq_ties_broken_by_recorded_at_text():
    # oracle: Requirement 1 by hand: seq descending first (3 before 1), and within seq 3 (and within seq 1)
    # recorded_at descending compared as text: "2026-06-30..." > "2026-06-09...".
    rows = [
        {**_row("x", _ts(9)), "seq": 3},
        {**_row("y", _ts(30)), "seq": 1},
        {**_row("z", _ts(30)), "seq": 3},
        {**_row("w", _ts(9)), "seq": 1},
    ]
    df = _frame(rows, [*SCHEMA, "seq"])
    out = order_records(df)
    assert list(out["scenario_version"]) == ["z", "x", "y", "w"]
    assert list(out["seq"]) == [3, 3, 1, 1]
    _assert_seq_integer(out)
    _assert_range_index(out)


@pytest.mark.criterion("C1")
def test_c1_point_index_reset_from_non_default_index():
    # oracle: Requirement 1: the index is reset to 0..n-1 whatever the input index. Order by hand: seq 5, then
    # legacy (NaN) row.
    rows = [{**_row("old", _ts(2)), "seq": float("nan")}, {**_row("new", _ts(28)), "seq": 5.0}]
    df = _frame(rows, [*SCHEMA, "seq"])
    df.index = pd.Index([17, 4])
    out = order_records(df)
    assert list(out["scenario_version"]) == ["new", "old"]
    assert list(out["seq"]) == [5, -1]
    _assert_range_index(out)
    _assert_seq_integer(out)


@pytest.mark.criterion("C1")
def test_c1_point_input_not_modified():
    # oracle: Requirement 1: the input is not modified (compared with a deep copy taken before the call);
    # the order by hand is seq 1, then the legacy row.
    rows = [{**_row("legacy", _ts(4)), "seq": float("nan")}, {**_row("seq1", _ts(14)), "seq": 1.0}]
    df = _frame(rows, [*SCHEMA, "seq"])
    df.index = pd.Index([7, 3])
    before = df.copy(deep=True)
    out = order_records(df)
    assert list(out["scenario_version"]) == ["seq1", "legacy"]
    pd.testing.assert_frame_equal(df, before)

    absent = _frame([_row("p", _ts(1)), _row("q", _ts(2))], SCHEMA)
    before_absent = absent.copy(deep=True)
    out_absent = order_records(absent)
    assert list(out_absent["scenario_version"]) == ["q", "p"]
    pd.testing.assert_frame_equal(absent, before_absent)
    assert "seq" not in absent.columns


@pytest.mark.criterion("C1")
def test_c1_point_empty_frames():
    # oracle: Requirement 1: an empty frame, with or without a seq column, returns an empty frame with a seq
    # column (the input's other columns kept).
    for columns in ([*SCHEMA, "seq"], SCHEMA):
        df = pd.DataFrame({c: pd.Series([], dtype="object") for c in columns})
        out = order_records(df)
        assert isinstance(out, pd.DataFrame)
        assert len(out) == 0
        assert "seq" in out.columns
        assert set(SCHEMA) <= set(out.columns)


# One generated record: (seq or None for legacy, index into a pool of recorded_at strings).
_RECORD = st.tuples(st.one_of(st.none(), st.integers(min_value=0, max_value=6)), st.integers(min_value=0, max_value=9))
_POOL = [_ts(d, h) for d, h in [(1, 9), (1, 17), (2, 8), (9, 9), (10, 9), (10, 23), (19, 0), (20, 12), (29, 6),
                                (30, 18)]]


def _build(records: list[tuple[int | None, int]], layout: str) -> pd.DataFrame:
    rows = []
    for i, (seq, ts) in enumerate(records):
        row = _row(f"v{i}", _POOL[ts], status="approved" if i % 2 == 0 else "rejected", note=f"n{i}")
        if layout != "absent":
            row["seq"] = float("nan") if seq is None else (float(seq) if layout == "float" else seq)
        rows.append(row)
    columns = SCHEMA if layout == "absent" else [*SCHEMA, "seq"]
    return _frame(rows, columns)


def _layout(records: list[tuple[int | None, int]], choice: int) -> str:
    if all(seq is None for seq, _ in records) and choice == 0:
        return "absent"
    if any(seq is None for seq, _ in records):
        return "float"
    return "float" if choice == 1 else "int"


def _expected_seq(records: list[tuple[int | None, int]], layout: str) -> list[int]:
    return [-1 if (layout == "absent" or seq is None) else seq for seq, _ in records]


@pytest.mark.criterion("C1")
@given(records=st.lists(_RECORD, min_size=1, max_size=12), choice=st.integers(min_value=0, max_value=2))
def test_c1_property_permutation_and_non_increasing_seq(records: list[tuple[int | None, int]], choice: int):
    # oracle: C1 properties: the output rows are a permutation of the input rows (every original column; the
    # seq column compared with legacy values read as -1), seq is non-increasing, and within one seq value
    # recorded_at is non-increasing as text; the full order also matches a sort key computed in the test.
    layout = _layout(records, choice)
    df = _build(records, layout)
    out = order_records(df)
    _assert_seq_integer(out)
    _assert_range_index(out)
    assert len(out) == len(df)

    seqs = _expected_seq(records, layout)
    want = sorted((*(df.iloc[i][c] for c in SCHEMA), seqs[i]) for i in range(len(df)))
    got = sorted((*(out.iloc[i][c] for c in SCHEMA), int(out.iloc[i]["seq"])) for i in range(len(out)))
    assert got == want

    out_seq = [int(s) for s in out["seq"]]
    assert all(a >= b for a, b in pairwise(out_seq))
    keys = list(zip(out_seq, out["recorded_at"], strict=True))
    assert all(a >= b for a, b in pairwise(keys))
    order = sorted(range(len(records)), key=lambda i: (seqs[i], _POOL[records[i][1]]), reverse=True)
    assert keys == [(seqs[i], _POOL[records[i][1]]) for i in order]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    records=st.lists(_RECORD, min_size=1, max_size=12, unique_by=lambda r: (-1 if r[0] is None else r[0], r[1])),
    choice=st.integers(min_value=0, max_value=2),
    data=st.data(),
)
def test_c1_metamorphic_shuffle_invariant(records: list[tuple[int | None, int]], choice: int,
                                          data: st.DataObject):
    # oracle: C1 property: with distinct (seq, recorded_at) pairs the order is total, so permuting the input
    # rows (and its index) gives the identical output frame.
    layout = _layout(records, choice)
    df = _build(records, layout)
    perm = data.draw(st.permutations(list(range(len(df)))))
    shuffled = df.iloc[perm]
    base = order_records(df)
    other = order_records(shuffled)
    _assert_seq_integer(base)
    assert len(base) == len(df)
    pd.testing.assert_frame_equal(base, other)
    assert not any(isinstance(s, float) and math.isnan(s) for s in base["seq"])
