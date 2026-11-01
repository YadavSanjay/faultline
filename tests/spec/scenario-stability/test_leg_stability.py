"""scenario-stability C2: ``stability.leg_stability`` gives each dual-RAG leg's spread per scenario.

Oracle: Requirement 2 by hand: a repetition is resolved for a scenario when it has a non-stopped record
(no record counts as stopped); per source the legs are the union over resolved records, sorted; each
leg's entry is ``spread`` over ``n_repetitions`` slots (``None`` when stopped or absent) plus
``n_missing = n_resolved - n_present``. Scenarios follow first appearance. ``ValueError`` for
``n_repetitions < 1``, a bad repetition, a duplicate (scenario, repetition), an unknown source key in a
resolved record and any move ``spread`` rejects.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.stability import leg_stability

TWO = ("macrorag", "xbrlrag")
DEFAULT_SOURCES = ("macrorag", "xbrlrag", "xbrlrag_seed")

# Metamorphic comparisons run the same records through the function twice; only the order in which
# floats are summed may differ, so relative 1e-9 / absolute 1e-12 covers rounding and nothing more.
META_REL = 1e-9
META_ABS = 1e-12

MOVE = st.integers(min_value=-400, max_value=400).map(lambda i: i / 1024)
LEG_MAP = st.one_of(st.none(), st.dictionaries(st.sampled_from(["eq:A", "eq:B", "ir:C"]), MOVE, max_size=3))
LEGS = st.one_of(st.none(), st.dictionaries(st.sampled_from(DEFAULT_SOURCES), LEG_MAP, max_size=3))


@st.composite
def _records(draw):
    """Valid records: distinct (scenario, repetition) pairs, any mix of stopped and resolved."""
    n = draw(st.integers(min_value=1, max_value=4))
    pairs = draw(st.lists(st.tuples(st.sampled_from(["S1", "S2", "S3"]), st.integers(0, n - 1)),
                          min_size=1, max_size=12, unique=True))
    recs = []
    for scen, rep in pairs:
        rec = {"repetition": rep, "scenario": scen}
        if draw(st.booleans()):
            rec["stopped"] = True
            if draw(st.booleans()):
                rec["legs"] = draw(LEGS)                   # ignored on a stopped record
        else:
            if draw(st.booleans()):
                rec["stopped"] = False
            rec["legs"] = draw(LEGS)
        recs.append(rec)
    return n, recs


def _first_appearance(recs):
    seen = []
    for r in recs:
        if r["scenario"] not in seen:
            seen.append(r["scenario"])
    return seen


@pytest.mark.criterion("C2")
def test_c2_point_two_scenarios(h):
    # oracle: Requirement 2 by hand (conftest.leg_expected): A n_resolved 2 / n_stopped 1;
    #   eq:XLK [-0.08, None, None] n_present 1 n_missing 1; eq:AMD [None, -0.15, None] n_missing 1;
    #   ir:USD10Y range 0.0010, mean 0.0045, std 0.0005 * sqrt(2), n_missing 0; eq:NVDA range 0.06,
    #   mean -0.23. B n_resolved 1 / n_stopped 2 with {} for both sources.
    h.assert_close(leg_stability(h.leg_records(), n_repetitions=3, sources=TWO), h.leg_expected())


@pytest.mark.criterion("C2")
def test_c2_point_legs_listed_in_sorted_order(h):
    # oracle: Requirement 2 by hand: legs in sorted order -> macrorag [eq:XLK, ir:USD10Y] although the
    #   record lists ir:USD10Y first; xbrlrag [eq:AMD, eq:NVDA] although eq:NVDA appears first.
    out = leg_stability(h.leg_records(), n_repetitions=3, sources=TWO)
    assert [row["scenario"] for row in out] == ["A", "B"]
    assert list(out[0]["macrorag"]) == ["eq:XLK", "ir:USD10Y"]
    assert list(out[0]["xbrlrag"]) == ["eq:AMD", "eq:NVDA"]


@pytest.mark.criterion("C2")
def test_c2_point_default_sources_include_the_seed(h):
    # oracle: Requirement 2 by hand: sources default to (macrorag, xbrlrag, xbrlrag_seed); a seed leg
    #   in repetition 0 only, with repetition 1 resolved and 2 absent -> eq:NVDA [-0.30, None, None],
    #   n_resolved 2, n_missing 1; macrorag and xbrlrag have no legs -> {}.
    recs = [
        {"repetition": 0, "scenario": "A", "legs": {"xbrlrag_seed": {"eq:NVDA": -0.30}}},
        {"repetition": 1, "scenario": "A", "legs": {"macrorag": None}},
    ]
    seed = h.entry([-0.30, None, None], mn=-0.30, mx=-0.30, rng=0.0, mean=-0.30, std=None, rel_range=0.0,
                   sign_flip=False, n_missing=1)
    expected = [{"scenario": "A", "n_repetitions": 3, "n_resolved": 2, "n_stopped": 1,
                 "macrorag": {}, "xbrlrag": {}, "xbrlrag_seed": {"eq:NVDA": seed}}]
    h.assert_close(leg_stability(recs, n_repetitions=3), expected)


@pytest.mark.criterion("C2")
def test_c2_point_none_legs_and_none_source_mean_no_legs(h):
    # oracle: Requirement 2 by hand: a resolved record with legs None, or a source mapped to None,
    #   still counts as resolved but contributes no legs; xbrlrag's eq:X is present only in repetition 1
    #   -> [None, 0.5] with n_resolved 2, n_missing 1; macrorag has no legs anywhere -> {}.
    recs = [
        {"repetition": 0, "scenario": "Z", "legs": None},
        {"repetition": 1, "scenario": "Z", "stopped": False, "legs": {"macrorag": None, "xbrlrag": {"eq:X": 0.5}}},
    ]
    x = h.entry([None, 0.5], mn=0.5, mx=0.5, rng=0.0, mean=0.5, std=None, rel_range=0.0, sign_flip=False,
                n_missing=1)
    expected = [{"scenario": "Z", "n_repetitions": 2, "n_resolved": 2, "n_stopped": 0,
                 "macrorag": {}, "xbrlrag": {"eq:X": x}}]
    h.assert_close(leg_stability(recs, n_repetitions=2, sources=TWO), expected)


@pytest.mark.criterion("C2")
def test_c2_point_stopped_record_legs_are_ignored(h):
    # oracle: Requirement 2 by hand: "A stopped record's legs, if any, are ignored" (and the unknown-source
    #   check applies to resolved records only) -> repetition 1's legs are dropped: eq:X [0.25, None],
    #   n_resolved 1, n_stopped 1, n_missing 0; the stopped-only leg eq:Y does not appear.
    recs = [
        {"repetition": 0, "scenario": "Z", "legs": {"macrorag": {"eq:X": 0.25}}},
        {"repetition": 1, "scenario": "Z", "stopped": True,
         "legs": {"macrorag": {"eq:X": 9.0, "eq:Y": 1.0}, "other": {"eq:Q": 1.0}}},
    ]
    x = h.entry([0.25, None], mn=0.25, mx=0.25, rng=0.0, mean=0.25, std=None, rel_range=0.0, sign_flip=False,
                n_missing=0)
    expected = [{"scenario": "Z", "n_repetitions": 2, "n_resolved": 1, "n_stopped": 1,
                 "macrorag": {"eq:X": x}, "xbrlrag": {}}]
    h.assert_close(leg_stability(recs, n_repetitions=2, sources=TWO), expected)


@pytest.mark.criterion("C2")
def test_c2_point_no_records_is_empty_list():
    # oracle: Requirement 2 by hand: no records -> [].
    # red-exempt: empty the correct answer for no records is the empty list (other tests pin non-empty input)
    assert leg_stability([], n_repetitions=3, sources=TWO) == []


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(
    ("n_repetitions", "records"),
    [
        (0, [{"repetition": 0, "scenario": "A", "legs": {}}]),                          # n_repetitions < 1
        (3, [{"repetition": True, "scenario": "A", "legs": {}}]),                       # bool repetition
        (3, [{"repetition": 1.0, "scenario": "A", "legs": {}}]),                        # float repetition
        (3, [{"repetition": 3, "scenario": "A", "legs": {}}]),                          # == n_repetitions
        (3, [{"repetition": -1, "scenario": "A", "legs": {}}]),                         # negative
        (3, [{"repetition": 0, "scenario": "A", "legs": {}},
             {"repetition": 0, "scenario": "A", "legs": {}}]),                          # duplicate
        (3, [{"repetition": 1, "scenario": "A", "legs": {"macrorag": {"eq:X": 0.1}}},
             {"repetition": 1, "scenario": "A", "stopped": True}]),                     # duplicate, one stopped
        (3, [{"repetition": 0, "scenario": "A", "legs": {"news": {"eq:X": 0.1}}}]),     # unknown source
        (3, [{"repetition": 0, "scenario": "A", "legs": {"macrorag": {"eq:X": float("nan")}}}]),  # NaN move
        (3, [{"repetition": 0, "scenario": "A", "legs": {"xbrlrag": {"eq:X": True}}}]),  # bool move
        (3, [{"repetition": 0, "scenario": "A", "legs": {"xbrlrag": {"eq:X": "0.1"}}}]),  # string move
    ],
)
def test_c2_point_invalid_input_raises(n_repetitions, records):
    # oracle: Requirement 2 by hand: each case is one ValueError bullet (see the comment on each case).
    with pytest.raises(ValueError, match=r"\S"):
        leg_stability(records, n_repetitions=n_repetitions, sources=TWO)


@pytest.mark.criterion("C2")
def test_c2_point_input_not_mutated(h):
    # oracle: Requirement 2 by hand: "The input is not mutated" -> the records equal their earlier deep
    #   copy and the result is still conftest.leg_expected.
    recs = h.leg_records()
    before = copy.deepcopy(recs)
    out = leg_stability(recs, n_repetitions=3, sources=TWO)
    assert recs == before
    h.assert_close(out, h.leg_expected())


@pytest.mark.criterion("C2")
@given(case=_records())
def test_c2_property_counts_add_up(case):
    # oracle: Requirement 2 by hand: one row per distinct scenario in first-appearance order;
    #   n_resolved + n_stopped == n_repetitions; every leg entry has n_present + n_missing == n_resolved
    #   and len(values) == n_repetitions; legs are sorted; every source key is present.
    n, recs = case
    out = leg_stability(recs, n_repetitions=n)
    assert [row["scenario"] for row in out] == _first_appearance(recs)
    for row in out:
        assert row["n_repetitions"] == n
        assert row["n_resolved"] + row["n_stopped"] == n
        for src in DEFAULT_SOURCES:
            legs = row[src]
            assert list(legs) == sorted(legs)
            for e in legs.values():
                assert e["n_present"] + e["n_missing"] == row["n_resolved"]
                assert len(e["values"]) == n
                assert e["n_present"] >= 1


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case=_records())
def test_c2_metamorphic_dropping_later_stopped_records(case, h):
    # oracle: Requirement 2 by hand: a repetition with no record counts as stopped exactly like a stopped
    #   record, so removing a stopped record whose scenario already appeared earlier (its row position is
    #   kept) leaves the output unchanged.
    n, recs = case
    seen: set[str] = set()
    kept = []
    for r in recs:
        if r.get("stopped", False) and r["scenario"] in seen:
            continue
        seen.add(r["scenario"])
        kept.append(r)
    full = leg_stability(recs, n_repetitions=n)
    assert len(full) == len(_first_appearance(recs))
    h.assert_close(leg_stability(kept, n_repetitions=n), full, rel=META_REL, abs_=META_ABS)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(case=_records(), data=st.data())
def test_c2_metamorphic_shuffling_changes_only_scenario_order(case, data, h):
    # oracle: Requirement 2 by hand: each row depends only on its scenario's records, so shuffling the
    #   records gives the same row per scenario; rows follow first appearance in the shuffled input.
    n, recs = case
    shuffled = data.draw(st.permutations(recs))
    a = leg_stability(recs, n_repetitions=n)
    b = leg_stability(shuffled, n_repetitions=n)
    assert [row["scenario"] for row in b] == _first_appearance(shuffled)
    assert len(a) == len(b) == len(_first_appearance(recs))
    by_a = {row["scenario"]: row for row in a}
    by_b = {row["scenario"]: row for row in b}
    h.assert_close(by_b, by_a, rel=META_REL, abs_=META_ABS)
