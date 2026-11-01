"""replay-pit-leaks C1: ``implied_corr.series_as_of`` cuts a date-indexed series at ``as_of``.

Oracle: Requirement 1 by hand. ``as_of=None`` returns the whole series (a copy); otherwise the entries
whose index date (``pd.to_datetime``) is ``<= as_of``, in the original order; ``ValueError`` for an
index that cannot be parsed as dates; the input is never mutated. Values are only selected, never
computed, so results are compared exactly.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.implied_corr import series_as_of

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

DATES = st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31))
VALUES = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def _series(draw):
    """A series over distinct, unordered dates, indexed by ISO strings or by a DatetimeIndex."""
    days = draw(st.lists(DATES, unique=True, min_size=1, max_size=12))
    values = draw(st.lists(VALUES, min_size=len(days), max_size=len(days)))
    if draw(st.booleans()):
        index: pd.Index = pd.Index([d.isoformat() for d in days])
    else:
        index = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    return days, values, pd.Series(values, index=index, dtype=float)


def _days(s: pd.Series) -> list[date]:
    """The index of ``s`` as calendar dates, in order."""
    return [pd.Timestamp(i).date() for i in s.index]


@pytest.mark.criterion("C1")
def test_c1_point_string_dates_cut_after_as_of():
    # oracle: Requirement 1 by hand: as_of 2026-01-06 keeps 01-02, 01-05 and the as_of day 01-06,
    #   drops 01-07 and 01-08.
    # tolerance: exact -- the kept values are copies of the inputs, nothing is computed.
    s = pd.Series([1.5, 2.5, 3.5, 4.5, 5.5],
                  index=["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    out = series_as_of(s, date(2026, 1, 6))
    assert list(out.index) == ["2026-01-02", "2026-01-05", "2026-01-06"]
    assert list(out) == [1.5, 2.5, 3.5]


@pytest.mark.criterion("C1")
def test_c1_point_datetime_index_cut_after_as_of():
    # oracle: Requirement 1 by hand: as_of 2025-12-31 keeps 12-30 and 12-31 (the as_of day), drops
    #   2026-01-02.
    # tolerance: exact -- the kept values are copies of the inputs, nothing is computed.
    idx = pd.DatetimeIndex(["2025-12-30", "2025-12-31", "2026-01-02"])
    s = pd.Series([10.25, 11.75, 12.5], index=idx)
    out = series_as_of(s, date(2025, 12, 31))
    assert list(out.index) == [pd.Timestamp("2025-12-30"), pd.Timestamp("2025-12-31")]
    assert list(out) == [10.25, 11.75]


@pytest.mark.criterion("C1")
def test_c1_point_original_order_kept_for_unsorted_index():
    # oracle: Requirement 1 by hand: entries <= 2026-01-06 in the original (unsorted) order:
    #   01-02 (2.0) then 01-06 (3.0); 01-07 (1.0) and 01-09 (4.0) are after as_of.
    # tolerance: exact -- the kept values are copies of the inputs, nothing is computed.
    s = pd.Series([1.0, 2.0, 4.0, 3.0], index=["2026-01-07", "2026-01-02", "2026-01-09", "2026-01-06"])
    out = series_as_of(s, date(2026, 1, 6))
    assert list(out.index) == ["2026-01-02", "2026-01-06"]
    assert list(out) == [2.0, 3.0]


@pytest.mark.criterion("C1")
def test_c1_point_none_returns_whole_series_as_a_copy():
    # oracle: Requirement 1 by hand: as_of=None returns the same values and index, as a copy, so
    #   writing to the result leaves the input alone.
    # tolerance: exact -- the values are copies of the inputs, nothing is computed.
    s = pd.Series([0.5, 0.75, 1.25], index=["2026-03-02", "2026-03-03", "2026-03-04"])
    out = series_as_of(s, None)
    assert out is not s
    assert list(out.index) == ["2026-03-02", "2026-03-03", "2026-03-04"]
    assert list(out) == [0.5, 0.75, 1.25]
    out.iloc[0] = 99.0
    assert list(s) == [0.5, 0.75, 1.25]


@pytest.mark.criterion("C1")
def test_c1_point_as_of_before_every_date_is_empty():
    # oracle: Requirement 1 by hand: no date <= 2025-12-31 -> empty; the same series cut at its last
    #   date 2026-02-03 keeps both entries (so an always-empty answer fails).
    # tolerance: exact -- the kept values are copies of the inputs, nothing is computed.
    s = pd.Series([1.5, 2.5], index=["2026-02-02", "2026-02-03"])
    empty = series_as_of(s, date(2025, 12, 31))
    assert isinstance(empty, pd.Series)
    assert len(empty) == 0
    full = series_as_of(s, date(2026, 2, 3))
    assert list(full.index) == ["2026-02-02", "2026-02-03"]
    assert list(full) == [1.5, 2.5]


@pytest.mark.criterion("C1")
def test_c1_point_unparseable_index_raises():
    # oracle: Requirement 1 by hand: an index that pd.to_datetime cannot parse raises ValueError.
    s = pd.Series([1.0, 2.0], index=["not a date", "nor this"])
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        series_as_of(s, date(2026, 1, 6))


@pytest.mark.criterion("C1")
def test_c1_point_input_not_mutated():
    # oracle: Requirement 1 by hand: 2026-01-05 keeps the first two entries and the input keeps its
    #   values and index.
    # tolerance: exact -- the kept values are copies of the inputs, nothing is computed.
    s = pd.Series([1.5, 2.5, 3.5], index=["2026-01-02", "2026-01-05", "2026-01-08"])
    before = s.copy()
    out = series_as_of(s, date(2026, 1, 5))
    assert list(out) == [1.5, 2.5]
    pd.testing.assert_series_equal(s, before)


@pytest.mark.criterion("C1")
@given(data=_series(), as_of=DATES)
def test_c1_property_kept_exactly_the_dates_on_or_before_as_of(data, as_of):
    # oracle: Requirement 1 by hand: the expected entries are the drawn (date, value) pairs with
    #   date <= as_of, in draw order; every dropped date is > as_of; the input is unchanged.
    # tolerance: exact -- the kept values are copies of the inputs, nothing is computed.
    days, values, s = data
    before = s.copy()
    out = series_as_of(s, as_of)
    expected = [(d, v) for d, v in zip(days, values, strict=True) if d <= as_of]
    assert _days(out) == [d for d, _ in expected]
    assert list(out) == [v for _, v in expected]
    kept = set(_days(out))
    assert all(d > as_of for d in days if d not in kept)
    pd.testing.assert_series_equal(s, before)


@pytest.mark.criterion("C1")
@given(data=_series(), a=DATES, b=DATES)
def test_c1_property_never_grows_when_as_of_moves_earlier(data, a, b):
    # oracle: Requirement 1 by hand: entries <= earlier are a subset of entries <= later, and the
    #   later cut holds exactly the drawn dates <= later.
    days, _, s = data
    earlier, later = min(a, b), max(a, b)
    early = _days(series_as_of(s, earlier))
    late = _days(series_as_of(s, later))
    assert late == [d for d in days if d <= later]
    assert set(early) <= set(late)
    assert len(early) <= len(late)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(data=_series())
def test_c1_metamorphic_none_equals_cut_at_last_date(data):
    # oracle: Requirement 1 by hand: as_of=None and as_of=max(date) both keep every entry, so both
    #   equal the input (same index and values) and the input is unchanged.
    # tolerance: exact -- the values are copies of the inputs, nothing is computed.
    days, _, s = data
    before = s.copy()
    whole = series_as_of(s, None)
    at_last = series_as_of(s, max(days))
    pd.testing.assert_series_equal(whole, before)
    pd.testing.assert_series_equal(at_last, before)
    pd.testing.assert_series_equal(s, before)
