"""sdr-dedup C1-C5: merge_sdr_records keys on the Dissemination Identifier and resolves chains.

All records are synthetic (see conftest.py). Frames are compared order-insensitively: rows sorted by
identifier, cells as strings, missing cells as ''.
"""

from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.marketdata.rates.sdr_records import merge_sdr_records

# Made-up 19-digit identifiers.
ID19 = st.integers(min_value=10**18, max_value=10**19 - 1).map(str)

# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_all_same_day_records_survive_into_empty_set(sdr):
    # oracle: hand count of the fixture: five distinct identifiers on one date -> five rows.
    new = sdr.frame(sdr.record(f"200000000000000000{i}", ts=f"2026-10-01T1{i}:00:00Z") for i in range(1, 6))
    out = merge_sdr_records(sdr.empty(), new)
    assert len(out) == 5
    assert sdr.canon(out) == sdr.canon(new)


@pytest.mark.criterion("C1")
def test_c1_same_day_records_added_to_unrelated_existing(sdr):
    # oracle: hand count of the fixture: 2 existing (2026-09-30) + 5 new (2026-10-01), all ids distinct -> 7.
    existing = sdr.frame([
        sdr.record("3000000000000000001", date="2026-09-30", ts="2026-09-30T15:00:00Z"),
        sdr.record("3000000000000000002", date="2026-09-30", ts="2026-09-30T16:00:00Z"),
    ])
    new = sdr.frame(sdr.record(f"200000000000000000{i}", ts=f"2026-10-01T1{i}:00:00Z") for i in range(1, 6))
    out = merge_sdr_records(existing, new)
    assert len(out) == 7
    assert sdr.canon(out) == sdr.canon(pd.concat([existing, new], ignore_index=True))


@pytest.mark.criterion("C1")
@given(new_ids=st.sets(ID19, min_size=1, max_size=25), old_ids=st.sets(ID19, max_size=10))
def test_c1_property_row_count_is_existing_plus_new(sdr, new_ids, old_ids):
    # oracle: invariant: distinct identifiers sharing one date are all kept; count = |existing| + |new|.
    old_ids = old_ids - new_ids
    existing = sdr.frame(sdr.record(i, date="2026-09-30", ts="2026-09-30T12:00:00Z") for i in sorted(old_ids))
    new = sdr.frame(sdr.record(i) for i in sorted(new_ids))
    out = merge_sdr_records(existing, new)
    assert len(out) == len(old_ids) + len(new_ids)
    assert sdr.ids(out) == sorted(old_ids | new_ids)


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
def test_c2_merge_is_idempotent_on_hand_fixture(sdr):
    # oracle: invariant merge(merge(E, N), N) == merge(E, N) and merge(E, E) == E; the ids of
    # merge(E, N) by hand: E = {a, b, c}; N = {d, e} plus f = CORR of a (later) -> {b, c, d, e, f}.
    a, b, c = "4000000000000000001", "4000000000000000002", "4000000000000000003"
    d, e, f = "4000000000000000004", "4000000000000000005", "4000000000000000006"
    existing = sdr.frame([
        sdr.record(a, ts="2026-09-30T10:00:00Z", date="2026-09-30"),
        sdr.record(b, ts="2026-09-30T11:00:00Z", date="2026-09-30"),
        sdr.record(c, action="MODI", ts="2026-09-30T12:00:00Z", date="2026-09-30"),
    ])
    new = sdr.frame([
        sdr.record(d, ts="2026-10-01T10:00:00Z"),
        sdr.record(e, ts="2026-10-01T11:00:00Z"),
        sdr.record(f, action="CORR", orig=a, ts="2026-10-01T12:00:00Z", fields={"Fixed rate-Leg 1": "0.0380"}),
    ])
    once = merge_sdr_records(existing, new)
    assert sdr.ids(once) == sorted([b, c, d, e, f])
    assert sdr.canon(merge_sdr_records(once, new)) == sdr.canon(once)
    assert sdr.canon(merge_sdr_records(existing, existing)) == sdr.canon(existing)


