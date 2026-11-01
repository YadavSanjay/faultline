"""var-panel-window C1-C3: put per-factor P&L columns on a date window, carrying short interior gaps.

Oracle: Requirement 1 by hand. The result is built on the window rows; a column's value on a date outside
the window is not used. Per column, a run of consecutive missing window rows of length <= max_gap with a
value before and after it in the window becomes exactly 0.0; longer, leading and trailing runs stay
missing, and every row still missing a value is removed, keeping window order.

Every output value is a copy of an input value or the literal 0.0, so all comparisons are exact
(``pd.testing.assert_frame_equal(check_exact=True)``); no arithmetic happens, so no tolerance applies.
``check_freq=False`` because the spec fixes the dates, not whether the index carries a frequency.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.risk.var import align_to_window

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

# Hand-built window: 12 business days (the weekend 01-10/01-11 and 01-17/01-18 are not window rows).
WINDOW_DATES = [
    "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12",
    "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16", "2026-01-19", "2026-01-20",
]

# Columns as (date, value) pairs; a window date that is absent is a missing value.
#   pos date        Z        A                    M
#   0   01-05       1.0      -  leading gap       2.5
#   1   01-06      -2.0      0.5                 -3.0
#   2   01-07       3.5      -  1-row gap         0.75
#   3   01-08       0.25    -1.5                  1.25
#   4   01-09      -0.75     -  2-row gap        -0.25
#   5   01-12       2.0      -  2-row gap         1.0
#   6   01-13       1.5      3.0                 -2.5
#   7   01-14      -1.25    -0.5                  -  3-row gap
#   8   01-15       0.5      1.75                 -  3-row gap
#   9   01-16       4.0     -1.0                  -  3-row gap
#   10  01-19      -0.375    0.25                 0.125
#   11  01-20       0.625    2.0                  -  trailing gap
# Outside the window (all ignored): Z 01-02 -50.0, 01-10 99.0, 01-21 7.0;
#   A 01-02 5.0 (before its leading gap), 01-10 9.0 (a Saturday inside its 2-row gap); M 01-21 6.0
#   (after its trailing gap).
Z_POINTS = [
    ("2026-01-02", -50.0), ("2026-01-05", 1.0), ("2026-01-06", -2.0), ("2026-01-07", 3.5),
    ("2026-01-08", 0.25), ("2026-01-09", -0.75), ("2026-01-10", 99.0), ("2026-01-12", 2.0),
    ("2026-01-13", 1.5), ("2026-01-14", -1.25), ("2026-01-15", 0.5), ("2026-01-16", 4.0),
    ("2026-01-19", -0.375), ("2026-01-20", 0.625), ("2026-01-21", 7.0),
]
A_POINTS = [
    ("2026-01-02", 5.0), ("2026-01-06", 0.5), ("2026-01-08", -1.5), ("2026-01-10", 9.0),
    ("2026-01-13", 3.0), ("2026-01-14", -0.5), ("2026-01-15", 1.75), ("2026-01-16", -1.0),
    ("2026-01-19", 0.25), ("2026-01-20", 2.0),
]
M_POINTS = [
    ("2026-01-05", 2.5), ("2026-01-06", -3.0), ("2026-01-07", 0.75), ("2026-01-08", 1.25),
    ("2026-01-09", -0.25), ("2026-01-12", 1.0), ("2026-01-13", -2.5), ("2026-01-19", 0.125),
    ("2026-01-21", 6.0),
]
# Deliberately not alphabetical: the result's column order comes from cols.
HAND_ORDER = ["Z", "A", "M"]

Row = tuple[str, float, float, float]


def series(points: list[tuple[str, float]]) -> pd.Series:
    return pd.Series([v for _, v in points], index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in points]),
                     dtype=float)


def window_of(dates: list[str]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d) for d in dates])


def hand_cols() -> dict[str, pd.Series]:
    return {"Z": series(Z_POINTS), "A": series(A_POINTS), "M": series(M_POINTS)}


def frame(rows: list[Row]) -> pd.DataFrame:
    """A float frame from (date, Z, A, M) tuples, dates as a DatetimeIndex."""
    index = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    return pd.DataFrame([list(r[1:]) for r in rows], index=index, columns=HAND_ORDER, dtype=float)


def assert_same(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    # exact: values are copies or the literal 0.0, so no tolerance applies
    pd.testing.assert_frame_equal(actual, expected, check_exact=True, check_freq=False)


# ---------------------------------------------------------------- C1 points


@pytest.mark.criterion("C1")
def test_c1_point_hand_case_max_gap_2():
    # oracle: Requirement 1 by hand on the table above, max_gap 2:
    #   A's 1-row gap (01-07) and 2-row gap (01-09, 01-12) -> 0.0 (A's 01-10 value is outside the window);
    #   A's leading gap (01-05) stays missing despite A's 01-02 value -> row removed;
    #   M's 3-row gap (01-14..01-16) stays missing -> rows removed; M's trailing gap (01-20) stays
    #   missing despite M's 01-21 value -> row removed; Z's outside-window values are not used.
    expected = frame([
        ("2026-01-06", -2.0, 0.5, -3.0),
        ("2026-01-07", 3.5, 0.0, 0.75),
        ("2026-01-08", 0.25, -1.5, 1.25),
        ("2026-01-09", -0.75, 0.0, -0.25),
        ("2026-01-12", 2.0, 0.0, 1.0),
        ("2026-01-13", 1.5, 3.0, -2.5),
        ("2026-01-19", -0.375, 0.25, 0.125),
    ])
    cols = hand_cols()
    window = window_of(WINDOW_DATES)
    out = align_to_window(cols, window, max_gap=2)
    assert_same(out, expected)
    # the inputs are not mutated
    for key, original in hand_cols().items():
        pd.testing.assert_series_equal(cols[key], original, check_exact=True, check_freq=False)
    pd.testing.assert_index_equal(window, window_of(WINDOW_DATES), exact=True)


@pytest.mark.criterion("C1")
def test_c1_point_hand_case_max_gap_0_and_3():
    # oracle: Requirement 1 by hand on the table above:
    #   max_gap 0 fills nothing: only the rows where Z, A and M are all observed remain
    #     (01-06, 01-08, 01-13, 01-19).
    #   max_gap 3 also fills M's 3-row gap (01-14..01-16) with 0.0; only the leading (01-05) and
    #     trailing (01-20) rows are removed.
    zero = frame([
        ("2026-01-06", -2.0, 0.5, -3.0),
        ("2026-01-08", 0.25, -1.5, 1.25),
        ("2026-01-13", 1.5, 3.0, -2.5),
        ("2026-01-19", -0.375, 0.25, 0.125),
    ])
    three = frame([
        ("2026-01-06", -2.0, 0.5, -3.0),
        ("2026-01-07", 3.5, 0.0, 0.75),
        ("2026-01-08", 0.25, -1.5, 1.25),
        ("2026-01-09", -0.75, 0.0, -0.25),
        ("2026-01-12", 2.0, 0.0, 1.0),
        ("2026-01-13", 1.5, 3.0, -2.5),
        ("2026-01-14", -1.25, -0.5, 0.0),
        ("2026-01-15", 0.5, 1.75, 0.0),
        ("2026-01-16", 4.0, -1.0, 0.0),
        ("2026-01-19", -0.375, 0.25, 0.125),
    ])
    assert_same(align_to_window(hand_cols(), window_of(WINDOW_DATES), max_gap=0), zero)
    assert_same(align_to_window(hand_cols(), window_of(WINDOW_DATES), max_gap=3), three)


@pytest.mark.criterion("C1")
def test_c1_point_column_order_from_cols():
    # oracle: Requirement 1: columns are named by key in the iteration order of cols, so the same
    #   columns given as M, Z, A produce the max_gap-2 hand frame with its columns in that order.
    expected = frame([
        ("2026-01-06", -2.0, 0.5, -3.0),
        ("2026-01-07", 3.5, 0.0, 0.75),
        ("2026-01-08", 0.25, -1.5, 1.25),
        ("2026-01-09", -0.75, 0.0, -0.25),
        ("2026-01-12", 2.0, 0.0, 1.0),
        ("2026-01-13", 1.5, 3.0, -2.5),
        ("2026-01-19", -0.375, 0.25, 0.125),
    ])[["M", "Z", "A"]]
    base = hand_cols()
    cols = {"M": base["M"], "Z": base["Z"], "A": base["A"]}
    assert_same(align_to_window(cols, window_of(WINDOW_DATES), max_gap=2), expected)


# ---------------------------------------------------------------- C3 points


@pytest.mark.criterion("C3")
def test_c3_point_rejects_negative_max_gap():
    # oracle: Requirement 1: ValueError when max_gap < 0.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        align_to_window(hand_cols(), window_of(WINDOW_DATES), max_gap=-1)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("kind", ["repeated", "decreasing"])
def test_c3_point_rejects_non_increasing_window(kind):
    # oracle: Requirement 1: ValueError when window is not strictly increasing
    #   (one date repeated, or the dates reversed).
    repeated = window_of([*WINDOW_DATES[:3], WINDOW_DATES[2], *WINDOW_DATES[3:]])
    decreasing = window_of(WINDOW_DATES[::-1])
    window = repeated if kind == "repeated" else decreasing
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        align_to_window(hand_cols(), window, max_gap=2)


@pytest.mark.criterion("C3")
def test_c3_point_empty_cols_returns_empty_frame():
    # oracle: Requirement 1: an empty cols returns an empty DataFrame (no rows, no columns), while the
    #   same window with one fully observed column (Z) returns Z on all 12 window dates, unchanged.
    out = align_to_window({}, window_of(WINDOW_DATES), max_gap=2)
    assert isinstance(out, pd.DataFrame)
    assert out.shape == (0, 0)
    z_only = align_to_window({"Z": series(Z_POINTS)}, window_of(WINDOW_DATES), max_gap=2)
    z_on_window = [v for d, v in Z_POINTS if d in WINDOW_DATES]
    expected = pd.DataFrame({"Z": z_on_window}, index=window_of(WINDOW_DATES), dtype=float)
    assert_same(z_only, expected)


# ---------------------------------------------------------------- properties

VALUE = st.integers(-8, 8).map(lambda k: k / 4.0)


@st.composite
def cases(draw: st.DrawFn, *, full: bool = False) -> tuple[dict[str, pd.Series], pd.DatetimeIndex]:
    """Columns over a grid of days and a window drawn as a non-empty subset of that grid.

    With ``full`` every column is observed on every window date (and may have extra outside dates).
    """
    n_days = draw(st.integers(1, 14))
    days = list(pd.date_range("2026-01-01", periods=n_days, freq="D"))
    in_window = draw(st.lists(st.booleans(), min_size=n_days, max_size=n_days))
    if not any(in_window):
        in_window[draw(st.integers(0, n_days - 1))] = True
    window = pd.DatetimeIndex([d for d, w in zip(days, in_window, strict=True) if w])
    n_cols = draw(st.integers(1, 4))
    cols: dict[str, pd.Series] = {}
    for j in range(n_cols):
        present = draw(st.lists(st.booleans(), min_size=n_days, max_size=n_days))
        values = draw(st.lists(VALUE, min_size=n_days, max_size=n_days))
        keep = [i for i in range(n_days) if present[i] or (full and in_window[i])]
        cols[f"f{j}"] = pd.Series([values[i] for i in keep], index=pd.DatetimeIndex([days[i] for i in keep]),
                                  dtype=float)
    return cols, window


def fillable(observed: list[bool], i: int, max_gap: int) -> bool:
    """Requirement 1 by hand: position i is observed, or sits in an interior missing run <= max_gap."""
    if observed[i]:
        return True
    lo = i
    while lo > 0 and not observed[lo - 1]:
        lo -= 1
    hi = i
    while hi < len(observed) - 1 and not observed[hi + 1]:
        hi += 1
    interior = lo > 0 and hi < len(observed) - 1
    return interior and hi - lo + 1 <= max_gap


def kept_rows(cols: dict[str, pd.Series], window: pd.DatetimeIndex, max_gap: int) -> list[pd.Timestamp]:
    """The window rows where every column is observed or fillable, in window order."""
    observed = {k: [ts in set(s.index) for ts in window] for k, s in cols.items()}
    return [ts for i, ts in enumerate(window) if all(fillable(observed[k], i, max_gap) for k in cols)]


def copy_cols(cols: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {k: s.copy() for k, s in cols.items()}


@pytest.mark.criterion("C2")
@given(case=cases(), max_gap=st.integers(0, 5))
def test_c2_property_rows_values_and_no_missing(case, max_gap):
    # oracle: Requirement 1 invariants: columns are the keys of cols in order; the index is the window
    #   rows where every column is observed or in an interior run <= max_gap (a subset of window, in
    #   window order); every value is the column's own value on that date, or exactly 0.0 where the
    #   column has none; nothing is missing; the inputs are not mutated.
    cols, window = case
    before, window_before = copy_cols(cols), window.copy()
    out = align_to_window(cols, window, max_gap=max_gap)
    assert list(out.columns) == list(cols)
    assert list(out.index) == kept_rows(cols, window, max_gap)
    assert set(out.index) <= set(window)
    assert out.index.is_monotonic_increasing
    assert not out.isna().to_numpy().any()
    for k, s in cols.items():
        for ts in out.index:
            want = float(s.loc[ts]) if ts in s.index else 0.0
            # abs 0: exact, a value is copied or the literal 0.0, never recomputed
            assert float(out.loc[ts, k]) == pytest.approx(want, abs=0.0)
    for k in cols:
        pd.testing.assert_series_equal(cols[k], before[k], check_exact=True, check_freq=False)
    pd.testing.assert_index_equal(window, window_before, exact=True)


@pytest.mark.criterion("C2")
@given(case=cases(full=True), max_gap=st.integers(0, 5))
def test_c2_property_fully_observed_is_unchanged(case, max_gap):
    # oracle: Requirement 1: when every column has a value on every window date nothing is filled or
    #   removed, so the result is the columns read on the window dates (built by hand here).
    cols, window = case
    expected = pd.DataFrame({k: [float(s.loc[ts]) for ts in window] for k, s in cols.items()},
                            index=window, dtype=float)
    assert_same(align_to_window(cols, window, max_gap=max_gap), expected)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case=cases(), max_gap=st.integers(0, 5), data=st.data())
def test_c2_metamorphic_permuting_cols_permutes_columns(case, max_gap, data):
    # oracle: Requirement 1: column order is the iteration order of cols and each column's fills depend
    #   only on that column, so permuting cols permutes the result's columns and nothing else.
    cols, window = case
    order = data.draw(st.permutations(list(cols)))
    permuted = {k: cols[k] for k in order}
    base = align_to_window(cols, window, max_gap=max_gap)
    other = align_to_window(permuted, window, max_gap=max_gap)
    assert list(base.columns) == list(cols)
    assert list(other.columns) == list(order)
    assert_same(other, base[list(order)])


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case=cases(), max_gap=st.integers(0, 4), extra=st.integers(1, 3))
def test_c2_metamorphic_raising_max_gap_never_removes_rows(case, max_gap, extra):
    # oracle: Requirement 1: a run filled at max_gap g is filled at any g' > g, so every row kept at g is
    #   kept at g', with the same values (observed values and 0.0 fills do not depend on g).
    cols, window = case
    low = align_to_window(cols, window, max_gap=max_gap)
    high = align_to_window(cols, window, max_gap=max_gap + extra)
    assert list(low.columns) == list(cols)
    assert list(high.columns) == list(cols)
    assert list(low.index) == kept_rows(cols, window, max_gap)
    assert set(low.index) <= set(high.index)
    assert_same(high.loc[low.index], low)


# ---------------------------------------------------------------- C1 metamorphic, C2 point, C3 properties

HAND_MAX_GAP_2 = [
    ("2026-01-06", -2.0, 0.5, -3.0),
    ("2026-01-07", 3.5, 0.0, 0.75),
    ("2026-01-08", 0.25, -1.5, 1.25),
    ("2026-01-09", -0.75, 0.0, -0.25),
    ("2026-01-12", 2.0, 0.0, 1.0),
    ("2026-01-13", 1.5, 3.0, -2.5),
    ("2026-01-19", -0.375, 0.25, 0.125),
]
# Dates outside the hand window: before it, on the two weekends inside it, and after it.
OUTSIDE_DATES = ["2025-12-31", "2026-01-02", "2026-01-03", "2026-01-10", "2026-01-11", "2026-01-17",
                 "2026-01-18", "2026-01-21", "2026-01-23"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(extra=st.lists(st.tuples(st.sampled_from(HAND_ORDER), st.sampled_from(OUTSIDE_DATES), VALUE), max_size=12))
def test_c1_metamorphic_outside_window_values_are_ignored(extra):
    # oracle: Requirement 1: a value a column has on a date outside the window is not used, so adding or
    #   overwriting any outside-window values in the hand case leaves the max_gap-2 hand frame unchanged
    #   (in particular they neither close A's leading gap nor M's trailing gap, nor split A's 2-row gap).
    cols = hand_cols()
    for key, day, value in extra:
        s = cols[key].copy()
        s.loc[pd.Timestamp(day)] = value
        cols[key] = s.sort_index()
    assert_same(align_to_window(cols, window_of(WINDOW_DATES), max_gap=2), frame(HAND_MAX_GAP_2))


@pytest.mark.criterion("C2")
def test_c2_point_fully_observed_and_permuted_hand_case():
    # oracle: Requirement 1 by hand:
    #   window 01-05..01-08; X and Y observed on every window date (Y also on 01-03, outside) ->
    #   the result is X and Y read on the window, unchanged, for any max_gap;
    #   cols given as Y, X -> the same frame with its columns swapped.
    window = window_of(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    x = series([("2026-01-05", 0.5), ("2026-01-06", -1.0), ("2026-01-07", 0.0), ("2026-01-08", 2.25)])
    y = series([("2026-01-03", 8.0), ("2026-01-05", -0.25), ("2026-01-06", 1.5), ("2026-01-07", -3.0),
                ("2026-01-08", 0.75)])
    expected = pd.DataFrame({"X": [0.5, -1.0, 0.0, 2.25], "Y": [-0.25, 1.5, -3.0, 0.75]}, index=window,
                            dtype=float)
    for max_gap in (0, 2, 5):
        assert_same(align_to_window({"X": x, "Y": y}, window, max_gap=max_gap), expected)
    assert_same(align_to_window({"Y": y, "X": x}, window, max_gap=2), expected[["Y", "X"]])


@pytest.mark.criterion("C2")
def test_c2_point_raising_max_gap_keeps_rows_hand_case():
    # oracle: the hand case in C1: max_gap 0 keeps 4 rows, max_gap 2 keeps 7, max_gap 3 keeps 10, each a
    #   superset of the previous (01-06, 01-08, 01-13, 01-19 / + 01-07, 01-09, 01-12 / + 01-14..01-16).
    rows = {g: list(align_to_window(hand_cols(), window_of(WINDOW_DATES), max_gap=g).index) for g in (0, 2, 3)}
    assert rows[0] == list(window_of(["2026-01-06", "2026-01-08", "2026-01-13", "2026-01-19"]))
    assert rows[2] == list(window_of([r[0] for r in HAND_MAX_GAP_2]))
    assert rows[3] == list(window_of(WINDOW_DATES[1:11]))


@pytest.mark.criterion("C3")
@given(case=cases(), bad_gap=st.integers(-10, -1), max_gap=st.integers(0, 5), kind=st.sampled_from(
    ["negative", "repeated", "decreasing"]), data=st.data())
def test_c3_property_refusals(case, bad_gap, max_gap, kind, data):
    # oracle: Requirement 1: ValueError for any max_gap < 0, for a window with a repeated date and for a
    #   window whose dates are reversed (decreasing; at least two dates so it is not also increasing).
    cols, window = case
    if kind == "negative":
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            align_to_window(cols, window, max_gap=bad_gap)
        return
    if kind == "repeated":
        i = data.draw(st.integers(0, len(window) - 1))
        bad = window.insert(i, window[i])
    else:
        bad = window.append(pd.DatetimeIndex([window[-1] + pd.Timedelta(days=1)]))[::-1]
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        align_to_window(cols, bad, max_gap=max_gap)


@pytest.mark.criterion("C3")
@given(case=cases(full=True), max_gap=st.integers(0, 5))
def test_c3_property_empty_cols_returns_empty_frame(case, max_gap):
    # oracle: Requirement 1: an empty cols returns an empty DataFrame (no rows, no columns) for any
    #   window, while the same window with fully observed columns keeps every window row.
    cols, window = case
    out = align_to_window({}, window, max_gap=max_gap)
    assert isinstance(out, pd.DataFrame)
    assert out.shape == (0, 0)
    full = align_to_window(cols, window, max_gap=max_gap)
    assert list(full.index) == list(window)
    assert list(full.columns) == list(cols)
