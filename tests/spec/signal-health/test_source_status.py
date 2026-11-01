"""signal-health C1: ``signal_health.source_status`` builds one source's status dict.

Oracle: Requirement 1 by hand: ``latest`` as ISO string or None; ``staleness_days = (as_of - latest).days``
or None; ``stale = staleness_days > max_staleness_days`` (False when undated); ``coverage = covered /
expected`` or None when ``expected == 0``; ``holds`` = reason -> count in order of first appearance;
``status`` = unused (expected 0) > down (covered 0) > degraded (stale, covered < expected or holds) > ok.
ValueError for covered < 0, expected < 0, covered > expected, max_staleness_days < 0, latest after as_of.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.signal_health import source_status

AS_OF = date(2026, 10, 18)
REASONS = st.sampled_from(["fallback", "timeout", "news_gate_held", "stop:no_company"])


@st.composite
def _inputs(draw):
    """A valid argument set: 0 <= covered <= expected, max_staleness >= 0, latest on/before as_of or None."""
    expected = draw(st.integers(min_value=0, max_value=50))
    covered = draw(st.integers(min_value=0, max_value=expected))
    lag = draw(st.one_of(st.none(), st.integers(min_value=0, max_value=400)))
    latest = None if lag is None else AS_OF - timedelta(days=lag)
    limit = draw(st.integers(min_value=0, max_value=200))
    holds = draw(st.lists(REASONS, max_size=6))
    return {"latest": latest, "max_staleness_days": limit, "covered": covered, "expected": expected,
            "holds": holds}


@pytest.mark.criterion("C1")
def test_c1_point_ok():
    # oracle: Requirement 1 by hand: 2026-10-18 - 2026-10-08 = 10 days <= 30 -> not stale; 5/5 -> 1.0;
    #   no holds -> {}; nothing degrades it -> "ok".
    got = source_status("news", as_of=AS_OF, latest=date(2026, 10, 8), max_staleness_days=30,
                        covered=5, expected=5, holds=[])
    assert got == {"source": "news", "latest": "2026-10-08", "staleness_days": 10, "stale": False,
                   "covered": 5, "expected": 5, "coverage": pytest.approx(1.0, abs=1e-12), "holds": {},
                   "status": "ok"}  # tolerance: a single float division, exact up to rounding


@pytest.mark.criterion("C1")
def test_c1_point_degraded_by_staleness():
    # oracle: Requirement 1 by hand, counting days from 2026-08-01 to 2026-10-18:
    #   Aug 1 -> Sep 1 = 31, Sep 1 -> Oct 1 = 30, Oct 1 -> Oct 18 = 17 -> 78 days > 60 -> stale ->
    #   "degraded" although coverage is full and there are no holds.
    got = source_status("gpr", as_of=AS_OF, latest=date(2026, 8, 1), max_staleness_days=60,
                        covered=2, expected=2, holds=())
    assert got == {"source": "gpr", "latest": "2026-08-01", "staleness_days": 78, "stale": True,
                   "covered": 2, "expected": 2, "coverage": pytest.approx(1.0, abs=1e-12), "holds": {},
                   "status": "degraded"}  # tolerance: a single float division, exact up to rounding


@pytest.mark.criterion("C1")
def test_c1_point_degraded_by_coverage():
    # oracle: Requirement 1 by hand: 3 of 4 covered -> coverage 0.75 (exact in binary), covered <
    #   expected -> "degraded"; 2026-10-18 - 2026-10-17 = 1 day <= 30 -> not stale.
    got = source_status("crowding", as_of=AS_OF, latest=date(2026, 10, 17), max_staleness_days=30,
                        covered=3, expected=4, holds=[])
    assert got == {"source": "crowding", "latest": "2026-10-17", "staleness_days": 1, "stale": False,
                   "covered": 3, "expected": 4, "coverage": pytest.approx(0.75, abs=1e-12), "holds": {},
                   "status": "degraded"}  # tolerance: a single float division, exact up to rounding


@pytest.mark.criterion("C1")
def test_c1_point_degraded_by_holds_counts_in_first_appearance_order():
    # oracle: Requirement 1 by hand: full coverage, undated, but holds ["timeout", "fallback",
    #   "timeout"] -> {"timeout": 2, "fallback": 1} (timeout first seen first) -> "degraded".
    got = source_status("macrorag", as_of=AS_OF, latest=None, max_staleness_days=0,
                        covered=6, expected=6, holds=["timeout", "fallback", "timeout"])
    assert got == {"source": "macrorag", "latest": None, "staleness_days": None, "stale": False,
                   "covered": 6, "expected": 6, "coverage": pytest.approx(1.0, abs=1e-12),
                   "holds": {"timeout": 2, "fallback": 1},
                   "status": "degraded"}  # tolerance: a single float division, exact up to rounding
    assert list(got["holds"].items()) == [("timeout", 2), ("fallback", 1)]


@pytest.mark.criterion("C1")
def test_c1_point_down_when_nothing_covered():
    # oracle: Requirement 1 by hand: expected 10 > 0 and covered 0 -> "down" (ranks above degraded even
    #   though it is also stale and held); coverage 0/10 = 0.0; Sep 1 -> Oct 1 = 30, + 17 = 47 days > 30.
    got = source_status("options", as_of=AS_OF, latest=date(2026, 9, 1), max_staleness_days=30,
                        covered=0, expected=10, holds=["no_pit", "no_pit"])
    assert got == {"source": "options", "latest": "2026-09-01", "staleness_days": 47, "stale": True,
                   "covered": 0, "expected": 10, "coverage": pytest.approx(0.0, abs=1e-12),
                   "holds": {"no_pit": 2},
                   "status": "down"}  # tolerance: 0/10 is exactly 0.0; abs guards rounding only


@pytest.mark.criterion("C1")
def test_c1_point_unused_when_nothing_expected():
    # oracle: Requirement 1 by hand: expected 0 -> coverage None and status "unused", which ranks above
    #   down/degraded, so even a stale date does not change it; 2026-10-18 - 2026-01-01 = 290 days.
    got = source_status("throughput", as_of=AS_OF, latest=date(2026, 1, 1), max_staleness_days=30,
                        covered=0, expected=0, holds=[])
    assert got == {"source": "throughput", "latest": "2026-01-01", "staleness_days": 290, "stale": True,
                   "covered": 0, "expected": 0, "coverage": None, "holds": {}, "status": "unused"}


@pytest.mark.criterion("C1")
def test_c1_point_undated_source_is_never_stale():
    # oracle: Requirement 1 by hand: latest None -> latest None, staleness_days None, stale False even
    #   with max_staleness_days 0; full coverage and no holds -> "ok"; 7/7 -> 1.0.
    got = source_status("xbrlrag", as_of=AS_OF, latest=None, max_staleness_days=0,
                        covered=7, expected=7, holds=[])
    assert got == {"source": "xbrlrag", "latest": None, "staleness_days": None, "stale": False,
                   "covered": 7, "expected": 7, "coverage": pytest.approx(1.0, abs=1e-12), "holds": {},
                   "status": "ok"}  # tolerance: a single float division, exact up to rounding


@pytest.mark.criterion("C1")
def test_c1_point_staleness_boundary_equal_is_not_stale():
    # oracle: Requirement 1 "stale when staleness_days > max_staleness_days": 2026-10-18 - 2026-09-18 =
    #   30 days == limit 30 -> not stale -> "ok"; one day older (31 > 30) -> stale -> "degraded".
    at_limit = source_status("news", as_of=AS_OF, latest=date(2026, 9, 18), max_staleness_days=30,
                             covered=1, expected=1, holds=[])
    assert at_limit["staleness_days"] == 30
    assert at_limit["stale"] is False
    assert at_limit["status"] == "ok"

    past_limit = source_status("news", as_of=AS_OF, latest=date(2026, 9, 17), max_staleness_days=30,
                               covered=1, expected=1, holds=[])
    assert past_limit["staleness_days"] == 31
    assert past_limit["stale"] is True
    assert past_limit["status"] == "degraded"


@pytest.mark.criterion("C1")
def test_c1_point_same_day_with_zero_limit_is_not_stale():
    # oracle: Requirement 1 by hand: latest == as_of -> 0 days; 0 > 0 is false -> not stale -> "ok".
    got = source_status("options", as_of=AS_OF, latest=AS_OF, max_staleness_days=0,
                        covered=2, expected=2, holds=[])
    assert got["staleness_days"] == 0
    assert got["stale"] is False
    assert got["status"] == "ok"


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    ("latest", "limit", "covered", "expected"),
    [
        pytest.param(date(2026, 10, 1), 30, -1, 4, id="covered_negative"),
        pytest.param(date(2026, 10, 1), 30, 0, -1, id="expected_negative"),
        pytest.param(date(2026, 10, 1), 30, 5, 4, id="covered_above_expected"),
        pytest.param(date(2026, 10, 1), -1, 2, 4, id="max_staleness_negative"),
        pytest.param(date(2026, 10, 19), 30, 2, 4, id="latest_after_as_of"),
    ],
)
def test_c1_point_invalid_inputs_raise(latest, limit, covered, expected):
    # oracle: Requirement 1 "It raises ValueError for covered < 0, expected < 0, covered > expected,
    #   max_staleness_days < 0, or latest after as_of" -- one case per listed condition, every other
    #   argument valid.
    with pytest.raises(ValueError, match=r"\S"):
        source_status("news", as_of=AS_OF, latest=latest, max_staleness_days=limit,
                      covered=covered, expected=expected, holds=[])


@pytest.mark.criterion("C1")
@given(args=_inputs())
def test_c1_property_coverage_in_unit_interval_and_status_rule(args):
    # oracle: Requirement 1: coverage is None exactly when expected == 0, else covered/expected in
    #   [0, 1]; status follows unused > down > degraded > ok, and is "ok" only when the source is used,
    #   covered, not stale, fully covered and unheld; holds counts total the holds list.
    got = source_status("src", as_of=AS_OF, **args)
    expected, covered, latest = args["expected"], args["covered"], args["latest"]

    if expected == 0:
        assert got["coverage"] is None
    else:
        assert 0.0 <= got["coverage"] <= 1.0
        assert got["coverage"] == pytest.approx(covered / expected, rel=1e-12)  # tolerance: one division

    stale = latest is not None and (AS_OF - latest).days > args["max_staleness_days"]
    assert got["stale"] is stale
    assert sum(got["holds"].values()) == len(args["holds"])
    assert set(got["holds"]) == set(args["holds"])

    if expected == 0:
        want = "unused"
    elif covered == 0:
        want = "down"
    elif stale or covered < expected or args["holds"]:
        want = "degraded"
    else:
        want = "ok"
    assert got["status"] == want
    if got["status"] == "ok":
        assert not stale and covered == expected > 0 and got["holds"] == {}


@pytest.mark.criterion("C1")
@given(args=_inputs(), extra=REASONS)
def test_c1_property_adding_a_hold_never_improves_status(args, extra):
    # oracle: Requirement 1 metamorphic: one more hold reason can only keep or worsen the status
    #   (ok -> degraded; degraded/down/unused unchanged) and adds exactly one to that reason's count.
    rank = {"ok": 0, "degraded": 1, "down": 2, "unused": 3}
    base = source_status("src", as_of=AS_OF, **args)
    more = source_status("src", as_of=AS_OF, **{**args, "holds": [*args["holds"], extra]})
    if base["status"] == "ok":
        assert more["status"] == "degraded"
    else:
        assert more["status"] == base["status"]
    assert rank[more["status"]] >= rank[base["status"]]
    assert more["holds"][extra] == base["holds"].get(extra, 0) + 1


@pytest.mark.criterion("C1")
@given(covered=st.integers(min_value=1, max_value=20), gap=st.integers(min_value=1, max_value=20),
       limit=st.integers(min_value=0, max_value=100))
def test_c1_property_invalid_counts_and_future_dates_always_raise(covered, gap, limit):
    # oracle: Requirement 1 refusals: covered = expected + gap > expected raises; latest = as_of + gap
    #   days (after as_of) raises; max_staleness_days = -gap raises.
    with pytest.raises(ValueError, match=r"\S"):
        source_status("s", as_of=AS_OF, latest=None, max_staleness_days=limit,
                      covered=covered + gap, expected=covered, holds=[])
    with pytest.raises(ValueError, match=r"\S"):
        source_status("s", as_of=AS_OF, latest=AS_OF + timedelta(days=gap), max_staleness_days=limit,
                      covered=covered, expected=covered, holds=[])
    with pytest.raises(ValueError, match=r"\S"):
        source_status("s", as_of=AS_OF, latest=AS_OF, max_staleness_days=-gap,
                      covered=covered, expected=covered, holds=[])
