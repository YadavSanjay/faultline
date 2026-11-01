"""credit-sdr-dedup C1-C2: merge_sdr_chains resolves chains like merge_sdr_records, on its inputs' columns.

All records are synthetic (see conftest.py). Frames are compared order-insensitively: rows sorted,
cells as strings, missing cells as ''.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.rates.sdr_records import merge_sdr_chains, merge_sdr_records

ACTIONS = ["NEWT", "CORR", "EROR", "MODI", "TERM", "REVI"]


def _id(n: int) -> str:
    """A made-up 19-digit identifier (above 2^53, so a float parse would corrupt it)."""
    return str(5_000_000_000_000_000_000 + n)


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_hand_built_chains_resolve_like_merge_sdr_records(cr):
    # oracle: hand-resolved chains (Part 43 rules from sdr-dedup) and, as a consistency check, the
    # locked merge_sdr_records on the same inputs.
    #   chain a: a NEWT 10:00 (existing), c CORR 11:00, b CORR 12:00 -> only b survives (latest CORR)
    #   chain d: d NEWT (existing), e EROR of d (new)                -> d and e both gone
    #   chain g: g NEWT, f MODI of g                                  -> both kept (lifecycle record)
    #   h: in existing and in new with another rate                    -> new version of h survives
    a, b, c, d, e, f, g, h = (_id(i) for i in range(1, 9))
    rr = cr.rates_record
    existing = cr.frame([
        rr(a, ts="2026-09-30T10:00:00Z", date="2026-09-30"),
        rr(d, ts="2026-09-30T10:30:00Z", date="2026-09-30"),
        rr(h, ts="2026-09-30T11:00:00Z", date="2026-09-30", fields={"Fixed rate-Leg 1": "0.0370"}),
    ], cr.RATES_COLUMNS)
    new = cr.frame([
        rr(c, action="CORR", orig=a, ts="2026-10-01T11:00:00Z", fields={"Fixed rate-Leg 1": "0.0371"}),
        rr(b, action="CORR", orig=a, ts="2026-10-01T12:00:00Z", fields={"Fixed rate-Leg 1": "0.0372"}),
        rr(e, action="EROR", orig=d, ts="2026-10-01T12:30:00Z"),
        rr(g, ts="2026-10-01T13:00:00Z"),
        rr(f, action="MODI", orig=g, ts="2026-10-01T13:30:00Z"),
        rr(h, ts="2026-10-01T14:00:00Z", fields={"Fixed rate-Leg 1": "0.0380"}),
    ], cr.RATES_COLUMNS)
    out = merge_sdr_chains(existing, new)
    assert cr.ids(out) == sorted([b, f, g, h])
    assert cr.cell(out.loc[out[cr.DID] == b, "Fixed rate-Leg 1"].iloc[0]) == "0.0372"
    assert cr.cell(out.loc[out[cr.DID] == h, "Fixed rate-Leg 1"].iloc[0]) == "0.0380"
    assert sorted(out.columns) == sorted(cr.RATES_COLUMNS)
    assert cr.canon(out, cr.RATES_COLUMNS) == cr.canon(merge_sdr_records(existing, new), cr.RATES_COLUMNS)


@st.composite
def _rates_inputs(draw):
    """Random chains over the sdr-dedup columns, split between `existing` and `new`.

    Identifiers are unique within each frame and timestamps are unique overall (no ties), so the
    result is order-independent. An anchor NEWT in `new` guarantees a non-empty result.
    """
    n = draw(st.integers(min_value=0, max_value=10))
    minutes = draw(st.permutations(list(range(60))))
    existing_rows, new_rows = [], []
    for i in range(n):
        did = _id(100 + i)
        action = draw(st.sampled_from(ACTIONS)) if i else "NEWT"
        orig = None if action == "NEWT" else _id(100 + draw(st.integers(min_value=0, max_value=max(i - 1, 0))))
        ts = f"2026-10-01T10:{minutes[i]:02d}:00Z"
        place = draw(st.sampled_from(["existing", "new", "both"]))
        if place in ("existing", "both"):
            existing_rows.append({"did": did, "action": action, "orig": orig, "ts": ts, "rate": "0.0300"})
        if place in ("new", "both"):
            new_rows.append({"did": did, "action": action, "orig": orig, "ts": ts, "rate": "0.0400"})
    return existing_rows, new_rows


@pytest.mark.criterion("C1")
@given(inputs=_rates_inputs())
def test_c1_property_matches_merge_sdr_records_on_rates_columns(cr, inputs):
    # oracle: consistency with the locked, already-merged merge_sdr_records (spec C1).
    existing_rows, new_rows = inputs

    def build(rows):
        return [cr.rates_record(r["did"], action=r["action"], orig=r["orig"], ts=r["ts"],
                                fields={"Fixed rate-Leg 1": r["rate"]}) for r in rows]

    anchor = _id(999)
    existing = cr.frame(build(existing_rows), cr.RATES_COLUMNS)
    new = cr.frame([*build(new_rows), cr.rates_record(anchor, ts="2026-10-01T11:00:00Z")], cr.RATES_COLUMNS)
    out = merge_sdr_chains(existing, new)
    expected = merge_sdr_records(existing, new)
    assert anchor in cr.ids(out)
    assert sorted(out.columns) == sorted(cr.RATES_COLUMNS)
    assert cr.canon(out, cr.RATES_COLUMNS) == cr.canon(expected, cr.RATES_COLUMNS)


# ----------------------------------------------------------------------------------------- C2

EXISTING_ONLY = "Existing-only flag"
NEW_ONLY = "Dissemination channel"


@pytest.mark.criterion("C2")
def test_c2_credit_columns_kept_exactly_and_values_as_strings(cr):
    # oracle: hand-built credit-shaped frames. Expected columns = union of existing's and new's columns
    # (credit columns incl. Price, plus one extra each; no rates-only column such as "Spread-Leg 2").
    # Hand-resolved: x NEWT (existing) is superseded by y = later CORR of x (new); w (existing) and
    # z (new) are unrelated new trades -> survivors {w, y, z}.
    w, x, y, z = (_id(i) for i in range(11, 15))
    existing = cr.frame([
        cr.credit_record(w, ts="2026-09-30T09:00:00Z", date="2026-09-30", fields={"Price": "101.250"}),
        cr.credit_record(x, ts="2026-09-30T10:00:00Z", date="2026-09-30", fields={"Spread-Leg 1": "0.0059350"}),
    ])
    existing[EXISTING_ONLY] = ["Y", "N"]
    new = cr.frame([
        cr.credit_record(y, action="CORR", orig=x, event=None, ts="2026-10-01T10:00:00Z",
                         fields={"Spread-Leg 1": "0.0061000"}),
        cr.credit_record(z, ts="2026-10-01T11:00:00Z",
                         fields={"Price": "099.875", "Notional amount-Leg 1": "10000000.00"}),
    ])
    new[NEW_ONLY] = ["API", "SFTP"]
    out = merge_sdr_chains(existing, new)

    union = [*list(cr.CREDIT_COLUMNS), EXISTING_ONLY, NEW_ONLY]
    assert sorted(out.columns) == sorted(union)
    assert "Spread-Leg 2" not in out.columns
    assert cr.ids(out) == sorted([w, y, z])

    def row(did):
        hit = out[out[cr.DID] == did]
        assert len(hit) == 1
        return {c: cr.cell(hit[c].iloc[0]) for c in union}

    assert row(w)["Price"] == "101.250"
    assert row(w)[EXISTING_ONLY] == "Y"
    assert row(w)[NEW_ONLY] == ""
    assert row(y)["Spread-Leg 1"] == "0.0061000"
    assert row(y)[cr.ODID] == x
    assert row(y)[EXISTING_ONLY] == ""
    assert row(y)[NEW_ONLY] == "API"
    assert row(z)["Price"] == "099.875"
    assert row(z)["Notional amount-Leg 1"] == "10000000.00"
    assert row(z)[NEW_ONLY] == "SFTP"


@pytest.mark.criterion("C2")
def test_c2_legacy_existing_is_discarded(cr):
    # oracle: hand-built: existing has no Dissemination Identifier (legacy collapsed layout), so the
    # result is exactly the two new records, with only new's columns (no legacy column comes back).
    legacy = pd.DataFrame({"date": ["2026-09-30"], "spread_bps": ["61.2"], "ticker": ["ORCL"]})
    p, q = _id(21), _id(22)
    new = cr.frame([
        cr.credit_record(p, ts="2026-10-01T10:00:00Z", fields={"Spread-Leg 1": "0.005935"}),
        cr.credit_record(q, action="TERM", orig=p, event="ETRM", ts="2026-10-01T11:00:00Z"),
    ])
    out = merge_sdr_chains(legacy, new)
    assert sorted(out.columns) == sorted(cr.CREDIT_COLUMNS)
    assert cr.canon(out, cr.CREDIT_COLUMNS) == cr.canon(new, cr.CREDIT_COLUMNS)


VALUE = st.sampled_from(["0.005935", "0.0059350", "5.935E-3", "007", "-0.0010", "1e5", "102.350", "N", "TRUE"])
EXTRA_NAMES = ["Price", "Clearing venue", "Index factor", "Upfront fee", "Series"]


@pytest.mark.criterion("C2")
@given(
    n_old=st.integers(min_value=0, max_value=5),
    n_new=st.integers(min_value=1, max_value=5),
    old_extra=st.lists(st.sampled_from(EXTRA_NAMES), unique=True, max_size=3),
    new_extra=st.lists(st.sampled_from(EXTRA_NAMES), unique=True, max_size=3),
    data=st.data(),
)
def test_c2_property_columns_are_union_and_values_identical(cr, n_old, n_new, old_extra, new_extra, data):
    # oracle: invariant (spec C2): distinct new trades all survive; columns = union of the inputs'
    # columns, nothing added; every kept value is the identical input string.
    base = [cr.DID, cr.ODID, "Action type", "Event type", "Event timestamp", "date"]

    def build(n, offset, extra, day):
        rows = []
        for i in range(n):
            r = {k: v for k, v in cr.credit_record(_id(offset + i), ts=f"{day}T10:{i:02d}:00Z", date=day).items()
                 if k in base}
            for col in extra:
                r[col] = data.draw(VALUE)
            rows.append(r)
        return pd.DataFrame(rows, columns=base + list(extra))

    existing = build(n_old, 300, old_extra, "2026-09-30")
    new = build(n_new, 400, new_extra, "2026-10-01")
    out = merge_sdr_chains(existing, new)
    union = list(dict.fromkeys(base + list(old_extra) + list(new_extra)))
    assert sorted(out.columns) == sorted(union)
    assert len(out) == n_old + n_new
    for src, extra in ((existing, old_extra), (new, new_extra)):
        cols = base + list(extra)
        got = cr.canon(out[out[cr.DID].isin(list(src[cr.DID]))], cols) if len(src) else []
        assert got == cr.canon(src, cols)
