"""panel-gap-carry C1: carry a factor's level (a 0.0 move) across short interior gaps of the move panel.

Oracle: Requirement 1 by hand. A row is eligible when (observed columns / all columns) >= min_coverage;
other rows are removed. On the eligible rows, each column's run of consecutive NaNs with an observed value
before and after it and length <= max_gap becomes exactly 0.0; longer, leading and trailing runs stay NaN.

Every output value is either a copy of an input value, the literal 0.0 or NaN, so all comparisons are
exact and NaN-aware (``pd.testing.assert_frame_equal(check_exact=True)`` treats NaN as equal to NaN); no
arithmetic happens, so no tolerance applies.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.risk.factor_history import carry_short_gaps

NAN = math.nan
COLS = ["A", "B", "C", "D"]
# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

# Hand-built synthetic panel (4 columns, so a row is eligible at min_coverage 0.75 with >= 3 observed).
#   row date        A      B      C      D       note
HAND_ROWS = [
    ("2026-01-05", NAN, -1.0, 2.0, 3.0),    # A leading gap (length 1)          3/4 eligible
    ("2026-01-06", 0.5, NAN, 2.5, 3.5),     # B interior 1-row gap              3/4 eligible
    ("2026-01-07", 0.25, 1.25, NAN, 3.25),  # C gap, 1st eligible row           3/4 eligible
    ("2026-01-08", NAN, NAN, NAN, NAN),     # full holiday                      0/4 removed
    ("2026-01-09", 0.75, 1.5, NAN, 4.0),    # C gap, 2nd eligible row           3/4 eligible
    ("2026-01-12", 1.5, 1.75, NAN, 4.5),    # C gap, 3rd eligible row           3/4 eligible
    ("2026-01-13", 2.0, 2.25, 2.75, 4.25),  # full row                          4/4 eligible
    ("2026-01-14", 2.5, 2.5, 3.0, 4.75),    # full row                          4/4 eligible
    ("2026-01-15", 3.0, 2.75, 3.25, NAN),   # D gap, 1st eligible row           3/4 eligible
    ("2026-01-16", 3.5, -3.0, 3.5, NAN),    # D gap, 2nd eligible row           3/4 eligible
    ("2026-01-17", NAN, NAN, NAN, 8.0),     # Saturday, observed by D only      1/4 removed
    ("2026-01-19", 4.0, 3.25, 3.75, NAN),   # D gap, 3rd eligible row           3/4 eligible
    ("2026-01-20", 4.5, 3.5, 4.0, NAN),     # D gap, 4th eligible row           3/4 eligible
    ("2026-01-21", 5.0, 3.75, 4.25, 5.0),   # full row                          4/4 eligible
    ("2026-01-22", 5.5, 4.0, 4.5, NAN),     # D trailing gap (length 1)         3/4 eligible
]

Row = tuple[str, float, float, float, float]


def frame(rows: list[Row]) -> pd.DataFrame:
    """A float panel from (date, A, B, C, D) tuples, dates as a DatetimeIndex."""
    index = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    return pd.DataFrame([list(r[1:]) for r in rows], index=index, columns=COLS, dtype=float)


def hand_panel() -> pd.DataFrame:
    return frame(HAND_ROWS)


def assert_same(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    # exact, NaN-aware: values are copies or the literal 0.0, so no tolerance applies
    pd.testing.assert_frame_equal(actual, expected, check_exact=True, check_freq=False)


@pytest.mark.criterion("C1")
def test_c1_point_hand_panel_max_gap_3():
    # oracle: Requirement 1 by hand on HAND_ROWS, max_gap 3, min_coverage 0.75:
    #   removed: 01-08 (0/4), 01-17 (1/4). A's leading NaN stays; B 01-06 run 1 -> 0.0;
    #   C run 01-07, 01-09, 01-12 = 3 on the eligible rows (the holiday is not counted) -> 0.0;
    #   D run 01-15, 01-16, 01-19, 01-20 = 4 on the eligible rows (the Saturday's 8.0 is gone) -> NaN;
    #   D's trailing NaN on 01-22 stays.
    expected = frame([
        ("2026-01-05", NAN, -1.0, 2.0, 3.0),
        ("2026-01-06", 0.5, 0.0, 2.5, 3.5),
        ("2026-01-07", 0.25, 1.25, 0.0, 3.25),
        ("2026-01-09", 0.75, 1.5, 0.0, 4.0),
        ("2026-01-12", 1.5, 1.75, 0.0, 4.5),
        ("2026-01-13", 2.0, 2.25, 2.75, 4.25),
        ("2026-01-14", 2.5, 2.5, 3.0, 4.75),
        ("2026-01-15", 3.0, 2.75, 3.25, NAN),
        ("2026-01-16", 3.5, -3.0, 3.5, NAN),
        ("2026-01-19", 4.0, 3.25, 3.75, NAN),
        ("2026-01-20", 4.5, 3.5, 4.0, NAN),
        ("2026-01-21", 5.0, 3.75, 4.25, 5.0),
        ("2026-01-22", 5.5, 4.0, 4.5, NAN),
    ])
    panel = hand_panel()
    out = carry_short_gaps(panel, max_gap=3, min_coverage=0.75)
    assert_same(out, expected)
    assert_same(panel, hand_panel())  # the input is not mutated


@pytest.mark.criterion("C1")
def test_c1_point_hand_panel_other_max_gaps():
    # oracle: Requirement 1 by hand on HAND_ROWS, min_coverage 0.75:
    #   max_gap 2: B's 1-run -> 0.0; C's 3-run and D's 4-run stay NaN.
    #   max_gap 4: B, C and D's 4-run all -> 0.0; A's leading and D's trailing NaN stay.
    two = frame([
        ("2026-01-05", NAN, -1.0, 2.0, 3.0),
        ("2026-01-06", 0.5, 0.0, 2.5, 3.5),
        ("2026-01-07", 0.25, 1.25, NAN, 3.25),
        ("2026-01-09", 0.75, 1.5, NAN, 4.0),
        ("2026-01-12", 1.5, 1.75, NAN, 4.5),
        ("2026-01-13", 2.0, 2.25, 2.75, 4.25),
        ("2026-01-14", 2.5, 2.5, 3.0, 4.75),
        ("2026-01-15", 3.0, 2.75, 3.25, NAN),
        ("2026-01-16", 3.5, -3.0, 3.5, NAN),
        ("2026-01-19", 4.0, 3.25, 3.75, NAN),
        ("2026-01-20", 4.5, 3.5, 4.0, NAN),
        ("2026-01-21", 5.0, 3.75, 4.25, 5.0),
        ("2026-01-22", 5.5, 4.0, 4.5, NAN),
    ])
    four = frame([
        ("2026-01-05", NAN, -1.0, 2.0, 3.0),
        ("2026-01-06", 0.5, 0.0, 2.5, 3.5),
        ("2026-01-07", 0.25, 1.25, 0.0, 3.25),
        ("2026-01-09", 0.75, 1.5, 0.0, 4.0),
        ("2026-01-12", 1.5, 1.75, 0.0, 4.5),
        ("2026-01-13", 2.0, 2.25, 2.75, 4.25),
        ("2026-01-14", 2.5, 2.5, 3.0, 4.75),
        ("2026-01-15", 3.0, 2.75, 3.25, 0.0),
        ("2026-01-16", 3.5, -3.0, 3.5, 0.0),
        ("2026-01-19", 4.0, 3.25, 3.75, 0.0),
        ("2026-01-20", 4.5, 3.5, 4.0, 0.0),
        ("2026-01-21", 5.0, 3.75, 4.25, 5.0),
        ("2026-01-22", 5.5, 4.0, 4.5, NAN),
    ])
    assert_same(carry_short_gaps(hand_panel(), max_gap=2, min_coverage=0.75), two)
    assert_same(carry_short_gaps(hand_panel(), max_gap=4, min_coverage=0.75), four)


@pytest.mark.criterion("C1")
def test_c1_point_max_gap_zero_and_empty_panel():
    # oracle: Requirement 1: max_gap 0 fills nothing, so the result is HAND_ROWS minus the two
    #   ineligible rows (01-08 holiday, 01-17 Saturday), every other cell unchanged (NaNs included).
    #   An empty panel (no rows) comes back as is.
    expected = frame([r for r in HAND_ROWS if r[0] not in ("2026-01-08", "2026-01-17")])
    out = carry_short_gaps(hand_panel(), max_gap=0, min_coverage=0.75)
    assert_same(out, expected)
    assert len(out) == 13
    empty = pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([]), dtype=float)
    assert_same(carry_short_gaps(empty, max_gap=3, min_coverage=0.75), empty)


@pytest.mark.criterion("C1")
def test_c1_point_min_coverage_changes_eligibility():
    # oracle: Requirement 1 by hand on HAND_ROWS, max_gap 3:
    #   min_coverage 1.0 keeps only the full rows 01-13, 01-14, 01-21 (nothing to fill).
    #   min_coverage 0.25 also keeps the Saturday (1/4 >= 0.25), not the holiday (0/4). Then
    #   D's run splits into 01-15..01-16 (2) and 01-19..01-20 (2) around the Saturday's 8.0 -> both 0.0;
    #   A, B, C on the Saturday are interior 1-runs -> 0.0.
    full_only = frame([r for r in HAND_ROWS if r[0] in ("2026-01-13", "2026-01-14", "2026-01-21")])
    assert_same(carry_short_gaps(hand_panel(), max_gap=3, min_coverage=1.0), full_only)
    low = frame([
        ("2026-01-05", NAN, -1.0, 2.0, 3.0),
        ("2026-01-06", 0.5, 0.0, 2.5, 3.5),
        ("2026-01-07", 0.25, 1.25, 0.0, 3.25),
        ("2026-01-09", 0.75, 1.5, 0.0, 4.0),
        ("2026-01-12", 1.5, 1.75, 0.0, 4.5),
        ("2026-01-13", 2.0, 2.25, 2.75, 4.25),
        ("2026-01-14", 2.5, 2.5, 3.0, 4.75),
        ("2026-01-15", 3.0, 2.75, 3.25, 0.0),
        ("2026-01-16", 3.5, -3.0, 3.5, 0.0),
        ("2026-01-17", 0.0, 0.0, 0.0, 8.0),
        ("2026-01-19", 4.0, 3.25, 3.75, 0.0),
        ("2026-01-20", 4.5, 3.5, 4.0, 0.0),
        ("2026-01-21", 5.0, 3.75, 4.25, 5.0),
        ("2026-01-22", 5.5, 4.0, 4.5, NAN),
    ])
    assert_same(carry_short_gaps(hand_panel(), max_gap=3, min_coverage=0.25), low)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("max_gap", "min_coverage"), [(-1, 0.75), (3, 0.0), (3, 1.5)])
def test_c1_point_rejects_bad_parameters(max_gap, min_coverage):
    # oracle: Requirement 1: ValueError when max_gap < 0 or min_coverage is not in (0, 1].
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        carry_short_gaps(hand_panel(), max_gap=max_gap, min_coverage=min_coverage)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("kind", ["decreasing", "duplicate"])
def test_c1_point_rejects_non_increasing_index(kind):
    # oracle: Requirement 1: ValueError when the index is not strictly increasing
    #   (rows reversed, or one date repeated).
    reversed_rows = hand_panel().iloc[::-1]
    repeated_date = frame([*HAND_ROWS[:2], HAND_ROWS[1], *HAND_ROWS[2:]])
    panel = reversed_rows if kind == "decreasing" else repeated_date
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        carry_short_gaps(panel, max_gap=3, min_coverage=0.75)


# ---------------------------------------------------------------- properties

CELL = st.one_of(st.just(NAN), st.integers(-8, 8).map(lambda k: k / 4.0))
COVERAGE = st.sampled_from([0.2, 0.25, 0.5, 0.6, 0.75, 0.8, 1.0])


@st.composite
def panels(draw: st.DrawFn) -> pd.DataFrame:
    n_rows = draw(st.integers(1, 14))
    n_cols = draw(st.integers(1, 5))
    cells = draw(st.lists(st.lists(CELL, min_size=n_cols, max_size=n_cols), min_size=n_rows, max_size=n_rows))
    index = pd.date_range("2026-01-01", periods=n_rows, freq="D")
    return pd.DataFrame(cells, index=index, columns=[f"f{j}" for j in range(n_cols)], dtype=float)


def eligible_index(panel: pd.DataFrame, min_coverage: float) -> list[pd.Timestamp]:
    """Requirement 1 eligibility by hand: observed share of columns >= min_coverage."""
    n = panel.shape[1]
    return [ts for ts, row in panel.iterrows() if int(row.notna().sum()) / n >= min_coverage]


def interior_run_length(column: list[float], i: int) -> int | None:
    """Length of the NaN run through position i if it is interior, else None (leading/trailing)."""
    lo = i
    while lo > 0 and math.isnan(column[lo - 1]):
        lo -= 1
    hi = i
    while hi < len(column) - 1 and math.isnan(column[hi + 1]):
        hi += 1
    if lo == 0 or hi == len(column) - 1:
        return None
    return hi - lo + 1


def filled_cells(panel: pd.DataFrame, out: pd.DataFrame) -> set[tuple[pd.Timestamp, str]]:
    """Cells that were NaN in the input and are numbers in the output."""
    sub = panel.loc[out.index]
    return {(ts, c) for c in out.columns for ts in out.index
            if math.isnan(sub.loc[ts, c]) and not math.isnan(out.loc[ts, c])}


@pytest.mark.criterion("C1")
@given(panel=panels(), max_gap=st.integers(0, 5), min_coverage=COVERAGE)
def test_c1_property_rows_values_and_fills(panel, max_gap, min_coverage):
    # oracle: Requirement 1 invariants: output rows = the eligible input rows in input order, columns
    #   unchanged; every observed value of an eligible row is unchanged; a NaN becomes exactly 0.0 iff it
    #   sits in an interior NaN run (on the eligible rows) of length <= max_gap; the input is not mutated.
    before = panel.copy()
    out = carry_short_gaps(panel, max_gap=max_gap, min_coverage=min_coverage)
    assert_same(panel, before)
    assert list(out.columns) == list(panel.columns)
    assert list(out.index) == eligible_index(panel, min_coverage)
    sub = panel.loc[out.index]
    for c in out.columns:
        column = [float(v) for v in sub[c]]
        for i, ts in enumerate(out.index):
            got = float(out.loc[ts, c])
            if not math.isnan(column[i]):
                # abs 0: exact, an observed value is copied, not recomputed
                assert got == pytest.approx(column[i], abs=0.0)
                continue
            run = interior_run_length(column, i)
            if run is not None and run <= max_gap:
                # abs 0: exact, the fill is the literal 0.0
                assert got == pytest.approx(0.0, abs=0.0)
            else:
                assert math.isnan(got)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(panel=panels(), max_gap=st.integers(0, 4), extra=st.integers(1, 3), min_coverage=COVERAGE)
def test_c1_metamorphic_raising_max_gap_never_unfills(panel, max_gap, extra, min_coverage):
    # oracle: Requirement 1: rows do not depend on max_gap, and a run filled at g is filled at any g' > g,
    #   so the filled set only grows; every filled cell is exactly 0.0.
    low = carry_short_gaps(panel, max_gap=max_gap, min_coverage=min_coverage)
    high = carry_short_gaps(panel, max_gap=max_gap + extra, min_coverage=min_coverage)
    assert list(low.index) == list(high.index)
    assert list(low.index) == eligible_index(panel, min_coverage)
    low_filled = filled_cells(panel, low)
    assert low_filled <= filled_cells(panel, high)
    for ts, c in low_filled:
        # abs 0: exact, the fill is the literal 0.0
        assert float(low.loc[ts, c]) == pytest.approx(0.0, abs=0.0)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(panel=panels(), max_gap=st.integers(0, 4), pair=st.lists(COVERAGE, min_size=2, max_size=2))
def test_c1_metamorphic_raising_min_coverage_never_adds_rows(panel, max_gap, pair):
    # oracle: Requirement 1: a row eligible at the higher min_coverage is eligible at the lower one,
    #   and both outputs keep input order (subsequences of the input index).
    lo_cov, hi_cov = sorted(pair)
    lo = carry_short_gaps(panel, max_gap=max_gap, min_coverage=lo_cov)
    hi = carry_short_gaps(panel, max_gap=max_gap, min_coverage=hi_cov)
    assert set(hi.index) <= set(lo.index)
    assert list(lo.index) == eligible_index(panel, lo_cov)
    for out in (lo, hi):
        kept = set(out.index)
        assert list(out.index) == [ts for ts in panel.index if ts in kept]