@st.composite
def _existing_and_new(draw):
    pool = draw(st.lists(ID19, min_size=2, max_size=30, unique=True))
    split = draw(st.integers(min_value=1, max_value=len(pool) - 1))
    e_ids, fresh = pool[:split], pool[split:]
    e_actions = draw(st.lists(st.sampled_from(["NEWT", "MODI", "TERM", "REVI"]), min_size=len(e_ids),
                              max_size=len(e_ids)))
    overlap = draw(st.lists(st.sampled_from(e_ids), unique=True, max_size=len(e_ids)))
    cancelled = draw(st.lists(st.sampled_from(e_ids), unique=True, max_size=len(e_ids)))
    eror_ids = draw(st.lists(ID19.filter(lambda x: x not in pool), min_size=len(cancelled),
                             max_size=len(cancelled), unique=True))
    notionals = draw(st.lists(st.sampled_from(["50000000", "100000000", "250000000"]),
                              min_size=len(pool) + len(overlap), max_size=len(pool) + len(overlap)))
    return e_ids, e_actions, fresh, overlap, cancelled, eror_ids, notionals


@pytest.mark.criterion("C2")
@given(data=_existing_and_new())
def test_c2_property_idempotent_and_self_merge_identity(sdr, data):
    # oracle: invariant (C2) plus set identity: ids(merge(E, N)) = (ids(E) | NEWT ids(N)) - cancelled roots.
    e_ids, e_actions, fresh, overlap, cancelled, eror_ids, notionals = data
    existing = sdr.frame(
        sdr.record(i, action=act, date="2026-09-30", ts=f"2026-09-30T10:00:{k % 60:02d}Z",
                   fields={"Notional amount-Leg 1": notionals[k]})
        for k, (i, act) in enumerate(zip(e_ids, e_actions, strict=True)))
    new_rows = [sdr.record(i, ts=f"2026-10-01T10:00:{k % 60:02d}Z",
                           fields={"Notional amount-Leg 1": notionals[len(e_ids) + k]})
                for k, i in enumerate([*fresh, *overlap])]
    new_rows += [sdr.record(x, action="EROR", orig=root, ts="2026-10-01T18:00:00Z")
                 for x, root in zip(eror_ids, cancelled, strict=True)]
    new = sdr.frame(new_rows)

    once = merge_sdr_records(existing, new)
    assert sdr.ids(once) == sorted((set(e_ids) | set(fresh)) - set(cancelled))
    assert sdr.canon(merge_sdr_records(once, new)) == sdr.canon(once)
    assert sdr.canon(merge_sdr_records(existing, existing)) == sdr.canon(existing)


# ----------------------------------------------------------------------------------------- C3

X, Y, Z, W = "5000000000000000001", "5000000000000000002", "5000000000000000003", "5000000000000000004"


def _c3_fixture(sdr):
    existing_rows = [
        sdr.record(X, ts="2026-10-01T15:00:00Z", fields={"Notional amount-Leg 1": "100000000",
                                                         "Fixed rate-Leg 1": "0.0375", "Cleared": "Y"}),
        sdr.record(Y, ts="2026-10-01T09:00:00Z"),
        sdr.record(Z, ts="2026-10-01T09:30:00Z", fields={"Notional amount-Leg 1": "75000000"}),
    ]
    # The new version of X differs in every field the test checks, including an EARLIER event
    # timestamp: new wins on an identical identifier regardless of timestamps.
    new_x = sdr.record(X, ts="2026-10-01T14:00:00Z", date="2026-10-02",
                       fields={"Notional amount-Leg 1": "250000000", "Fixed rate-Leg 1": "0.0410",
                               "Cleared": "N", "Platform identifier": "BBGX", "Spread-Leg 1": "0.0005"})
    new_rows = [sdr.record(W, ts="2026-10-02T10:00:00Z", date="2026-10-02"), new_x]
    expected = [existing_rows[1], existing_rows[2], new_x, new_rows[0]]
    return existing_rows, new_rows, expected


@pytest.mark.criterion("C3")
def test_c3_new_record_wins_on_identical_identifier(sdr):
    # oracle: hand-built fixture: X from `new` (notional 250000000, rate 0.0410, uncleared, BBGX) replaces
    # X from `existing`; Y, Z (existing only) and W (new only) are kept unchanged.
    existing_rows, new_rows, expected = _c3_fixture(sdr)
    out = merge_sdr_records(sdr.frame(existing_rows), sdr.frame(new_rows))
    assert sdr.canon(out) == sdr.canon(sdr.frame(expected))
    x_row = out[out[sdr.DID].map(sdr.cell) == X]
    assert len(x_row) == 1
    assert sdr.cell(x_row["Notional amount-Leg 1"].iloc[0]) == "250000000"


