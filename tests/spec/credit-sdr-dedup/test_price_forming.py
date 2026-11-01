"""credit-sdr-dedup C4-C5: price_forming_prints keeps NEWT/TRAD prints executed in a half-open window."""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.credit.sdr_credit_records import price_forming_prints

AS_OF = "2026-10-05"


def _id(n: int) -> str:
    return str(7_000_000_000_000_000_000 + n)


def _day(base: str, offset: int) -> str:
    return (dt.date.fromisoformat(base) + dt.timedelta(days=offset)).isoformat()


# ----------------------------------------------------------------------------------------- C4

# (id, action, event, file date, Execution Timestamp, keep?) for as_of 2026-10-05, lookback 30 days
# (window: execution date in (2026-09-05, 2026-10-05]).
LABELLED = [
    (_id(1), "NEWT", "TRAD", "2026-10-05", "2026-10-05T14:00:00Z", True),
    (_id(2), "NEWT", "TRAD", "2026-10-05", "2026-10-02T15:30:00Z", True),     # late-disseminated new trade
    (_id(3), "NEWT", "TRAD", "2026-09-20", "2026-09-20T09:00:00Z", True),
    (_id(4), "NEWT", "TRAD", "2026-10-05", "2026-10-05T00:00:00Z", True),     # first second of as_of
    (_id(5), "NEWT", "TRAD", "2026-10-05", "2026-10-05T23:59:59.500Z", True),  # fractional seconds
    (_id(6), "NEWT", "TRAD", "2026-10-06", "2026-10-04T16:00:00Z", False),    # disseminated after as_of
    (_id(7), "NEWT", "TRAD", "2026-08-03", "2026-08-03T12:00:00Z", False),    # executed before the window
    (_id(8), "NEWT", "NOVA", "2026-10-05", "2026-10-05T12:00:00Z", False),    # novation
    (_id(9), "NEWT", "CLRG", "2026-10-05", "2026-10-05T12:00:00Z", False),    # clearing
    (_id(10), "NEWT", "ETRM", "2026-10-05", "2026-10-05T12:00:00Z", False),
    (_id(11), "NEWT", "CORP", "2026-10-05", "2026-10-05T12:00:00Z", False),
    (_id(12), "NEWT", None, "2026-10-05", "2026-10-05T12:00:00Z", False),     # blank event type
    (_id(13), "CORR", None, "2026-10-05", "2026-10-05T12:00:00Z", False),     # correction
    (_id(14), "CORR", "TRAD", "2026-10-05", "2026-10-05T12:00:00Z", False),   # correction, even with TRAD
    (_id(15), "MODI", "TRAD", "2026-10-05", "2026-10-05T12:00:00Z", False),
    (_id(16), "TERM", "ETRM", "2026-10-05", "2026-10-05T12:00:00Z", False),
    (_id(17), "REVI", "TRAD", "2026-10-05", "2026-10-05T12:00:00Z", False),
    (_id(18), "EROR", None, "2026-10-05", "2026-10-05T12:00:00Z", False),
    (_id(19), "NEWT", "TRAD", "2026-10-05", None, False),                     # missing execution timestamp
    (_id(20), "NEWT", "TRAD", "2026-10-05", "not a timestamp", False),        # unparseable
]


def _records(cr, rows):
    return cr.frame(cr.credit_record(did, action=act, event=ev, date=date, ts=f"{date}T18:00:00Z",
                                     fields={"Spread-Leg 1": f"0.00{i + 10}0", "Execution Timestamp": ex})
                    for i, (did, act, ev, date, ex, _) in enumerate(rows))


@pytest.mark.criterion("C4")
def test_c4_labelled_prints(cr):
    # oracle: hand-labelled keep/drop records (LABELLED) per the spec C4 rule.
    records = _records(cr, LABELLED)
    out = price_forming_prints(records, AS_OF, 30)
    keep = [row[0] for row in LABELLED if row[5]]
    assert cr.ids(out) == sorted(keep)
    assert sorted(out.columns) == sorted(cr.CREDIT_COLUMNS)
    assert cr.canon(out, cr.CREDIT_COLUMNS) == cr.canon(records[records[cr.DID].isin(keep)], cr.CREDIT_COLUMNS)


@pytest.mark.criterion("C4")
def test_c4_extra_columns_and_values_unchanged(cr):
    # oracle: hand-built: two kept NEWT/TRAD prints and one MODI; the kept rows come back with every
    # column (incl. one outside the credit schema) and the identical strings.
    records = cr.frame([
        cr.credit_record(_id(31), date="2026-10-05", exec_ts="2026-10-01T10:00:00Z",
                         fields={"Spread-Leg 1": "0.0059350", "Price": "099.875"}),
        cr.credit_record(_id(32), date="2026-10-05", exec_ts="2026-10-05T10:00:00Z",
                         fields={"Spread-Leg 1": "5.935E-3", "Notional amount-Leg 1": "10000000.00"}),
        cr.credit_record(_id(33), action="MODI", date="2026-10-05", exec_ts="2026-10-05T10:00:00Z"),
    ])
    records["Spread bp note"] = ["a", "b", "c"]
    out = price_forming_prints(records, AS_OF, 365)
    cols = [*list(cr.CREDIT_COLUMNS), "Spread bp note"]
    assert sorted(out.columns) == sorted(cols)
    assert cr.canon(out, cols) == cr.canon(records.iloc[:2], cols)


ACTIONS = ["NEWT", "NEWT", "CORR", "MODI", "TERM", "REVI", "EROR"]
EVENTS = ["TRAD", "TRAD", "NOVA", "CLRG", "ETRM", "CORP", None]


