"""credit-sdr-dedup C3: filter_credit_records keeps include-keyword rows, any action type, values as strings."""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.credit.sdr_credit_records import filter_credit_records

DAY = "2026-10-05"
FILTER_COL = "UPI Underlier Name"
INCLUDE = ["ORACLE", "Meta Platforms"]
EXCLUDE = ["tranche"]
DROPPED_COL = "Price notation"           # absent from the raw file -> must come back present and blank
EXTRA_COL = "Custom basket indicator"    # in the raw file, not an output column -> must not come back


def _id(n: int) -> str:
    return str(6_000_000_000_000_000_000 + n)


def _raw_row(cr, did, underlier, action, event="TRAD", fields=None):
    rec = cr.credit_record(did, action=action, event=event, fields={FILTER_COL: underlier, **(fields or {})})
    del rec["date"]
    del rec[DROPPED_COL]
    rec[EXTRA_COL] = "FALSE"
    return rec


# (id, underlier, action type, event type, keep?)
LABELLED = [
    (_id(1), "ORACLE CORP", "NEWT", "TRAD", True),
    (_id(2), "oracle corp", "CORR", None, True),                    # case-insensitive include
    (_id(3), "ORACLE CORP", "EROR", None, True),
    (_id(4), "ORACLE CORP", "TERM", "ETRM", True),
    (_id(5), "ORACLE CORP", "MODI", "TRAD", True),
    (_id(6), "ORACLE CORP", "REVI", None, True),
    (_id(7), "META PLATFORMS INC", "NEWT", "NOVA", True),           # second include keyword
    (_id(8), "ORACLE CORP TRANCHE 3-7", "NEWT", "TRAD", False),     # exclude keyword
    (_id(9), "Tranche on Meta Platforms", "NEWT", "TRAD", False),   # exclude, case-insensitive
    (_id(10), "MICROSOFT CORP", "NEWT", "TRAD", False),             # no include keyword
    (_id(11), None, "NEWT", "TRAD", False),                         # blank filter value
    (_id(12), "", "NEWT", "TRAD", False),                           # blank filter value
]


@pytest.mark.criterion("C3")
def test_c3_labelled_rows_schema_and_exact_strings(cr):
    # oracle: hand-built raw frame with labelled keep/drop rows (LABELLED) and the Interface column list.
    exact = {"Spread-Leg 1": "0.005935", "Price": "+102.350", "Notional amount-Leg 1": "10000000.00",
             "Fixed rate-Leg 1": "0.0100"}
    raw = pd.DataFrame([_raw_row(cr, did, und, act, ev, exact) for did, und, act, ev, _ in LABELLED])
    out = filter_credit_records(raw, DAY, FILTER_COL, list(INCLUDE), list(EXCLUDE))
    keep = [row[0] for row in LABELLED if row[4]]
    assert cr.ids(out) == sorted(keep)
    assert sorted(out.columns) == sorted(cr.CREDIT_COLUMNS)
    assert len(out.columns) == len(cr.CREDIT_COLUMNS)
    assert EXTRA_COL not in out.columns
    assert all(cr.cell(v) == DAY for v in out["date"])
    assert all(cr.is_blank(v) for v in out[DROPPED_COL])
    for col, value in exact.items():
        assert [cr.cell(v) for v in out[col]] == [value] * len(keep)
    carried = [c for c in cr.CREDIT_COLUMNS if c not in ("date", DROPPED_COL)]
    assert cr.canon(out, carried) == cr.canon(raw[raw[cr.DID].isin(keep)], carried)


# Keyword tokens chosen so no keyword is a substring of another token (space-joined values cannot
# create a match across tokens).
INC_TOKENS = ["ORACLE", "META", "ORCL"]
EXC_TOKENS = ["TRANCHE", "LCDS", "SUBORD"]
NEUTRAL = ["CORP", "INC", "XYZ", "HOLDINGS"]
SPREADS = ["0.005935", "0.0059350", "5.935E-3", "00.0061", "-0.0010", "1e-3", "0.01"]


def _recase(draw, s):
    return "".join(c.upper() if draw(st.booleans()) else c.lower() for c in s)


@st.composite
def _filter_case(draw):
    include = draw(st.lists(st.sampled_from(INC_TOKENS), min_size=1, max_size=3, unique=True))
    exclude = draw(st.lists(st.sampled_from(EXC_TOKENS), max_size=3, unique=True))
    n = draw(st.integers(min_value=0, max_value=12))
    values = []
    for _ in range(n):
        tokens = draw(st.lists(st.sampled_from(INC_TOKENS + EXC_TOKENS + NEUTRAL), max_size=4))
        values.append(_recase(draw, " ".join(tokens)) if tokens else draw(st.sampled_from([None, ""])))
    # Anchor: contains include[0] and no exclude keyword, so the right answer is never empty.
    values.append(_recase(draw, f"{include[0]} CORP"))
    actions = [draw(st.sampled_from(["NEWT", "CORR", "EROR", "MODI", "TERM", "REVI"])) for _ in values]
    spreads = [draw(st.sampled_from(SPREADS)) for _ in values]
    return include, exclude, values, actions, spreads


@pytest.mark.criterion("C3")
@given(case=_filter_case())
def test_c3_property_keyword_rule_any_action_and_exact_values(cr, case):
    # oracle: the spec C3 rule evaluated in the test: keep iff the filter value contains some include
    # keyword and no exclude keyword (case-insensitive substrings; blank dropped), for every action.
    include, exclude, values, actions, spreads = case
    rows = [_raw_row(cr, _id(100 + i), v, a, fields={"Spread-Leg 1": s})
            for i, (v, a, s) in enumerate(zip(values, actions, spreads, strict=True))]
    raw = pd.DataFrame(rows)

    def kept(v):
        if v is None or v == "":
            return False
        low = v.lower()
        return any(k.lower() in low for k in include) and not any(k.lower() in low for k in exclude)

    expected = [_id(100 + i) for i, v in enumerate(values) if kept(v)]
    out = filter_credit_records(raw, DAY, FILTER_COL, list(include), list(exclude))
    assert cr.ids(out) == sorted(expected)
    assert sorted(out.columns) == sorted(cr.CREDIT_COLUMNS)
    by_id = dict(zip(raw[cr.DID], raw["Spread-Leg 1"], strict=True))
    for did, spread in zip(out[cr.DID], out["Spread-Leg 1"], strict=True):
        assert cr.cell(spread) == by_id[cr.cell(did)]