@pytest.mark.criterion("C3")
@given(e_perm=st.permutations([0, 1, 2]), n_perm=st.permutations([0, 1]))
def test_c3_property_new_wins_whatever_the_row_order(sdr, e_perm, n_perm):
    # oracle: hand-built fixture (as the point test); metamorphic: input row order does not matter.
    existing_rows, new_rows, expected = _c3_fixture(sdr)
    out = merge_sdr_records(sdr.frame([existing_rows[i] for i in e_perm]), sdr.frame([new_rows[i] for i in n_perm]))
    assert sdr.canon(out) == sdr.canon(sdr.frame(expected))


# ----------------------------------------------------------------------------------------- C4
# Hand-built chains (root = Original Dissemination Identifier when present, else own identifier):
#   A: A0 NEWT 10:00, A1 CORR(A0) 12:00, A2 CORR(A0) 11:00          -> only A1 (latest timestamp) survives;
#      A2 has the larger identifier but the earlier timestamp, so "latest" cannot be read off the id
#   B: B0 NEWT, B1 EROR(B0)                                         -> nothing survives
#   C: C0 NEWT, C1 MODI(C0), C2 TERM(C0)                            -> C0, C1, C2 all survive, C0 untouched
#   E: E0 NEWT, E1 CORR(E0), E2 EROR(E0)                            -> nothing survives
#   D: D0 NEWT                                                      -> D0 survives

A0, A1, A2 = "6100000000000000000", "6100000000000000001", "6100000000000000002"
B0, B1 = "6200000000000000000", "6200000000000000001"
C0, C1, C2 = "6300000000000000000", "6300000000000000001", "6300000000000000002"
E0, E1, E2 = "6500000000000000000", "6500000000000000001", "6500000000000000002"
D0 = "6400000000000000000"


def _chains(sdr):
    return {
        A0: sdr.record(A0, ts="2026-10-01T10:00:00Z", fields={"Fixed rate-Leg 1": "0.0370"}),
        A1: sdr.record(A1, action="CORR", orig=A0, ts="2026-10-01T12:00:00Z", fields={"Fixed rate-Leg 1": "0.0372"}),
        A2: sdr.record(A2, action="CORR", orig=A0, ts="2026-10-01T11:00:00Z", fields={"Fixed rate-Leg 1": "0.0371"}),
        B0: sdr.record(B0, ts="2026-10-01T10:05:00Z"),
        B1: sdr.record(B1, action="EROR", orig=B0, ts="2026-10-01T13:00:00Z"),
        C0: sdr.record(C0, ts="2026-10-01T10:10:00Z", fields={"Notional amount-Leg 1": "300000000"}),
        C1: sdr.record(C1, action="MODI", orig=C0, ts="2026-10-01T14:00:00Z",
                       fields={"Event type": "ETRM", "Notional amount-Leg 1": "200000000"}),
        C2: sdr.record(C2, action="TERM", orig=C0, ts="2026-10-01T15:00:00Z", fields={"Event type": "ETRM"}),
        E0: sdr.record(E0, ts="2026-10-01T10:15:00Z"),
        E1: sdr.record(E1, action="CORR", orig=E0, ts="2026-10-01T11:15:00Z"),
        E2: sdr.record(E2, action="EROR", orig=E0, ts="2026-10-01T12:15:00Z"),
        D0: sdr.record(D0, ts="2026-10-01T10:20:00Z"),
    }


SURVIVORS = [A1, C0, C1, C2, D0]
CHAIN_ROOT = {A0: A0, A1: A0, A2: A0, B0: B0, B1: B0, C0: C0, C1: C0, C2: C0, E0: E0, E1: E0, E2: E0, D0: D0}


def _expected(sdr):
    recs = _chains(sdr)
    return sdr.canon(sdr.frame(recs[i] for i in SURVIVORS))


@pytest.mark.criterion("C4")
def test_c4_chains_resolve_in_a_single_merge(sdr):
    # oracle: hand-built chains (table above): survivors A1, C0, C1, C2, D0 with their own field values.
    recs = _chains(sdr)
    out = merge_sdr_records(sdr.empty(), sdr.frame(recs.values()))
    assert sdr.ids(out) == sorted(SURVIVORS)
    assert sdr.canon(out) == _expected(sdr)


@pytest.mark.criterion("C4")
def test_c4_amendments_arriving_after_their_roots(sdr):
    # oracle: hand-built chains: roots already on disk, every CORR/EROR/MODI/TERM in the next file.
    recs = _chains(sdr)
    roots = [A0, B0, C0, E0, D0]
    existing = sdr.frame(recs[i] for i in roots)
    new = sdr.frame(r for i, r in recs.items() if i not in roots)
    out = merge_sdr_records(existing, new)
    assert sdr.canon(out) == _expected(sdr)
    c0 = out[out[sdr.DID].map(sdr.cell) == C0]
    assert sdr.canon(c0) == sdr.canon(sdr.frame([recs[C0]]))


