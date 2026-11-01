"""issuer-credit-factors C2: ``issuer_spread_moves``, daily additive moves on a business-day calendar.

Oracle: Requirement 2 by hand: restrict the calendar to [first, last] daily date; place each daily value
on its calendar date (dates not in the calendar are ignored); forward-fill at most ``max_gap_days``
consecutive calendar dates; the move on a date is its value minus the previous calendar date's value
where both exist.
"""

from __future__ import annotations

import itertools

import pandas as pd
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.pricer.risk.issuer_credit import issuer_spread_moves

# Business days 2026-01-02 .. 2026-01-21 (weekdays; 2026-01-02 and 2026-01-21 lie outside the daily range).
CAL_ISO = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12",
           "2026-01-13", "2026-01-14", "2026-01-15", "2026-01-16", "2026-01-19", "2026-01-20", "2026-01-21"]
# c0..c11 = 2026-01-05 .. 2026-01-20. Daily values on c0, c1, c3, c6, c10, c11 plus Saturday 2026-01-10:
#   c2 missing (gap 1), c4-c5 missing (gap 2), c7-c9 missing (gap 3).
DAILY = [("2026-01-05", 0.010), ("2026-01-06", 0.012), ("2026-01-08", 0.015), ("2026-01-10", 0.099),
         ("2026-01-13", 0.011), ("2026-01-19", 0.020), ("2026-01-20", 0.018)]


@pytest.mark.criterion("C2")
def test_c2_point_gaps_shorter_equal_longer(h):
    # oracle: hand calculation, max_gap_days = 2. Restricted calendar 01-05..01-20 (01-02 and 01-21 out, so
    #   no move on 01-21 even though 0.018 could be carried there). Saturday 01-10 is not a calendar date
    #   -> its 0.099 is ignored. Filled values:
    #   01-05 .010, 01-06 .012, 01-07 .012 (fill, gap 1 < 2), 01-08 .015, 01-09 .015, 01-12 .015 (fills,
    #   gap 2 == 2), 01-13 .011, 01-14 .011, 01-15 .011 (2 fills), 01-16 none (gap 3 > 2), 01-19 .020,
    #   01-20 .018. Moves where both exist:
    #   01-06 .012-.010, 01-07 0, 01-08 .015-.012, 01-09 0, 01-12 0, 01-13 .011-.015, 01-14 0, 01-15 0,
    #   01-16 absent, 01-19 absent (previous missing), 01-20 .018-.020.
    cal = [h.ts(d) for d in CAL_ISO]
    got = issuer_spread_moves(h.series(DAILY), cal, max_gap_days=2)
    want = [
        (h.ts("2026-01-06"), 0.012 - 0.010), (h.ts("2026-01-07"), 0.0), (h.ts("2026-01-08"), 0.015 - 0.012),
        (h.ts("2026-01-09"), 0.0), (h.ts("2026-01-12"), 0.0), (h.ts("2026-01-13"), 0.011 - 0.015),
        (h.ts("2026-01-14"), 0.0), (h.ts("2026-01-15"), 0.0), (h.ts("2026-01-20"), 0.018 - 0.020),
    ]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12 (zeros are exact: equal filled values)


@pytest.mark.criterion("C2")
def test_c2_point_no_fill_when_limit_zero(h):
    # oracle: hand calculation, max_gap_days = 0: no forward fill, so a move needs both consecutive calendar
    #   dates observed: 01-05/01-06 -> .012-.010 and 01-19/01-20 -> .018-.020; nothing else.
    cal = [h.ts(d) for d in CAL_ISO]
    got = issuer_spread_moves(h.series(DAILY), cal, max_gap_days=0)
    want = [(h.ts("2026-01-06"), 0.012 - 0.010), (h.ts("2026-01-20"), 0.018 - 0.020)]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C2")
