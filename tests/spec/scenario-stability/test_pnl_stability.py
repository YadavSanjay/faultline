"""scenario-stability C3: ``stability.pnl_stability`` gives the P&L range per scenario tier.

Oracle: Requirement 3 by hand: one row per (scenario, severity) in first-appearance order with
``scenario``, ``severity``, ``n_repetitions``, ``n_priced = n_present``,
``n_missing = n_repetitions - n_priced`` merged with ``spread`` over the per-repetition ``total_pnl``
(``None`` where a repetition has no record). ``ValueError`` for ``n_repetitions < 1``, a bad
repetition, a duplicate (scenario, severity, repetition) and a ``total_pnl`` that is ``None`` or that
``spread`` rejects.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.stability import pnl_stability

# Metamorphic comparisons run the same P&L numbers through the function in another order; only float
# summation order may differ, so relative 1e-9 / absolute 1e-6 (P&L in dollars up to 1e8) covers it.
META_REL = 1e-9
META_ABS = 1e-6

PNL = st.integers(min_value=-10**8, max_value=10**8).map(float)


@st.composite
def _records(draw):
    """Valid records: distinct (scenario, severity, repetition) triples."""
    n = draw(st.integers(min_value=1, max_value=4))
    keys = draw(st.lists(st.tuples(st.sampled_from(["S1", "S2"]), st.sampled_from(["mild", "adverse", "severe"]),
                                   st.integers(0, n - 1)), min_size=1, max_size=15, unique=True))
    return n, [{"repetition": r, "scenario": s, "severity": t, "total_pnl": draw(PNL)} for s, t, r in keys]


def _first_appearance(recs):
    seen = []
    for r in recs:
        key = (r["scenario"], r["severity"])
        if key not in seen:
            seen.append(key)
    return seen


@pytest.mark.criterion("C3")
def test_c3_point_three_tier_rows(h):
    # oracle: Requirement 3 by hand (conftest.pnl_expected): (A, mild) [-1.0e6, -1.2e6, -0.9e6] ->
    #   min -1.2e6, max -0.9e6, range 3.0e5, mean -3.1e6 / 3, std 1e5 * sqrt(7/3) ~ 152752.5,
    #   n_priced 3, n_missing 0; (A, adverse) [-2.0e6, None, -2.5e6] -> range 5.0e5, n_priced 2,
    #   n_missing 1; (B, mild) priced once -> std None, range 0.0. Rows in first-appearance order.
    h.assert_close(pnl_stability(h.pnl_records(), n_repetitions=3), h.pnl_expected())


@pytest.mark.criterion("C3")
def test_c3_point_std_of_mild_row_matches_spec_figure():
    # oracle: the C3 figure in the spec: std ~ 152752.5 for (A, mild); 1e5 * sqrt(7/3) = 152752.523...
    out = pnl_stability([{"repetition": r, "scenario": "A", "severity": "mild", "total_pnl": p}
                         for r, p in enumerate([-1.0e6, -1.2e6, -0.9e6])], n_repetitions=3)
    assert len(out) == 1
    assert out[0]["std"] == pytest.approx(152752.5, abs=0.05)  # the spec quotes one decimal
    assert out[0]["min"] == pytest.approx(-1.2e6, rel=1e-12)  # min is the worst loss; exact input value


@pytest.mark.criterion("C3")
def test_c3_point_no_records_is_empty_list():
    # oracle: Requirement 3 by hand: no records -> [].
    # red-exempt: empty the correct answer for no records is the empty list (other tests pin non-empty input)
    assert pnl_stability([], n_repetitions=3) == []


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(
    ("n_repetitions", "records"),
    [
        (0, [{"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": 1.0}]),      # n < 1
        (3, [{"repetition": True, "scenario": "A", "severity": "mild", "total_pnl": 1.0}]),   # bool rep
        (3, [{"repetition": 0.0, "scenario": "A", "severity": "mild", "total_pnl": 1.0}]),    # float rep
        (3, [{"repetition": 3, "scenario": "A", "severity": "mild", "total_pnl": 1.0}]),      # out of range
        (3, [{"repetition": -1, "scenario": "A", "severity": "mild", "total_pnl": 1.0}]),     # negative
        (3, [{"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": 1.0},
             {"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": 2.0}]),      # duplicate
        (3, [{"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": None}]),     # None P&L
        (3, [{"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": float("nan")}]),  # NaN
        (3, [{"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": float("inf")}]),  # inf
        (3, [{"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": False}]),    # bool P&L
        (3, [{"repetition": 0, "scenario": "A", "severity": "mild", "total_pnl": "-1.0"}]),   # string P&L
    ],
)
def test_c3_point_invalid_input_raises(n_repetitions, records):
    # oracle: Requirement 3 by hand: each case is one ValueError bullet (see the comment on each case).
    with pytest.raises(ValueError, match=r"\S"):
        pnl_stability(records, n_repetitions=n_repetitions)


@pytest.mark.criterion("C3")
def test_c3_point_input_not_mutated(h):
    # oracle: Requirement 3 by hand: "The input is not mutated" -> the records equal their earlier deep
    #   copy and the result is still conftest.pnl_expected.
    recs = h.pnl_records()
    before = copy.deepcopy(recs)
    out = pnl_stability(recs, n_repetitions=3)
    assert recs == before
    h.assert_close(out, h.pnl_expected())


@pytest.mark.criterion("C3")
@given(case=_records())
def test_c3_property_counts_add_up(case):
    # oracle: Requirement 3 by hand: one row per distinct (scenario, severity) in first-appearance order;
    #   n_priced + n_missing == n_repetitions; n_priced == n_present == number of records for the row;
    #   len(values) == n_repetitions.
    n, recs = case
    out = pnl_stability(recs, n_repetitions=n)
    keys = _first_appearance(recs)
    assert [(row["scenario"], row["severity"]) for row in out] == keys
    for row in out:
        count = sum(1 for r in recs if (r["scenario"], r["severity"]) == (row["scenario"], row["severity"]))
        assert row["n_repetitions"] == n
        assert row["n_priced"] + row["n_missing"] == n
        assert row["n_priced"] == row["n_present"] == count
        assert len(row["values"]) == n


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(case=_records(), pnl=PNL)
def test_c3_metamorphic_new_tier_leaves_existing_rows(case, pnl, h):
    # oracle: Requirement 3 by hand: a row depends only on its own (scenario, severity) records, so
    #   appending a record for a tier not yet present adds one row at the end and changes no other row.
    n, recs = case
    base = pnl_stability(recs, n_repetitions=n)
    extra = {"repetition": 0, "scenario": "S9", "severity": "extreme", "total_pnl": pnl}
    out = pnl_stability([*recs, extra], n_repetitions=n)
    assert len(base) == len(_first_appearance(recs))
    assert len(out) == len(base) + 1
    h.assert_close(out[:-1], base, rel=META_REL, abs_=META_ABS)
    assert (out[-1]["scenario"], out[-1]["severity"], out[-1]["n_priced"]) == ("S9", "extreme", 1)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(case=_records(), data=st.data())
def test_c3_metamorphic_permuting_repetition_indices(case, data, h):
    # oracle: Requirement 3 by hand: v[r] is the P&L of repetition r, so relabelling repetitions by a
    #   permutation p (record r -> p(r)) moves each value to slot p(r); every statistic depends only on
    #   the multiset of present values, so all other fields are unchanged.
    n, recs = case
    p = data.draw(st.permutations(list(range(n))))
    relabelled = [{**r, "repetition": p[r["repetition"]]} for r in recs]
    base = pnl_stability(recs, n_repetitions=n)
    out = pnl_stability(relabelled, n_repetitions=n)
    assert len(base) == len(out) == len(_first_appearance(recs))
    for b, o in zip(base, out, strict=True):
        moved = [None] * n
        for r, v in enumerate(b["values"]):
            moved[p[r]] = v
        assert o["values"] == moved
        h.assert_close({k: v for k, v in o.items() if k != "values"},
                       {k: v for k, v in b.items() if k != "values"}, rel=META_REL, abs_=META_ABS)
