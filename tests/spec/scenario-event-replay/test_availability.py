"""scenario-event-replay C2: ``scenario_available`` drops scenarios whose replay ends on or after as-of.

Oracle: Requirement 2 by hand: true without a ``replay`` block; with one, true exactly when the ISO
``replay.end`` is strictly before ``as_of``; ``ValueError`` for a replay block without a parsable end.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.event_replay import scenario_available

DATES = st.dates(min_value=date(1990, 1, 1), max_value=date(2060, 12, 31))


def _spec(end: str | None = None, *, replay: bool = True) -> dict[str, object]:
    spec: dict[str, object] = {"name": "tariff_shock", "tier": "severe", "shocks": {"eq:NVDA": -0.08}}
    if replay:
        spec["replay"] = {"start": "2025-04-02", "end": end} if end is not None else {"start": "2025-04-02"}
    return spec


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("as_of", [date(1999, 1, 1), date(2024, 7, 10), date(2025, 4, 2), date(2060, 1, 1)])
def test_c2_point_no_replay_block_is_available(as_of):
    # oracle: Requirement 2: a scenario without a replay block is available at every as-of date.
    assert scenario_available(_spec(replay=False), as_of) is True


@pytest.mark.criterion("C2")
def test_c2_point_end_strictly_before_as_of_is_available():
    # oracle: Requirement 2: replay.end 2025-04-08 < as_of 2025-04-09 (one day after) and < 2026-01-01.
    spec = _spec("2025-04-08")
    assert scenario_available(spec, date(2025, 4, 9)) is True
    assert scenario_available(spec, date(2026, 1, 1)) is True


@pytest.mark.criterion("C2")
def test_c2_point_end_on_or_after_as_of_is_not_available():
    # oracle: Requirement 2: tariff_shock's replay ends 2025-04-08; at the tariff episode's as-of
    #   2025-04-02 (end after as_of) and at as_of = end itself (not strictly before) it did not exist;
    #   the day after, it did.
    spec = _spec("2025-04-08")
    assert scenario_available(spec, date(2025, 4, 2)) is False
    assert scenario_available(spec, date(2025, 4, 8)) is False
    assert scenario_available(spec, date(2025, 4, 9)) is True


@pytest.mark.criterion("C2")
def test_c2_point_dates_compare_as_dates_across_month_and_year():
    # oracle: Requirement 2: end 2024-12-31 is before 2025-01-01 (available) and after 2024-12-30 (not).
    spec = _spec("2024-12-31")
    assert scenario_available(spec, date(2025, 1, 1)) is True
    assert scenario_available(spec, date(2024, 12, 30)) is False


@pytest.mark.criterion("C2")
def test_c2_point_replay_without_end_raises():
    # oracle: Requirement 2: a replay block with no end is not parsable -> ValueError.
    with pytest.raises(ValueError, match=r"\S"):
        scenario_available(_spec(None), date(2025, 4, 9))


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("bad", ["2025/04/08", "08-04-2025", "2025-13-01", "2025-02-30", "not-a-date", ""])
def test_c2_point_unparsable_end_raises(bad):
    # oracle: Requirement 2: replay.end must be an ISO date; these are other formats or impossible
    #   dates, so ValueError.
    with pytest.raises(ValueError, match=r"\S"):
        scenario_available(_spec(bad), date(2025, 4, 9))


@pytest.mark.criterion("C2")
@given(end=DATES, as_of=DATES)
def test_c2_property_available_iff_end_before_as_of(end, as_of):
    # oracle: Requirement 2 by hand for any pair of dates: available exactly when end < as_of.
    assert scenario_available(_spec(end.isoformat()), as_of) is (end < as_of)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(end=DATES, as_of=DATES, later=st.integers(min_value=0, max_value=20000))
def test_c2_property_monotone_in_as_of(end, as_of, later):
    # oracle: C2 property: for a fixed replay, once available, available for every later as-of; the
    #   later date is also checked against the Requirement 2 rule (end < later as_of).
    spec = _spec(end.isoformat())
    as_of_later = as_of + timedelta(days=later)
    now = scenario_available(spec, as_of)
    then = scenario_available(spec, as_of_later)
    assert then is (end < as_of_later)
    if now is True:
        assert then is True
