"""signoff-legacy-seq C2: the sequence number for the next sign-off record.

Oracle: Requirement 2 by hand: one more than the largest ``seq`` among sequenced records, or 0 when there are
none (empty frame, no ``seq`` column, only legacy rows). Properties: appending a legacy record doesn't change
it; appending a record numbered ``next_seq(df)`` raises it by exactly 1.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.signoff import next_seq

SCHEMA = ["scenario_version", "scenario", "status", "approver", "recorded_at", "note"]
NAN = float("nan")


def _row(i: int, seq: object | None = None) -> dict[str, object]:
    row: dict[str, object] = {"scenario_version": f"v{i}", "scenario": "ai_bust", "status": "approved",
                              "approver": "analyst", "recorded_at": f"2026-06-{i % 28 + 1:02d}T09:00:00+00:00",
                              "note": ""}
    if seq is not None:
        row["seq"] = seq
    return row


def _with_seq(seqs: list[object]) -> pd.DataFrame:
    return pd.DataFrame([_row(i, s) for i, s in enumerate(seqs)], columns=[*SCHEMA, "seq"])


@pytest.mark.criterion("C2")
def test_c2_point_max_plus_one_over_sequenced_records():
    # oracle: hand calculation: max(0, 1, 2) + 1 = 3; max(0, 5, 2) + 1 = 6 (the max, not the count);
    # float seq with NaN legacy rows: max(0, 4) + 1 = 5; a single seq 0 gives 1.
    got = next_seq(_with_seq([0, 1, 2]))
    assert got == 3
    assert isinstance(got, int)
    assert next_seq(_with_seq([0, 5, 2])) == 6
    got_float = next_seq(_with_seq([0.0, NAN, 4.0, NAN]))
    assert got_float == 5
    assert isinstance(got_float, int)
    assert next_seq(_with_seq([0])) == 1


@pytest.mark.criterion("C2")
def test_c2_point_zero_when_no_sequenced_records():
    # oracle: Requirement 2: 0 with no sequenced records (empty with and without a seq column, no seq column,
    # only legacy NaN rows); checked next to a frame whose answer is 8 (max 7 + 1) by hand.
    assert next_seq(_with_seq([7.0, NAN])) == 8
    empty_with = pd.DataFrame({c: pd.Series([], dtype="object") for c in [*SCHEMA, "seq"]})
    empty_without = pd.DataFrame({c: pd.Series([], dtype="object") for c in SCHEMA})
    absent = pd.DataFrame([_row(0), _row(1)], columns=SCHEMA)
    legacy_only = _with_seq([NAN, NAN])
    for df in (empty_with, empty_without, absent, legacy_only):
        got = next_seq(df)
        assert got == 0
        assert isinstance(got, int)


@pytest.mark.criterion("C2")
@given(seqs=st.lists(st.one_of(st.none(), st.integers(min_value=0, max_value=10_000)), min_size=0, max_size=15))
def test_c2_property_hand_max_and_legacy_append(seqs: list[int | None]):
    # oracle: C2 by hand in the test (max over the non-missing seq + 1, else 0) and the property that appending
    # a legacy record (NaN seq, or no seq column at all) doesn't change it.
    sequenced = [s for s in seqs if s is not None]
    want = max(sequenced) + 1 if sequenced else 0
    values: list[object] = [NAN if s is None else float(s) for s in seqs]
    assert next_seq(_with_seq(values)) == want
    appended = _with_seq([*values, NAN])  # the same rows plus one legacy record at the end
    assert next_seq(appended) == want
    if not sequenced:
        absent = pd.DataFrame([_row(i) for i in range(len(seqs) + 1)], columns=SCHEMA)
        assert next_seq(absent) == 0


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    seqs=st.lists(st.one_of(st.none(), st.integers(min_value=0, max_value=500)), min_size=0, max_size=12),
    as_float=st.booleans(),
    steps=st.integers(min_value=1, max_value=4),
)
def test_c2_metamorphic_appending_next_seq_raises_by_one(seqs: list[int | None], as_float: bool, steps: int):
    # oracle: C2 property: appending a record with seq = next_seq(df) raises next_seq by exactly 1, each time.
    has_legacy = any(s is None for s in seqs)
    floats = as_float or has_legacy
    values: list[object] = [NAN if s is None else (float(s) if floats else s) for s in seqs]
    for _ in range(steps):
        n = next_seq(_with_seq(values))
        values = [*values, float(n) if floats else n]  # append a record numbered next_seq(df)
        assert next_seq(_with_seq(values)) == n + 1
