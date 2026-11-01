"""long-history-ingest C1: parsing one Yahoo chart response into ``date, ticker, adj_close`` rows.

Oracle: Requirement 1 by hand. Payloads are synthetic and shaped like the Yahoo chart API
(``chart.result[0]`` with ``meta.gmtoffset``, ``timestamp`` and ``indicators.adjclose[0].adjclose``;
``indicators.quote[0].close`` carries different values so reading the wrong field shows). Timestamps
are UTC seconds, worked by hand from 2024-01-01 00:00 UTC = 1704067200.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.equity.yahoo_history_downloader import parse_chart

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


def _payload(timestamps: list[int], adj: list[float | None], gmtoffset: int) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": "SYN", "gmtoffset": gmtoffset, "currency": "USD"},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [{"close": [None if a is None else a + 1000.0 for a in adj]}],
                        "adjclose": [{"adjclose": adj}],
                    },
                }
            ],
            "error": None,
        }
    }


def _rows(df: pd.DataFrame) -> list[tuple[str, str, float]]:
    """Rows in the frame's own order, dates normalised to ``YYYY-MM-DD``."""
    assert {"date", "ticker", "adj_close"} <= set(df.columns)
    dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df["date"]]
    return list(zip(dates, [str(t) for t in df["ticker"]], [float(v) for v in df["adj_close"]], strict=True))


@pytest.mark.criterion("C1")
def test_c1_point_new_york_offset_nulls_repeats_and_sorting():
    # oracle: Requirement 1 by hand, gmtoffset -18000 (UTC-5):
    #   1704205800 = 2024-01-02 14:30 UTC -> 09:30 local -> 2024-01-02, adj 100.5
    #   1704292200 = 2024-01-03 14:30 UTC -> 2024-01-03, adj null -> dropped
    #   1704378600 = 2024-01-04 14:30 UTC -> 2024-01-04, adj 102.25
    #   1704420000 = 2024-01-05 02:00 UTC -> 2024-01-04 21:00 local -> 2024-01-04 again, adj 102.75 (last kept)
    #   1703860200 = 2023-12-29 14:30 UTC -> 2023-12-29, adj 99.0 (last in payload, first after sorting)
    payload = _payload(
        [1704205800, 1704292200, 1704378600, 1704420000, 1703860200],
        [100.5, None, 102.25, 102.75, 99.0],
        -18000,
    )
    out = parse_chart(payload, "NVDA")
    assert _rows(out) == [
        ("2023-12-29", "NVDA", 99.0),
        ("2024-01-02", "NVDA", 100.5),
        ("2024-01-04", "NVDA", 102.75),
    ]


@pytest.mark.criterion("C1")
def test_c1_point_positive_offset_moves_utc_date_forward():
    # oracle: Requirement 1 by hand, gmtoffset +28800 (Taipei, UTC+8):
    #   1704157200 = 2024-01-02 01:00 UTC -> 09:00 local -> 2024-01-02, adj 590.0
    #   1704229200 = 2024-01-02 21:00 UTC -> 2024-01-03 05:00 local -> 2024-01-03, adj 600.0
    out = parse_chart(_payload([1704157200, 1704229200], [590.0, 600.0], 28800), "TSM")
    assert _rows(out) == [("2024-01-02", "TSM", 590.0), ("2024-01-03", "TSM", 600.0)]


@pytest.mark.criterion("C1")
def test_c1_error_or_empty_result_raises():
    # oracle: Requirement 1: a non-null chart.error, or no result, raises ValueError.
    # Control: the same structure with a valid result parses.
    error = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
    empty = {"chart": {"result": [], "error": None}}
    none = {"chart": {"result": None, "error": None}}
    for bad in (error, empty, none):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            parse_chart(bad, "XLK")
    ok = parse_chart(_payload([1704205800], [10.0], -18000), "XLK")
    assert _rows(ok) == [("2024-01-02", "XLK", 10.0)]


_OFFSETS = st.sampled_from([-18000, -14400, 0, 19800, 28800, 32400])
_ADJ = st.integers(min_value=1, max_value=100_000).map(lambda i: i / 16)
_BASE = datetime(2010, 3, 1, tzinfo=UTC)


@st.composite
def _chart_case(draw: st.DrawFn):
    off = draw(_OFFSETS)
    valued = draw(
        st.lists(
            st.tuples(st.integers(0, 30), st.integers(0, 86399), _ADJ), min_size=1, max_size=25
        )
    )
    # Null points sit on local days no valued point uses, so "drop nulls" and "last wins" commute.
    nulls = draw(st.lists(st.tuples(st.integers(31, 40), st.integers(0, 86399)), max_size=5))
    points = [(d, s, a) for d, s, a in valued] + [(d, s, None) for d, s in nulls]
    points = draw(st.permutations(points))
    return off, points


@pytest.mark.criterion("C1")
@given(case=_chart_case())
def test_c1_property_matches_requirement(case):
    # oracle: Requirement 1 computed in the test: local date = utc timestamp + gmtoffset; nulls dropped;
    # a repeated local date keeps the last point in payload order; rows sorted by date.
    off, points = case
    timestamps, adj, expected = [], [], {}
    for day, sec, a in points:
        local = _BASE + timedelta(days=day, seconds=sec)
        timestamps.append(calendar.timegm(local.timetuple()) - off)
        adj.append(a)
        if a is not None:
            expected[local.strftime("%Y-%m-%d")] = a
    out = parse_chart(_payload(timestamps, adj, off), "SYN")
    assert _rows(out) == [(d, "SYN", expected[d]) for d in sorted(expected)]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(case=_chart_case(), shift_days=st.integers(min_value=1, max_value=400))
def test_c1_metamorphic_shifting_all_timestamps_shifts_dates(case, shift_days: int):
    # oracle: Requirement 1: adding k whole days to every timestamp shifts every output date by k days and
    # leaves the values and their order unchanged.
    off, points = case
    ts = [calendar.timegm((_BASE + timedelta(days=d, seconds=s)).timetuple()) - off for d, s, _ in points]
    adj = [a for _, _, a in points]
    base = _rows(parse_chart(_payload(ts, adj, off), "SYN"))
    moved = _rows(parse_chart(_payload([t + 86400 * shift_days for t in ts], adj, off), "SYN"))
    assert len(base) >= 1
    shifted = [
        ((date.fromisoformat(d) + timedelta(days=shift_days)).isoformat(), t, v)
        for d, t, v in base
    ]
    assert moved == shifted
