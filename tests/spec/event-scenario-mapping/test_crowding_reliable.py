"""event-scenario-mapping C7: ``crowding_reliable``, enough fresh short-interest observations.

Oracle: Requirement 7 by hand: keep the dates (first 10 characters) on or before ``as_of``; true when
at least ``min_obs`` remain and the latest is at most ``max_staleness_days`` days before ``as_of``
(both boundaries inclusive); false when none remain. AS_OF is 2026-06-30.

Every test checks at least one true verdict, so a constant answer can never pass.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.events import crowding_reliable

DATES = ["2026-05-31", "2026-06-15", "2026-06-20T00:00:00"]  # 3 dates; latest 06-20 is 10 days old


@pytest.mark.criterion("C7")
def test_c7_point_boundaries(h):
    # oracle: hand count: 3 dates <= as_of, latest 2026-06-20 -> staleness 10 days
    #   min_obs 3 (= count), staleness 10 (= limit) -> True (both inclusive)
    #   min_obs 4 -> False; staleness limit 9 -> False
    assert crowding_reliable(DATES, h.AS_OF, min_obs=3, max_staleness_days=10) is True
    assert crowding_reliable(DATES, h.AS_OF, min_obs=4, max_staleness_days=10) is False
    assert crowding_reliable(DATES, h.AS_OF, min_obs=3, max_staleness_days=9) is False
    assert crowding_reliable(DATES, h.AS_OF, min_obs=1, max_staleness_days=100) is True


@pytest.mark.criterion("C7")
def test_c7_point_future_dates_ignored(h):
    # oracle: hand count: 2026-07-15 and 2026-07-01 are after as_of -> ignored; still 3 dates, latest 06-20
    #   min_obs 3, staleness 10 -> True; min_obs 4 -> False (the future dates don't count)
    #   only future dates -> none left -> False; a date on as_of counts with staleness 0 -> True
    dates = [*DATES, "2026-07-15", "2026-07-01T09:00:00"]
    assert crowding_reliable(dates, h.AS_OF, min_obs=3, max_staleness_days=10) is True
    assert crowding_reliable(dates, h.AS_OF, min_obs=4, max_staleness_days=10) is False
    assert crowding_reliable(["2026-07-01", "2026-08-01"], h.AS_OF, min_obs=1, max_staleness_days=1000) is False
    assert crowding_reliable([], h.AS_OF, min_obs=1, max_staleness_days=1000) is False
    assert crowding_reliable(["2026-06-30", "2026-07-01"], h.AS_OF, min_obs=1, max_staleness_days=0) is True


_ages = st.lists(st.integers(min_value=0, max_value=400), min_size=1, max_size=30, unique=True)


def _iso(h, age):
    return (h.AS_OF - timedelta(days=age)).isoformat()


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(ages=_ages, future=st.lists(st.integers(min_value=1, max_value=400), max_size=10),
       min_obs=st.integers(min_value=1, max_value=30), stale=st.integers(min_value=0, max_value=60))
def test_c7_property_future_dates_change_nothing(h, ages, future, min_obs, stale):
    # oracle: Requirement 7 closed form computed in the test (conftest hand_reliable) plus the relation:
    #   appending dates after as_of never changes the verdict. A fixed true case keeps it non-constant.
    dates = [_iso(h, a) for a in ages]
    more = [*dates, *(_iso(h, -f) for f in future)]
    want = h.hand_reliable(dates, h.AS_OF, min_obs, stale)
    assert crowding_reliable(dates, h.AS_OF, min_obs=min_obs, max_staleness_days=stale) is want
    assert crowding_reliable(more, h.AS_OF, min_obs=min_obs, max_staleness_days=stale) is want
    assert crowding_reliable([_iso(h, 0), *more], h.AS_OF, min_obs=1, max_staleness_days=stale) is True


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(latest=st.integers(min_value=0, max_value=30), older=st.lists(st.integers(min_value=1, max_value=300),
       unique=True, max_size=20), extra=st.lists(st.integers(min_value=1, max_value=300), unique=True, max_size=20))
def test_c7_property_monotone_in_observations(h, latest, older, extra):
    # oracle: Requirement 7 by hand: base = latest date (age `latest` <= 30) plus older dates, with
    #   min_obs = its size and staleness 30 -> True; adding dates older than the latest (latest unchanged)
    #   only raises the count -> still True.
    base_ages = {latest, *(latest + a for a in older)}
    more_ages = base_ages | {latest + a for a in extra}
    base = [_iso(h, a) for a in sorted(base_ages)]
    more = [_iso(h, a) for a in sorted(more_ages)]
    assert crowding_reliable(base, h.AS_OF, min_obs=len(base), max_staleness_days=30) is True
    assert crowding_reliable(more, h.AS_OF, min_obs=len(base), max_staleness_days=30) is True
