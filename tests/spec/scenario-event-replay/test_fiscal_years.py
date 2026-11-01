"""scenario-event-replay C3: ``latest_filed_fiscal_years`` pins fiscal years to filings made by as-of.

Oracle: Requirement 3 by hand: fiscal year ``Y`` ends on (Y, fye_month, fye_day) with the day clamped
to the month's last day, and is filed when end + filing_lag_days <= as_of (inclusive); the result is the
``n`` most recent filed years, ascending. Filing deadlines below are counted by hand with a 90-day lag:

* Jan 31 (NVDA-like), 2024: Feb has 29 days -> Feb 29 is +29, Mar 31 +60, Apr 30 +90 -> 2024-04-30.
* Jun 30 (MSFT-like), 2024: Jul 31 +31, Aug 31 +62, Sep 28 +90 -> 2024-09-28.
* Sep 30 (AAPL-like), 2024: Oct 31 +31, Nov 30 +61, Dec 29 +90 -> 2024-12-29.
* Dec 31, 2024: Jan 31 2025 +31, Feb 28 +59, Mar 31 +90 -> 2025-03-31.
* Dec 31, 2023: Jan 31 2024 +31, Feb 29 +60 (leap), Mar 30 +90 -> 2024-03-30.
* Apr "31" -> Apr 30, 2024: May 31 +31, Jun 30 +61, Jul 29 +90 -> 2024-07-29.
* Feb "31" -> Feb 29, 2024 (leap): Mar 31 +31, Apr 30 +61, May 29 +90 -> 2024-05-29
  (an unclamped-to-29 Feb 28 would give 2024-05-28).
* Feb "31" -> Feb 28, 2023: Mar 31 +31, Apr 30 +61, May 29 +90 -> 2023-05-29.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.event_replay import latest_filed_fiscal_years

LAG = 90
DATES = st.dates(min_value=date(1990, 1, 1), max_value=date(2060, 12, 31))


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("as_of", "n", "want"), [
    (date(2024, 4, 30), 1, [2024]),         # deadline day itself: filed (inclusive)
    (date(2024, 4, 29), 1, [2023]),         # one day before: FY2024 not filed yet
    (date(2024, 4, 30), 2, [2023, 2024]),
    (date(2024, 4, 29), 2, [2022, 2023]),
    (date(2025, 1, 24), 2, [2023, 2024]),   # deepseek as-of: FY2025 ends 2025-01-31, after as_of
    (date(2024, 7, 10), 3, [2022, 2023, 2024]),
])
def test_c3_point_january_year_end_nvda_like(as_of, n, want):
    # oracle: module docstring: Jan 31 + 90 days = Apr 30 (2024, leap year); FY Y ends in January of Y.
    assert latest_filed_fiscal_years(n, 1, 31, as_of, filing_lag_days=LAG) == want


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("as_of", "n", "want"), [
    (date(2024, 9, 28), 2, [2023, 2024]),   # deadline day: filed
    (date(2024, 9, 27), 2, [2022, 2023]),   # one day before
    (date(2024, 7, 10), 1, [2023]),         # yen_carry as-of: FY2024 (ends 06-30) not filed until 09-28
    (date(2025, 4, 2), 2, [2023, 2024]),
])
def test_c3_point_june_year_end_msft_like(as_of, n, want):
    # oracle: module docstring: Jun 30 + 90 days = Sep 28.
    assert latest_filed_fiscal_years(n, 6, 30, as_of, filing_lag_days=LAG) == want


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("as_of", "n", "want"), [
    (date(2024, 12, 29), 1, [2024]),        # deadline day: filed
    (date(2024, 12, 28), 1, [2023]),        # one day before
    (date(2025, 1, 24), 2, [2023, 2024]),
    (date(2024, 7, 10), 2, [2022, 2023]),
])
def test_c3_point_september_year_end_aapl_like(as_of, n, want):
    # oracle: module docstring: Sep 30 + 90 days = Dec 29.
    assert latest_filed_fiscal_years(n, 9, 30, as_of, filing_lag_days=LAG) == want


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("as_of", "n", "want"), [
    (date(2025, 3, 31), 2, [2023, 2024]),   # FY2024 deadline day: filed
    (date(2025, 3, 30), 2, [2022, 2023]),   # one day before
    (date(2024, 3, 30), 1, [2023]),         # FY2023 deadline in the leap year 2024 is Mar 30
    (date(2024, 3, 29), 1, [2022]),
    (date(2025, 1, 24), 2, [2022, 2023]),   # deepseek as-of: FY2024 not filed until 2025-03-31
])
def test_c3_point_december_year_end(as_of, n, want):
    # oracle: module docstring: Dec 31 + 90 days = Mar 31 of the next year (Mar 30 when it is a leap year).
    assert latest_filed_fiscal_years(n, 12, 31, as_of, filing_lag_days=LAG) == want


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("as_of", "want"), [(date(2024, 7, 29), [2023, 2024]),
                                             (date(2024, 7, 28), [2022, 2023])])
def test_c3_point_day_31_clamped_in_april(as_of, want):
    # oracle: module docstring: April has 30 days, so "Apr 31" is Apr 30; + 90 days = Jul 29.
    assert latest_filed_fiscal_years(2, 4, 31, as_of, filing_lag_days=LAG) == want


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("as_of", [date(2024, 9, 28), date(2024, 9, 27), date(2025, 1, 24)])
def test_c3_point_day_31_in_june_equals_june_30(as_of):
    # oracle: Requirement 3: June has 30 days, so "Jun 31" clamps to Jun 30 (MSFT-like deadline Sep 28):
    #   2024-09-28 -> [2023, 2024], 2024-09-27 -> [2022, 2023], 2025-01-24 -> [2023, 2024].
    want = {date(2024, 9, 28): [2023, 2024], date(2024, 9, 27): [2022, 2023],
            date(2025, 1, 24): [2023, 2024]}[as_of]
    assert latest_filed_fiscal_years(2, 6, 31, as_of, filing_lag_days=LAG) == want


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("as_of", "want"), [
    (date(2024, 5, 29), [2023, 2024]),      # leap: Feb 29 2024 + 90 = May 29 -> filed
    (date(2024, 5, 28), [2022, 2023]),      # a Feb 28 2024 end would wrongly be filed here
    (date(2023, 5, 29), [2022, 2023]),      # non-leap: Feb 28 2023 + 90 = May 29 -> filed
    (date(2023, 5, 28), [2021, 2022]),
])
def test_c3_point_day_31_clamped_in_february_incl_leap_year(as_of, want):
    # oracle: module docstring: "Feb 31" is Feb 29 in 2024 and Feb 28 in 2023; both + 90 = May 29.
    assert latest_filed_fiscal_years(2, 2, 31, as_of, filing_lag_days=LAG) == want


@pytest.mark.criterion("C3")
def test_c3_point_zero_lag_files_on_the_year_end():
    # oracle: Requirement 3 with lag 0: end + 0 <= as_of, so FY2024 (Dec 31) counts on 2024-12-31 itself
    #   and not on 2024-12-30.
    assert latest_filed_fiscal_years(1, 12, 31, date(2024, 12, 31), filing_lag_days=0) == [2024]
    assert latest_filed_fiscal_years(1, 12, 31, date(2024, 12, 30), filing_lag_days=0) == [2023]


@pytest.mark.criterion("C3")
def test_c3_point_no_qualifying_year_returns_empty():
    # oracle: Requirement 3 "fewer than n qualifying years ... returns those found": a 40000-day lag
    #   (about 109 years) leaves no year among the 50 before 2025-01-24 filed -> []; the same call with
    #   the 90-day lag finds [2022, 2023] (FY2023 filed 2024-03-30; FY2024 not until 2025-03-31).
    assert latest_filed_fiscal_years(2, 12, 31, date(2025, 1, 24), filing_lag_days=40000) == []
    assert latest_filed_fiscal_years(2, 12, 31, date(2025, 1, 24), filing_lag_days=LAG) == [2022, 2023]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("n", "month", "day", "lag"), [
    (0, 1, 31, 90), (-1, 1, 31, 90),        # n < 1
    (2, 0, 31, 90), (2, 13, 31, 90),        # month outside 1-12
    (2, 1, 0, 90), (2, 1, 32, 90),          # day outside 1-31
    (2, 1, 31, -1),                         # negative lag
])
def test_c3_point_invalid_input_raises(n, month, day, lag):
    # oracle: Requirement 3: ValueError for n < 1, fye_month outside 1-12, fye_day outside 1-31 or
    #   filing_lag_days < 0.
    with pytest.raises(ValueError, match=r"\S"):
        latest_filed_fiscal_years(n, month, day, date(2025, 1, 24), filing_lag_days=lag)


@pytest.mark.criterion("C3")
@given(n=st.integers(min_value=1, max_value=5), month=st.integers(min_value=1, max_value=12),
       day=st.integers(min_value=1, max_value=31), as_of=DATES, lag=st.integers(min_value=0, max_value=400))
def test_c3_property_most_recent_n_filed_years_ascending(h, n, month, day, as_of, lag):
    # oracle: C3 properties plus Requirement 3 by hand: with n <= 5 and lag <= 400 days, at least n
    #   years among the 50 before as_of are filed, so exactly n come back; they are ascending,
    #   consecutive, each satisfies the filed rule (clamped end + lag <= as_of), and the year after
    #   the latest is not filed (they are the most recent).
    result = latest_filed_fiscal_years(n, month, day, as_of, filing_lag_days=lag)
    assert len(result) == n
    assert result == sorted(set(result))
    assert result == list(range(result[0], result[0] + n))
    assert all(h.filed(y, month, day, as_of, lag) for y in result)
    assert not h.filed(result[-1] + 1, month, day, as_of, lag)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(n=st.integers(min_value=1, max_value=5), month=st.integers(min_value=1, max_value=12),
       day=st.integers(min_value=1, max_value=31), as_of=DATES, lag=st.integers(min_value=0, max_value=400),
       later=st.integers(min_value=0, max_value=3000))
def test_c3_property_later_as_of_never_removes_latest_year(h, n, month, day, as_of, lag, later):
    # oracle: C3 property: moving as_of later never lowers the latest year (a filed year stays filed);
    #   the latest year at the earlier date satisfies the filed rule at the later date too.
    early = latest_filed_fiscal_years(n, month, day, as_of, filing_lag_days=lag)
    late = latest_filed_fiscal_years(n, month, day, as_of + timedelta(days=later), filing_lag_days=lag)
    assert early
    assert late
    assert late[-1] >= early[-1]
    assert h.filed(early[-1], month, day, as_of + timedelta(days=later), lag)
    assert len(late) <= n
