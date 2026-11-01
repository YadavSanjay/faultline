"""issuer-credit-factors C1: ``issuer_daily_spreads``, the daily tenor-bucket spread of one issuer.

Oracle: Requirement 1 by hand: keep prints with ``min <= tenor <= max`` and ``spread > 0``; median per
date; walk dates in order, keeping a median unless at least 3 values are already kept and it lies
outside ``[ref / ratio, ref * ratio]`` with ``ref`` the median of the last ``lookback`` kept values; a
dropped date never enters a later ``ref``.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.risk.issuer_credit import issuer_daily_spreads

BUCKET = {"min_tenor_years": 4.0, "max_tenor_years": 6.0}


@pytest.mark.criterion("C1")
def test_c1_point_filters_and_medians(h):
    # oracle: hand calculation (ratio 10 so no outlier fires):
    #   2026-01-02: in-bucket positive prints 0.0100, 0.0120 (the 3.0Y 0.05, the -0.001 and the 0.0 are
    #               dropped) -> median (0.0100 + 0.0120) / 2 = 0.0110
    #   2026-01-05: tenors 4.0 and 6.0 are on the bucket edges (kept), 7.0 dropped -> 0.0300, 0.0320, 0.0340
    #               -> median 0.0320
    #   2026-01-06: only a 2.0Y print -> no qualifying print -> date absent
    #   2026-01-07: 0.0090, 0.0100, 0.0110, 0.0400 (rows out of order) -> (0.0100+0.0110)/2
    rows = [
        ("2026-01-07", 0.0400, 5.0),
        ("2026-01-02", 0.0100, 5.0), ("2026-01-02", 0.0120, 5.0), ("2026-01-02", 0.0500, 3.0),
        ("2026-01-02", -0.0010, 5.0), ("2026-01-02", 0.0, 5.0),
        ("2026-01-05", 0.0300, 4.0), ("2026-01-05", 0.0340, 6.0), ("2026-01-05", 0.0320, 5.0),
        ("2026-01-05", 0.0010, 7.0),
        ("2026-01-06", 0.0200, 2.0),
        ("2026-01-07", 0.0090, 5.0), ("2026-01-07", 0.0110, 5.0), ("2026-01-07", 0.0100, 5.0),
    ]
    got = issuer_daily_spreads(h.frame(rows), **BUCKET, outlier_ratio=10.0, outlier_lookback=10)
    want = [
        (h.ts("2026-01-02"), (0.0100 + 0.0120) / 2),
        (h.ts("2026-01-05"), 0.0320),
        (h.ts("2026-01-07"), (0.0100 + 0.0110) / 2),
    ]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C1")
def test_c1_point_date_objects(h):
    # oracle: hand calculation: a `date` column of datetime.date objects; 2026-01-08 prints 0.0200, 0.0260
    #   -> median 0.0230; 2026-01-09 print 0.0250 -> 0.0250 (first three kept, no outlier rule).
    rows = [(date(2026, 1, 9), 0.0250, 5.0), (date(2026, 1, 8), 0.0200, 5.0), (date(2026, 1, 8), 0.0260, 4.5)]
    got = issuer_daily_spreads(h.frame(rows), **BUCKET, outlier_ratio=2.0, outlier_lookback=10)
    h.assert_series(got, [(h.ts("2026-01-08"), (0.0200 + 0.0260) / 2), (h.ts("2026-01-09"), 0.0250)],
                    rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C1")
def test_c1_point_outliers_both_sides_and_no_leak(h):
    # oracle: hand calculation, ratio 2, lookback 10 (larger than the kept count, so ref = median of all kept):
    #   D1 0.010, D2 0.030, D3 0.050: fewer than 3 earlier kept -> kept although D2 (vs 0.010) and D3
    #       (vs median(0.010, 0.030) = 0.020, band [0.010, 0.040]) would fail the band.
    #   D4 0.100: ref = median(.01,.03,.05) = 0.030, band [0.015, 0.060] -> dropped (high side).
    #   D5 0.070: ref still 0.030 (D4 never enters) -> dropped. Had D4 leaked: ref = median(.01,.03,.05,.10)
    #       = 0.040, band [0.020, 0.080] -> 0.070 would have been kept.
    #   D6 0.012: ref 0.030 -> 0.012 < 0.015 -> dropped (low side).
    #   D7 0.040: in [0.015, 0.060] -> kept.
    #   D8 0.016: ref = median(.01,.03,.05,.04) = (0.03+0.04)/2 = 0.035, band [0.0175, 0.070] -> dropped.
    #   D9 0.060: ref 0.035 -> 0.060 <= 0.070 -> kept.
    vals = {"2026-02-02": 0.010, "2026-02-03": 0.030, "2026-02-04": 0.050, "2026-02-05": 0.100,
            "2026-02-06": 0.070, "2026-02-09": 0.012, "2026-02-10": 0.040, "2026-02-11": 0.016,
            "2026-02-12": 0.060}
    rows = [(d, v, 5.0) for d, v in vals.items()]
    got = issuer_daily_spreads(h.frame(rows), **BUCKET, outlier_ratio=2.0, outlier_lookback=10)
    want = [(h.ts(d), vals[d]) for d in ("2026-02-02", "2026-02-03", "2026-02-04", "2026-02-10", "2026-02-12")]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C1")
def test_c1_point_lookback_window(h):
    # oracle: hand calculation, ratio 2, lookback 2 (ref = median of the LAST 2 kept values):
    #   D1 0.010, D2 0.010, D3 0.040 kept (first three).
    #   D4 0.045: ref = median(0.010, 0.040) = 0.025, band [0.0125, 0.050] -> kept. (With every kept value
    #       ref would be median(.01,.01,.04) = 0.010 and 0.045 > 0.020 would be dropped.)
    #   D5 0.015: ref = median(0.040, 0.045) = 0.0425, band [0.02125, 0.085] -> dropped. (With every kept
    #       value ref = median(.01,.01,.04,.045) = 0.025, band [0.0125, 0.05] -> kept.)
    vals = {"2026-03-02": 0.010, "2026-03-03": 0.010, "2026-03-04": 0.040, "2026-03-05": 0.045,
            "2026-03-06": 0.015}
    rows = [(d, v, 5.0) for d, v in vals.items()]
    got = issuer_daily_spreads(h.frame(rows), **BUCKET, outlier_ratio=2.0, outlier_lookback=2)
    want = [(h.ts(d), vals[d]) for d in ("2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05")]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C1")
def test_c1_point_band_edges_are_inside(h):
    # oracle: hand calculation, ratio 2, lookback 10: D1-D3 0.010 -> ref 0.010, band [0.005, 0.020] closed:
    #   D4 0.020 = ref * 2 -> kept; D5 0.005: ref = median(.01,.01,.01,.02) = 0.010 -> 0.005 = ref / 2 -> kept;
    #   D6 0.0201: ref = median(.01,.01,.01,.02,.005) = 0.010 -> 0.0201 > 0.020 -> dropped.
    #   (0.01 * 2 and 0.01 / 2 are exact in binary floating point, so the edges are exact.)
    vals = {"2026-04-01": 0.010, "2026-04-02": 0.010, "2026-04-03": 0.010, "2026-04-06": 0.020,
            "2026-04-07": 0.005, "2026-04-08": 0.0201}
    rows = [(d, v, 5.0) for d, v in vals.items()]
    got = issuer_daily_spreads(h.frame(rows), **BUCKET, outlier_ratio=2.0, outlier_lookback=10)
    want = [(h.ts(d), vals[d]) for d in list(vals)[:5]]
    h.assert_series(got, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C1")
def test_c1_point_no_qualifying_prints_is_empty(h):
    # oracle: Requirement 1: no qualifying print gives an empty series; control frame by hand: one in-bucket
    #   print 0.0150 on 2026-05-04 -> that single value.
    out_of_bucket = h.frame([("2026-05-04", 0.0150, 3.0), ("2026-05-05", 0.0, 5.0), ("2026-05-06", -0.01, 5.0)])
    got = issuer_daily_spreads(out_of_bucket, **BUCKET, outlier_ratio=2.0, outlier_lookback=10)
    assert isinstance(got, pd.Series)
    assert len(got) == 0
    no_rows = h.frame([])
    assert len(issuer_daily_spreads(no_rows, **BUCKET, outlier_ratio=2.0, outlier_lookback=10)) == 0
    control = issuer_daily_spreads(h.frame([("2026-05-04", 0.0150, 5.0)]), **BUCKET, outlier_ratio=2.0,
                                   outlier_lookback=10)
    h.assert_series(control, [(h.ts("2026-05-04"), 0.0150)], rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(
    ("kwargs", "drop"),
    [
        ({"outlier_ratio": 1.0}, None), ({"outlier_ratio": 0.5}, None), ({"outlier_ratio": -2.0}, None),
        ({"outlier_lookback": 0}, None), ({"outlier_lookback": -3}, None),
        ({"min_tenor_years": 6.0, "max_tenor_years": 4.0}, None),
        ({}, "date"), ({}, "spread"), ({}, "tenor_years"),
    ],
)
def test_c1_point_invalid_raises(h, kwargs, drop):
    # oracle: Requirement 1: outlier_ratio <= 1, outlier_lookback < 1, min > max and each missing column raise.
    args = {**BUCKET, "outlier_ratio": 2.0, "outlier_lookback": 10, **kwargs}
    prints = h.frame([("2026-05-04", 0.0150, 5.0)])
    if drop is not None:
        prints = prints.drop(columns=[drop])
    with pytest.raises(ValueError, match=r"\S"):  # the spec fixes the type, not the wording
        issuer_daily_spreads(prints, **args)


_DAYS = [f"2026-06-{d:02d}" for d in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12)]
_print = st.tuples(st.sampled_from(_DAYS), st.floats(min_value=-0.002, max_value=0.05),
                   st.sampled_from([3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0]))
_outside = st.tuples(st.sampled_from([*_DAYS, "2026-06-15"]), st.floats(min_value=-0.01, max_value=0.2),
                     st.one_of(st.floats(min_value=0.0, max_value=3.99), st.floats(min_value=6.01, max_value=30.0)))


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(rows=st.lists(_print, min_size=1, max_size=30), extra=st.lists(_outside, min_size=1, max_size=5),
       lookback=st.integers(min_value=1, max_value=6))
def test_c1_metamorphic_out_of_bucket_print_changes_nothing(h, rows, extra, lookback):
    # oracle: Requirement 1: a print outside [min, max] is filtered first, so adding it changes nothing;
    #   both results equal the hand walk (h.hand_daily, Requirement 1 written out with statistics.median).
    want = h.hand_daily(rows, 4.0, 6.0, 2.0, lookback)
    base = issuer_daily_spreads(h.frame(rows), **BUCKET, outlier_ratio=2.0, outlier_lookback=lookback)
    more = issuer_daily_spreads(h.frame(rows + extra), **BUCKET, outlier_ratio=2.0, outlier_lookback=lookback)
    h.assert_series(base, want, rel=1e-12)  # spec rel 1e-12
    h.assert_series(more, want, rel=1e-12)  # spec rel 1e-12


@pytest.mark.criterion("C1")
@given(rows=st.lists(_print, min_size=1, max_size=30), ratio=st.floats(min_value=1.1, max_value=5.0),
       lookback=st.integers(min_value=1, max_value=6))
def test_c1_property_dates_subset_ascending(h, rows, ratio, lookback):
    # oracle: Requirement 1: output dates are a subset of the input dates, strictly ascending, and the
    #   series equals the hand walk (h.hand_daily).
    got = issuer_daily_spreads(h.frame(rows), **BUCKET, outlier_ratio=ratio, outlier_lookback=lookback)
    idx = [pd.Timestamp(i) for i in got.index]
    assert set(idx) <= {pd.Timestamp(d) for d, _, _ in rows}
    assert idx == sorted(set(idx))
    h.assert_series(got, h.hand_daily(rows, 4.0, 6.0, ratio, lookback), rel=1e-12)  # spec rel 1e-12