@pytest.mark.criterion("C4")
def test_c4_corrections_and_lifecycle_arriving_before_their_roots(sdr):
    # oracle: hand-built chains: A's CORRs and C's MODI/TERM on disk first, the roots A0 and C0 arrive
    # later; A1 (latest timestamp) still wins over A0 and A2, C0/C1/C2 all present.
    recs = _chains(sdr)
    existing = sdr.frame([recs[A2], recs[A1], recs[C1], recs[C2], recs[D0]])
    new = sdr.frame([recs[A0], recs[C0]])
    out = merge_sdr_records(existing, new)
    assert sdr.ids(out) == sorted(SURVIVORS)
    assert sdr.canon(out) == _expected(sdr)


@st.composite
def _arrival_plan(draw):
    order = draw(st.permutations(list(CHAIN_ROOT)))
    batch = {i: draw(st.integers(min_value=0, max_value=2)) for i in order}
    # A cancellation is never earlier than the members of its chain (see report: EROR-before-root
    # is not constrained by the spec, since the EROR row itself is not kept).
    for eror in (B1, E2):
        members = [i for i, root in CHAIN_ROOT.items() if root == CHAIN_ROOT[eror] and i != eror]
        batch[eror] = max(batch[eror], *(batch[m] for m in members))
    return order, batch


@pytest.mark.metamorphic
@pytest.mark.criterion("C4")
@given(plan=_arrival_plan())
def test_c4_metamorphic_split_and_order_of_arrival(sdr, plan):
    # oracle: hand-built chains; metamorphic: splitting the records over three successive merges, in any
    # row order, yields the same survivors as one merge.
    order, batch = plan
    recs = _chains(sdr)
    out = sdr.empty()
    for b in range(3):
        out = merge_sdr_records(out, sdr.frame(recs[i] for i in order if batch[i] == b))
    assert sdr.canon(out) == _expected(sdr)


# ----------------------------------------------------------------------------------------- C5


def _legacy():
    # The collapsed pre-change layout: one row per date, no Dissemination Identifier column.
    return pd.DataFrame({
        "date": ["2026-09-29", "2026-09-30", "2026-10-01"],
        "Action type": ["NEWT", "NEWT", "NEWT"],
        "UPI Underlier Name": ["USD-SOFR-OIS Compound"] * 3,
        "Notional currency-Leg 1": ["USD"] * 3,
        "Fixed rate-Leg 1": ["0.0371", "0.0368", "0.0375"],
    })


@pytest.mark.criterion("C5")
def test_c5_legacy_existing_is_discarded(sdr):
    # oracle: invariant merge(legacy, N) == merge(empty, N); by hand both hold exactly N's 3 records.
    new = sdr.frame([
        sdr.record("7000000000000000001", ts="2026-10-01T10:00:00Z"),
        sdr.record("7000000000000000002", ts="2026-10-01T11:00:00Z"),
        sdr.record("7000000000000000003", ts="2026-10-01T12:00:00Z"),
    ])
    from_legacy = merge_sdr_records(_legacy(), new)
    assert sdr.canon(from_legacy) == sdr.canon(merge_sdr_records(sdr.empty(), new))
    assert sdr.canon(from_legacy) == sdr.canon(new)
    assert len(from_legacy) == 3


@pytest.mark.criterion("C5")
@given(new_ids=st.sets(ID19, min_size=1, max_size=15),
       legacy_rates=st.lists(st.sampled_from(["0.0350", "0.0362", "0.0375", "0.0401"]), min_size=1, max_size=10))
def test_c5_property_legacy_equals_empty(sdr, new_ids, legacy_rates):
    # oracle: invariant merge(legacy, N) == merge(empty, N); with only NEWT records the result holds N's ids.
    legacy = pd.DataFrame({
        "date": [f"2026-09-{d + 1:02d}" for d in range(len(legacy_rates))],
        "Fixed rate-Leg 1": legacy_rates,
        "Notional currency-Leg 1": ["USD"] * len(legacy_rates),
    })
    new = sdr.frame(sdr.record(i) for i in sorted(new_ids))
    from_legacy = merge_sdr_records(legacy, new)
    assert sdr.canon(from_legacy) == sdr.canon(merge_sdr_records(sdr.empty(), new))
    assert sdr.ids(from_legacy) == sorted(new_ids)
