"""macro-as-of-filter C1: ``macro.models.point_in_time_filter`` caps ``date_to`` at the run's as-of.

Oracle: Requirement 1 by hand: the result equals the input except ``date_to``, which becomes
``as_of.isoformat()`` when the input's is ``None`` or later than ``as_of`` and is kept as given when
earlier or equal; the input is not mutated; ``ValueError`` when ``date_from`` is after ``as_of`` or
``date_from`` / ``date_to`` is not a ``YYYY-MM-DD`` date (only the first 10 characters are read).
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.macro.models import MacroFilter, point_in_time_filter

DATES = st.dates(min_value=date(2000, 1, 1), max_value=date(2090, 12, 31))
OFFSETS = st.integers(min_value=0, max_value=3650)


@pytest.mark.criterion("C1")
def test_c1_point_none_date_to_is_set_to_as_of(h):
    # oracle: Requirement 1: date_to None -> as_of.isoformat() = "2026-10-06"; all facets and date_from
    #   carried over unchanged.
    filters = h.full_filter(date_from="2025-01-01", date_to=None)
    result = point_in_time_filter(filters, date(2026, 10, 6))
    assert result == h.expected(filters, "2026-10-06")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("later", ["2026-10-07", "2026-10-13", "2027-01-01", "2099-12-31"])
def test_c1_point_later_date_to_is_capped(h, later):
    # oracle: Requirement 1: a date_to after 2026-10-06 (incl. the two Fed documents' dates 10-07 and
    #   10-13 named in the Requirement) is replaced by "2026-10-06".
    filters = h.full_filter(date_to=later)
    assert point_in_time_filter(filters, date(2026, 10, 6)) == h.expected(filters, "2026-10-06")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("kept", ["2026-10-06", "2026-10-05", "2026-01-31", "2025-06-30"])
def test_c1_point_earlier_or_equal_date_to_is_kept(h, kept):
    # oracle: Requirement 1: a date_to on or before 2026-10-06 is kept as given.
    filters = h.full_filter(date_from="2025-01-01", date_to=kept)
    assert point_in_time_filter(filters, date(2026, 10, 6)) == h.expected(filters, kept)


@pytest.mark.criterion("C1")
def test_c1_point_date_to_with_time_suffix_compares_by_first_ten_chars(h):
    # oracle: Requirement 1: only the first 10 characters are read and dates compare as dates, so
    #   "2026-10-05T23:59:59" is 2026-10-05 (earlier, kept verbatim) and "2026-10-07T00:00:00" is
    #   2026-10-07 (later, replaced by "2026-10-06").
    early = h.full_filter(date_to="2026-10-05T23:59:59")
    late = h.full_filter(date_to="2026-10-07T00:00:00")
    assert point_in_time_filter(early, date(2026, 10, 6)) == h.expected(early, "2026-10-05T23:59:59")
    assert point_in_time_filter(late, date(2026, 10, 6)) == h.expected(late, "2026-10-06")


@pytest.mark.criterion("C1")
def test_c1_point_dates_compare_as_dates_not_by_position(h):
    # oracle: Requirement 1 "dates compare as dates": 2026-09-30 is before 2026-10-06 (kept) and
    #   2026-10-10 is after it (capped), checked across a month boundary.
    kept = h.full_filter(date_to="2026-09-30")
    capped = h.full_filter(date_to="2026-10-10")
    assert point_in_time_filter(kept, date(2026, 10, 6)).date_to == "2026-09-30"
    assert point_in_time_filter(capped, date(2026, 10, 6)).date_to == "2026-10-06"


@pytest.mark.criterion("C1")
def test_c1_point_none_date_from_is_allowed_and_kept(h):
    # oracle: Requirement 1: date_from None is not "later than as_of" and not a malformed date, so it is
    #   carried over as None; date_to None becomes "2026-10-06".
    filters = h.full_filter(date_from=None, date_to=None)
    assert point_in_time_filter(filters, date(2026, 10, 6)) == h.expected(filters, "2026-10-06")


@pytest.mark.criterion("C1")
def test_c1_point_date_from_equal_to_as_of_is_allowed(h):
    # oracle: Requirement 1: only a date_from *later* than as_of raises; 2026-10-06 equals it, so the
    #   window is the single day and date_to None becomes "2026-10-06".
    filters = h.full_filter(date_from="2026-10-06", date_to=None)
    assert point_in_time_filter(filters, date(2026, 10, 6)) == h.expected(filters, "2026-10-06")


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("date_to", "hand"), [(None, "2026-10-06"), ("2026-10-13", "2026-10-06"),
                                                ("2026-01-31", "2026-01-31")])
def test_c1_point_input_not_mutated_and_result_is_new(h, date_to, hand):
    # oracle: Requirement 1: "returns a new MacroFilter" and "filters itself is not mutated": the input
    #   still equals a deep copy taken before the call, the result is a different object, and it is the
    #   hand result (None/later -> "2026-10-06", earlier kept).
    filters = h.full_filter(date_to=date_to)
    before = copy.deepcopy(filters)
    result = point_in_time_filter(filters, date(2026, 10, 6))
    assert isinstance(result, MacroFilter)
    assert result is not filters
    assert filters == before
    assert result == h.expected(before, hand)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("date_from", ["2026-10-07", "2027-01-01", "2026-10-07T00:00:00"])
def test_c1_point_date_from_after_as_of_raises(h, date_from):
    # oracle: Requirement 1: a date_from later than as_of 2026-10-06 (a window that ends before it
    #   starts) raises ValueError.
    filters = h.full_filter(date_from=date_from, date_to=None)
    with pytest.raises(ValueError, match=r"\S"):
        point_in_time_filter(filters, date(2026, 10, 6))


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("bad", ["2026/10/01", "01-10-2026", "Oct 1 2026", "2026-13-01", "2026-02-30",
                                 "not-a-date"])
def test_c1_point_non_iso_date_from_raises(h, bad):
    # oracle: Requirement 1: date_from must be a YYYY-MM-DD date in its first 10 characters; these are
    #   other formats or impossible dates (month 13, 30 February), so ValueError.
    filters = h.full_filter(date_from=bad, date_to=None)
    with pytest.raises(ValueError, match=r"\S"):
        point_in_time_filter(filters, date(2026, 10, 6))


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("bad", ["2026/10/01", "01-10-2026", "Oct 1 2026", "2026-13-01", "2026-02-30",
                                 "not-a-date", "2027/01/01"])
def test_c1_point_non_iso_date_to_raises(h, bad):
    # oracle: Requirement 1: date_to must be a YYYY-MM-DD date in its first 10 characters; these are
    #   other formats or impossible dates, so ValueError (whether it would be kept or capped).
    filters = h.full_filter(date_from="2025-01-01", date_to=bad)
    with pytest.raises(ValueError, match=r"\S"):
        point_in_time_filter(filters, date(2026, 10, 6))


@pytest.mark.criterion("C1")
@given(as_of=DATES, back=OFFSETS, to_shift=st.integers(min_value=-3650, max_value=3650) | st.none())
def test_c1_property_date_to_never_after_as_of(h, as_of, back, to_shift):
    # oracle: C1 property: whatever the input date_to (None, earlier, later), the result's date_to is an
    #   ISO date on or before as_of; every other field equals the input's.
    date_from = (as_of - timedelta(days=back)).isoformat()
    date_to = None if to_shift is None else (as_of + timedelta(days=to_shift)).isoformat()
    filters = h.full_filter(date_from=date_from, date_to=date_to)
    result = point_in_time_filter(filters, as_of)
    assert result.date_to is not None
    assert date.fromisoformat(result.date_to[:10]) <= as_of
    assert result == h.expected(filters, result.date_to)


@pytest.mark.criterion("C1")
@given(as_of=DATES, back=OFFSETS, to_shift=st.integers(min_value=-3650, max_value=3650) | st.none())
def test_c1_property_idempotent(h, as_of, back, to_shift):
    # oracle: C1 property: applying the function twice equals applying it once; the once-result is the
    #   Requirement 1 hand oracle (as_of when None/later, else kept).
    date_from = (as_of - timedelta(days=back)).isoformat()
    date_to = None if to_shift is None else (as_of + timedelta(days=to_shift)).isoformat()
    filters = h.full_filter(date_from=date_from, date_to=date_to)
    hand = as_of.isoformat() if to_shift is None or to_shift > 0 else date_to
    once = point_in_time_filter(filters, as_of)
    assert once == h.expected(filters, hand)
    assert point_in_time_filter(once, as_of) == once


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(as_of=DATES, back=OFFSETS, shift_a=OFFSETS, shift_b=OFFSETS)
def test_c1_property_independent_of_date_to_on_or_after_as_of(h, as_of, back, shift_a, shift_b):
    # oracle: C1 property: two inputs differing only in a date_to on or after as_of give equal results,
    #   both carrying date_to = as_of.isoformat() (Requirement 1: later -> as_of, equal -> kept, which is
    #   the same string).
    date_from = (as_of - timedelta(days=back)).isoformat()
    a = h.full_filter(date_from=date_from, date_to=(as_of + timedelta(days=shift_a)).isoformat())
    b = h.full_filter(date_from=date_from, date_to=(as_of + timedelta(days=shift_b)).isoformat())
    result_a = point_in_time_filter(a, as_of)
    assert result_a == point_in_time_filter(b, as_of)
    assert result_a == h.expected(a, as_of.isoformat())


@pytest.mark.criterion("C1")
@given(as_of=DATES, ahead=st.integers(min_value=1, max_value=3650), to_shift=OFFSETS | st.none())
def test_c1_property_date_from_after_as_of_always_raises(h, as_of, ahead, to_shift):
    # oracle: Requirement 1: any date_from strictly after as_of raises ValueError, whatever date_to is.
    date_to = None if to_shift is None else (as_of + timedelta(days=to_shift)).isoformat()
    filters = h.full_filter(date_from=(as_of + timedelta(days=ahead)).isoformat(), date_to=date_to)
    with pytest.raises(ValueError, match=r"\S"):
        point_in_time_filter(filters, as_of)