def _prints():
    """Random prints around as_of: (action, event, execution day offset, dissemination delay, hour)."""
    one = st.tuples(st.sampled_from(ACTIONS), st.sampled_from(EVENTS), st.integers(min_value=-400, max_value=3),
                    st.integers(min_value=0, max_value=5), st.integers(min_value=0, max_value=23))
    return st.lists(one, max_size=15)


def _build(cr, rows, base):
    recs = []
    for i, (act, ev, off, delay, hour) in enumerate(rows):
        ex_day = _day(base, off)
        recs.append(cr.credit_record(_id(1000 + i), action=act, event=ev, date=_day(ex_day, delay),
                                     exec_ts=f"{ex_day}T{hour:02d}:15:00Z"))
    return recs


@pytest.mark.criterion("C4")
@given(rows=_prints(), lookback=st.integers(min_value=1, max_value=400))
def test_c4_property_rule(cr, rows, lookback):
    # oracle: the spec C4 rule evaluated in the test with datetime.date arithmetic; an anchor NEWT/TRAD
    # print executed and disseminated on as_of keeps the right answer non-empty.
    recs = _build(cr, rows, AS_OF)
    anchor = _id(1)
    recs.append(cr.credit_record(anchor, date=AS_OF, exec_ts=f"{AS_OF}T12:00:00Z"))
    as_of = dt.date.fromisoformat(AS_OF)
    expected = []
    for r in recs:
        d = dt.date.fromisoformat(r["Execution Timestamp"][:10])
        if (r["Action type"] == "NEWT" and r["Event type"] == "TRAD" and r["date"] <= AS_OF
                and as_of - dt.timedelta(days=lookback) < d <= as_of):
            expected.append(r[cr.DID])
    out = price_forming_prints(cr.frame(recs), AS_OF, lookback)
    assert anchor in cr.ids(out)
    assert cr.ids(out) == sorted(expected)


# ----------------------------------------------------------------------------------------- C5


@pytest.mark.criterion("C5")
def test_c5_window_boundaries(cr):
    # oracle: hand-computed boundaries. as_of 2026-10-05, lookback 10 -> window (2026-09-25, 2026-10-05].
    rows = [
        (_id(51), "2026-09-25T23:59:59Z", False),   # exactly lookback days before as_of: excluded
        (_id(52), "2026-09-25T00:00:00Z", False),
        (_id(53), "2026-09-26T00:00:00Z", True),    # first included day
        (_id(54), "2026-10-05T23:59:59Z", True),    # as_of itself: included
        (_id(55), "2026-10-06T00:00:00Z", False),   # after as_of
    ]
    records = cr.frame(cr.credit_record(did, date="2026-10-05", exec_ts=ex) for did, ex, _ in rows)
    out = price_forming_prints(records, AS_OF, 10)
    assert cr.ids(out) == sorted(did for did, _, keep in rows if keep)


@pytest.mark.criterion("C5")
def test_c5_boundaries_across_month_end(cr):
    # oracle: hand calendar arithmetic. as_of 2026-03-01: lookback 1 -> window (2026-02-28, 2026-03-01];
    # lookback 29 -> (2026-01-31, 2026-03-01] (February 2026 has 28 days).
    rows = [
        (_id(61), "2026-03-01T08:00:00Z"),
        (_id(62), "2026-02-28T08:00:00Z"),
        (_id(63), "2026-02-01T08:00:00Z"),
        (_id(64), "2026-01-31T08:00:00Z"),
    ]
    records = cr.frame(cr.credit_record(did, date="2026-03-01", exec_ts=ex) for did, ex in rows)
    assert cr.ids(price_forming_prints(records, "2026-03-01", 1)) == [_id(61)]
    assert cr.ids(price_forming_prints(records, "2026-03-01", 29)) == sorted([_id(61), _id(62), _id(63)])


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(rows=_prints(), short=st.integers(min_value=1, max_value=200), extra=st.integers(min_value=0, max_value=200))
def test_c5_widening_lookback_never_removes(cr, rows, short, extra):
    # oracle: metamorphic relation (spec C5): rows(as_of, L) is a subset of rows(as_of, L + k), k >= 0.
    recs = _build(cr, rows, AS_OF)
    anchor = _id(2)
    recs.append(cr.credit_record(anchor, date=AS_OF, exec_ts=f"{AS_OF}T12:00:00Z"))
    records = cr.frame(recs)
    narrow = cr.ids(price_forming_prints(records, AS_OF, short))
    wide = cr.ids(price_forming_prints(records, AS_OF, short + extra))
    assert anchor in narrow
    assert set(narrow) <= set(wide)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(rows=_prints(), lookback=st.integers(min_value=1, max_value=200), shift=st.integers(min_value=0, max_value=60))
def test_c5_moving_as_of_later_keeps_rows_still_inside(cr, rows, lookback, shift):
    # oracle: metamorphic relation (spec C5): a row kept at as_of1 is kept at as_of2 = as_of1 + shift
    # when its execution date is still > as_of2 - lookback and its file date is still <= as_of2.
    recs = _build(cr, rows, AS_OF)
    anchor = _id(3)
    recs.append(cr.credit_record(anchor, date=AS_OF, exec_ts=f"{AS_OF}T12:00:00Z"))
    records = cr.frame(recs)
    as_of2 = _day(AS_OF, shift)
    first = price_forming_prints(records, AS_OF, lookback)
    assert anchor in cr.ids(first)
    second = set(cr.ids(price_forming_prints(records, as_of2, lookback)))
    floor = dt.date.fromisoformat(as_of2) - dt.timedelta(days=lookback)
    by_id = {r[cr.DID]: r for r in recs}
    for did in cr.ids(first):
        r = by_id[did]
        if dt.date.fromisoformat(r["Execution Timestamp"][:10]) > floor and r["date"] <= as_of2:
            assert did in second
