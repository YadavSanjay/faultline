"""options-shock-sizing C1: ``select_snapshot``, the point-in-time option snapshot date.

Oracle: Requirement 1 by hand: the latest date (first 10 characters) on or before ``as_of`` and at
most ``max_staleness_days`` days before it (inclusive), as a 10-character string, else None.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.options.sizing import select_snapshot

AS_OF = date(2026, 10, 6)


@pytest.mark.criterion("C1")
def test_c1_point_mixed_dates_picks_latest_on_or_before():
    # oracle: hand calculation, as_of 2026-10-06, limit 30:
    #   2026-10-16 and 2026-10-07 are after as_of -> out; 2026-10-06 (age 0) is the latest left.
    dates = ["2026-09-01", "2026-10-16", "2026-10-06", "2026-10-07", "2026-10-01T15:30:00"]
    assert select_snapshot(dates, AS_OF, max_staleness_days=30) == "2026-10-06"


@pytest.mark.criterion("C1")
def test_c1_point_reads_first_ten_characters():
    # oracle: hand calculation, as_of 2026-10-03: 2026-10-16/2026-10-06 after as_of; the latest left is
    #   "2026-10-01T15:30:00" (age 2), returned as its first 10 characters.
    dates = ["2026-09-01", "2026-10-16", "2026-10-06", "2026-10-01T15:30:00"]
    assert select_snapshot(dates, date(2026, 10, 3), max_staleness_days=30) == "2026-10-01"


@pytest.mark.criterion("C1")
def test_c1_point_staleness_boundary_inclusive():
    # oracle: hand calculation: 2026-09-06 is exactly 30 days before 2026-10-06 -> kept with limit 30,
    #   dropped with limit 29 (nothing else qualifies -> None).
    dates = ["2026-09-06", "2026-08-01", "2026-10-07"]
    assert select_snapshot(dates, AS_OF, max_staleness_days=30) == "2026-09-06"
    assert select_snapshot(dates, AS_OF, max_staleness_days=29) is None


@pytest.mark.criterion("C1")
def test_c1_point_zero_limit_same_day_only():
    # oracle: hand calculation, limit 0: only a date equal to as_of qualifies.
    assert select_snapshot(["2026-10-05", "2026-10-06", "2026-10-07"], AS_OF, max_staleness_days=0) == "2026-10-06"
    assert select_snapshot(["2026-10-05", "2026-10-07"], AS_OF, max_staleness_days=0) is None


@pytest.mark.criterion("C1")
def test_c1_point_none_when_nothing_qualifies():
    # oracle: hand calculation: every date is after as_of or older than 30 days; the empty list has
    #   nothing; a qualifying control date shows the function returns dates at all.
    assert select_snapshot(["2026-10-07", "2026-10-16", "2026-09-05"], AS_OF, max_staleness_days=30) is None
    assert select_snapshot([], AS_OF, max_staleness_days=30) is None
    assert select_snapshot(["2026-09-05", "2026-09-20"], AS_OF, max_staleness_days=30) == "2026-09-20"


@pytest.mark.criterion("C1")
def test_c1_point_negative_limit_raises():
    # oracle: Requirement 1: max_staleness_days < 0 raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        select_snapshot(["2026-10-06"], AS_OF, max_staleness_days=-1)


_offsets = st.lists(st.integers(min_value=-40, max_value=60), max_size=10)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    anchor=st.integers(min_value=0, max_value=20),
    others=_offsets,
    later=st.lists(st.integers(min_value=1, max_value=400), min_size=1, max_size=5),
    limit=st.integers(min_value=20, max_value=45),
)
def test_c1_property_dates_after_as_of_never_change_result(h, anchor, others, later, limit):
    # oracle: Requirement 1 closed form (h.hand_select); offsets are days before as_of (negative = after).
    #   ``anchor`` guarantees at least one qualifying date.
    dates = [(AS_OF - timedelta(days=d)).isoformat() for d in [anchor, *others]]
    base = select_snapshot(dates, AS_OF, max_staleness_days=limit)
    assert base == h.hand_select(dates, AS_OF, limit)
    extra = [(AS_OF + timedelta(days=d)).isoformat() for d in later]
    assert select_snapshot(dates + extra, AS_OF, max_staleness_days=limit) == base


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    anchor=st.integers(min_value=0, max_value=20),
    others=_offsets,
    data=st.data(),
    limit=st.integers(min_value=20, max_value=45),
)
def test_c1_property_order_independent(h, anchor, others, data, limit):
    # oracle: Requirement 1 closed form (h.hand_select); a permutation of the same dates.
    dates = [(AS_OF - timedelta(days=d)).isoformat() for d in [anchor, *others]]
    shuffled = data.draw(st.permutations(dates))
    got = select_snapshot(dates, AS_OF, max_staleness_days=limit)
    assert got == h.hand_select(dates, AS_OF, limit)
    assert select_snapshot(shuffled, AS_OF, max_staleness_days=limit) == got
    assert select_snapshot(list(reversed(dates)), AS_OF, max_staleness_days=limit) == got
