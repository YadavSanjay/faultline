"""measurement-views C2: ``report_views.decision_view`` builds the decision-impact view.

Oracle: Requirement 2 by hand: tier copied; books from the single ``kind == "stress"`` set in the order
balanced, core, aggressive, risky, then other books by name, with stress_budget, the unconstrained
worst_scenario / worst_loss / n_over_budget = len(over_budget), change.binding from / to and
n_trades_changed ([] with no stress set); without one row per source in report order, books in the
same order, binding_changed = from != to, size_ratio = mean of multipliers[*].ratio (1.0 when none);
[] when without_each_source is absent. ValueError for two stress sets. Inputs: conftest dicts.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.report_views import decision_view


@pytest.mark.criterion("C2")
def test_c2_point_hand_built_report(h):
    # oracle: Requirement 2 by hand (conftest.decision_view): ablation set ignored; books balanced, core,
    #   aggressive, risky, income; core n_over_budget = 3 (unconstrained list, not the 7 constrained
    #   decoys); without xbrlrag: balanced (0.75 + 1.25) / 2 = 1.0, core 2.0, risky (0.5 + 1.5 + 0.25) / 3
    #   = 0.75; macrorag: balanced (1 + 0.5 + 0.75 + 0.25) / 4 = 0.625, aggressive no multipliers -> 1.0,
    #   income 0.5.
    h.assert_close(decision_view(h.decision_report()), h.decision_view())


@pytest.mark.criterion("C2")
def test_c2_point_no_stress_set_and_no_without(h):
    # oracle: Requirement 2 by hand: only the ablation set -> books []; no without_each_source -> without
    #   []; tier still copied.
    rep = h.decision_report()
    rep["sets"] = rep["sets"][:1]
    del rep["without_each_source"]
    h.assert_close(decision_view(rep), {"tier": "adverse", "books": [], "without": []})


@pytest.mark.criterion("C2")
def test_c2_point_no_stress_set_keeps_without(h):
    # oracle: Requirement 2 by hand: books [] without a stress set; the without rows are unaffected.
    rep = h.decision_report()
    rep["sets"] = rep["sets"][:1]
    expected = h.decision_view()
    expected["books"] = []
    h.assert_close(decision_view(rep), expected)


@pytest.mark.criterion("C2")
def test_c2_point_raises_for_two_stress_sets(h):
    # oracle: Requirement 2 by hand: a second set with kind "stress" is refused (ValueError).
    rep = h.decision_report()
    rep["sets"].append(copy.deepcopy(rep["sets"][1]) | {"label": "authored-2"})
    with pytest.raises(ValueError, match=r"\S"):
        decision_view(rep)


@pytest.mark.criterion("C2")
def test_c2_point_report_not_mutated(h):
    # oracle: Requirement 2 by hand: the report equals a deep copy taken before the call; the view is the
    #   hand view.
    rep = h.decision_report()
    before = copy.deepcopy(rep)
    out = decision_view(rep)
    assert rep == before
    h.assert_close(out, h.decision_view())


BOOKS = ["balanced", "core", "aggressive", "risky", "alpha", "income", "zeta"]
BINDINGS = st.sampled_from(["var_budget", "es_budget", "stress", "gross_leverage"])
RATIO = st.integers(1, 32).map(lambda k: k / 8)


def _expected_order(books):
    std = [b for b in ("balanced", "core", "aggressive", "risky") if b in books]
    return std + sorted(b for b in books if b not in std)


@st.composite
def _decision_report(draw):
    stress_books = draw(st.lists(st.sampled_from(BOOKS), unique=True, min_size=1))
    profiles = {b: {"stress_budget": 8.0,
                    "unconstrained": {"binding": "var_budget", "worst_scenario": "s", "worst_loss": 1.0,
                                      "over_budget": ["s"] * draw(st.integers(0, 3))},
                    "change": {"binding": {"from": draw(BINDINGS), "to": draw(BINDINGS)},
                               "n_trades_changed": draw(st.integers(0, 4)), "multipliers": {}}}
                for b in stress_books}
    without = {}
    for src in draw(st.lists(st.sampled_from(["xbrlrag", "macrorag", "events", "options"]), unique=True)):
        without[src] = {
            b: {"binding": {"from": draw(BINDINGS), "to": draw(BINDINGS)},
                "multipliers": {f"T{i}": {"from": 1.0, "to": r, "ratio": r}
                                for i, r in enumerate(draw(st.lists(RATIO, max_size=4)))}}
            for b in draw(st.lists(st.sampled_from(BOOKS), unique=True))}
    return {"tier": "adverse", "sets": [{"kind": "stress", "label": "authored", "profiles": profiles}],
            "without_each_source": without}


@pytest.mark.criterion("C2")
@given(rep=_decision_report())
def test_c2_property_binding_changed_and_size_ratio(rep):
    # oracle: Requirement 2 by hand: per source and book, binding_changed is true exactly when from != to,
    #   and size_ratio = sum(ratios) / count (1.0 for no multipliers).
    out = decision_view(rep)
    assert [r["source"] for r in out["without"]] == list(rep["without_each_source"])
    for row in out["without"]:
        by_book = rep["without_each_source"][row["source"]]
        for b in row["books"]:
            src = by_book[b["book"]]
            assert b["binding_from"] == src["binding"]["from"]
            assert b["binding_to"] == src["binding"]["to"]
            assert b["binding_changed"] is (src["binding"]["from"] != src["binding"]["to"])
            ratios = [m["ratio"] for m in src["multipliers"].values()]
            expected = sum(ratios) / len(ratios) if ratios else 1.0
            # tolerance: ratios are k/8 and the count <= 4, so the mean is exact; approx guards order.
            assert b["size_ratio"] == pytest.approx(expected, rel=1e-12, abs=1e-12)


@pytest.mark.criterion("C2")
@given(rep=_decision_report())
def test_c2_property_book_order(rep):
    # oracle: Requirement 2 by hand: books (and each source's books) come in the order balanced, core,
    #   aggressive, risky, then the others by name; n_over_budget = len(unconstrained.over_budget).
    out = decision_view(rep)
    profiles = rep["sets"][0]["profiles"]
    assert [b["book"] for b in out["books"]] == _expected_order(list(profiles))
    for b in out["books"]:
        assert b["n_over_budget"] == len(profiles[b["book"]]["unconstrained"]["over_budget"])
    for row in out["without"]:
        assert [b["book"] for b in row["books"]] == _expected_order(list(rep["without_each_source"][row["source"]]))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(rep=_decision_report())
def test_c2_property_input_unchanged_and_ablation_ignored(rep):
    # oracle: Requirement 2 by hand: the report equals a deep copy taken before the call, and adding an
    #   ablation set (kind "ablation") leaves the view unchanged.
    before = copy.deepcopy(rep)
    out = decision_view(rep)
    assert rep == before
    assert len(out["books"]) == len(rep["sets"][0]["profiles"])
    more = copy.deepcopy(rep)
    more["sets"].insert(0, {"kind": "ablation", "label": "without:xbrlrag",
                            "profiles": copy.deepcopy(rep["sets"][0]["profiles"]) | {"omega": {}}})
    assert decision_view(more) == out