def test_c2_point_limit_bridges_every_gap(h):
    # oracle: hand calculation, max_gap_days = 3 bridges the 3-date gap too: 01-16 carries .011, so
    #   01-16 has move 0 and 01-19 has move .020-.011; the other moves as in the max_gap_days = 2 case.
    cal = [h.ts(d) for d in CAL_ISO]
    got = issuer_spread_moves(h.series(DAILY), cal, max_gap_days=3)
    want = [
        (h.ts("2026-01-06"), 0.012 - 0.010), (h.ts("2026-01-07"), 0.0), (h.ts("2026-01-08"), 0.015 - 0.012),
        (h.ts("2026-01-09"), 0.0), (h.ts("2026-01-12"), 0.0), (h.ts("2026-01-13"), 0.011 - 0.015),
        (h.ts("2026-01-14"), 0.0), (h.ts("2026-01-15"), 0.0), (h.ts("2026-01-16"), 0.0),
        (h.ts("2026-01-19"), 0.020 - 0.011), (h.ts("2026-01-20"), 0.018 - 0.020),
    ]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("limit", [-1, -5])
def test_c2_point_negative_limit_raises(h, limit):
    # oracle: Requirement 2: max_gap_days < 0 raises ValueError.
    cal = [h.ts(d) for d in CAL_ISO]
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        issuer_spread_moves(h.series(DAILY), cal, max_gap_days=limit)


_BDAYS = list(pd.bdate_range("2026-02-02", periods=40))
_val = st.floats(min_value=0.0005, max_value=0.05)


@pytest.mark.criterion("C2")
@given(n=st.integers(min_value=2, max_value=40), data=st.data())
def test_c2_property_no_gaps_sum_is_last_minus_first(h, n, data):
    # oracle: Requirement 2: with daily on every calendar date each move is a consecutive difference, so the
    #   n-1 moves telescope to last - first; also equal to the hand difference list (h.hand_moves).
    vals = data.draw(st.lists(_val, min_size=n, max_size=n))
    assume(vals[-1] != vals[0])
    days = _BDAYS[:n]
    daily = pd.Series(vals, index=pd.DatetimeIndex(days), dtype=float)
    got = issuer_spread_moves(daily, days, max_gap_days=0)
    assert len(got) == n - 1
    assert float(got.sum()) == pytest.approx(vals[-1] - vals[0], rel=1e-9, abs=1e-15)  # float sum of n terms
    h.assert_series(got, h.hand_moves(list(zip(days, vals, strict=True)), days, 0), rel=1e-12)  # spec rel


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(data=st.data())
def test_c2_metamorphic_bridged_gaps_sum_is_last_minus_first(h, data):
    # oracle: Requirement 2: when max_gap_days covers every gap, every restricted calendar date has a value,
    #   the filled dates contribute 0 and the moves still telescope to last - first (as with no gaps).
    mask = data.draw(st.lists(st.booleans(), min_size=len(_BDAYS) - 2, max_size=len(_BDAYS) - 2))
    observed = [_BDAYS[0]] + [d for d, m in zip(_BDAYS[1:-1], mask, strict=True) if m] + [_BDAYS[-1]]
    vals = data.draw(st.lists(_val, min_size=len(observed), max_size=len(observed)))
    assume(vals[-1] != vals[0])
    pos = [_BDAYS.index(d) for d in observed]
    longest = max(b - a - 1 for a, b in itertools.pairwise(pos))
    limit = data.draw(st.integers(min_value=longest, max_value=longest + 5))
    daily = pd.Series(vals, index=pd.DatetimeIndex(observed), dtype=float)
    got = issuer_spread_moves(daily, _BDAYS, max_gap_days=limit)
    assert len(got) == len(_BDAYS) - 1
    assert float(got.sum()) == pytest.approx(vals[-1] - vals[0], rel=1e-9, abs=1e-15)  # float sum of n terms
    want = h.hand_moves(list(zip(observed, vals, strict=True)), _BDAYS, limit)
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12
