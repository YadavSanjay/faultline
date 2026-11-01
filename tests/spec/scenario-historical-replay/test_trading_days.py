"""scenario-historical-replay C2: the number of rows of one series in ``(start, end]``.

Oracle: Requirement 2 by hand: count the index dates strictly after ``start`` and on or before ``end``, compared
as calendar dates whatever the index type; adjacent windows add up as a property.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.replay import trading_days

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

# The tariff-shock window 2025-04-02 -> 2025-04-08 (weekdays; 04-05/04-06 are a weekend).
DAYS = ["2025-04-01", "2025-04-02", "2025-04-03", "2025-04-04", "2025-04-07", "2025-04-08", "2025-04-09"]
VALS = [0.0, 0.25, -0.5, -0.5, 0.125, 0.0625, 0.5]


@pytest.mark.criterion("C2")
def test_c2_point_counts_rows_after_start_through_end():
    # oracle: Requirement 2 by hand: (04-02, 04-08] holds 04-03, 04-04, 04-07, 04-08 = 4 rows;
    # (04-01, 04-09] holds 6; (04-03, 04-06] (a Sunday end) holds 04-04 = 1; (03-01, 04-02] holds 04-01, 04-02 = 2.
    for s in (
        pd.Series(VALS, index=DAYS),
        pd.Series(VALS, index=pd.DatetimeIndex(DAYS)),
        pd.Series(VALS, index=[date.fromisoformat(d) for d in DAYS]),
    ):
        assert trading_days(s, date(2025, 4, 2), date(2025, 4, 8)) == 4
        assert trading_days(s, date(2025, 4, 1), date(2025, 4, 9)) == 6
        assert trading_days(s, date(2025, 4, 3), date(2025, 4, 6)) == 1
        assert trading_days(s, date(2025, 3, 1), date(2025, 4, 2)) == 2


@pytest.mark.criterion("C2")
def test_c2_refusals():
    # oracle: Requirement 2: start >= end raises ValueError.
    s = pd.Series(VALS, index=DAYS)
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        trading_days(s, date(2025, 4, 8), date(2025, 4, 8))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        trading_days(s, date(2025, 4, 8), date(2025, 4, 2))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    gaps=st.lists(st.integers(min_value=1, max_value=5), min_size=1, max_size=40),
    offsets=st.lists(st.integers(min_value=-3, max_value=150), min_size=3, max_size=3, unique=True),
    kind=st.sampled_from(["str", "ts", "date"]),
)
def test_c2_property_adjacent_windows_add_up(gaps: list[int], offsets: list[int], kind: str):
    # oracle: C2 property: count(a, b) + count(b, c) = count(a, c) for a < b < c; count(a, c) also equals the
    # hand count of index dates in (a, c] computed in the test.
    first = date(2024, 7, 1)
    days = [first]
    for g in gaps:
        days.append(days[-1] + timedelta(days=g))
    if kind == "str":
        index: list[object] | pd.DatetimeIndex = [d.isoformat() for d in days]
    elif kind == "ts":
        index = pd.DatetimeIndex([d.isoformat() for d in days])
    else:
        index = list(days)
    s = pd.Series([0.125] * len(days), index=index)
    a, b, c = (first + timedelta(days=k) for k in sorted(offsets))
    whole = trading_days(s, a, c)
    assert whole == sum(1 for d in days if a < d <= c)
    assert trading_days(s, a, b) + trading_days(s, b, c) == whole
