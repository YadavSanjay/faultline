"""measurement-views C1: ``report_views.coverage_view`` builds the coverage view.

Oracle: Requirement 1 by hand: coverage, n_directions, merge_pairs, missing copied ([] for an absent
list); directions in ``index`` order with the five named keys; uncovered_share = sum of variance_share
over the uncovered directions (0.0 when none); diversity's participation_ratio / target / in_target;
losses from diversity.losses ordered by loss descending, then scenario name. Raises ValueError for a
missing coverage, missing directions or a duplicate index. Inputs are hand-written dicts (conftest).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.report_views import coverage_view


@pytest.mark.criterion("C1")
def test_c1_point_hand_built_report(h):
    # oracle: Requirement 1 by hand (conftest.coverage_view): directions reordered 2, 3, 1 -> 1, 2, 3 and
    #   cut to the five named keys; uncovered_share = 0.125 (only index 2 uncovered); losses ai_bust 12.5,
    #   then the 8.0 tie by name: rates_up, taiwan; one merge pair and missing copied.
    h.assert_close(coverage_view(h.coverage_report()), h.coverage_view())


@pytest.mark.criterion("C1")
def test_c1_point_absent_lists_and_nothing_uncovered(h):
    # oracle: Requirement 1 by hand: no merge_pairs / missing keys -> [] and []; no uncovered direction
    #   -> uncovered_share 0.0; everything else as in the hand view with every uncovered flag false.
    rep = h.coverage_report()
    del rep["merge_pairs"]
    del rep["missing"]
    rep["directions"][0]["uncovered"] = False
    expected = h.coverage_view()
    expected["merge_pairs"] = []
    expected["missing"] = []
    expected["uncovered_share"] = 0.0
    expected["directions"][1]["uncovered"] = False
    h.assert_close(coverage_view(rep), expected)


@pytest.mark.criterion("C1")
def test_c1_point_two_uncovered_directions(h):
    # oracle: Requirement 1 by hand: index 2 (0.125) and index 3 (0.0625) uncovered ->
    #   uncovered_share = 0.125 + 0.0625 = 0.1875.
    rep = h.coverage_report()
    rep["directions"][1]["uncovered"] = True
    out = coverage_view(rep)
    # tolerance: dyadic sum, exact; approx only guards summation order.
    assert out["uncovered_share"] == pytest.approx(0.1875, rel=1e-12, abs=1e-12)
    assert [d["uncovered"] for d in out["directions"]] == [False, True, True]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("key", ["coverage", "directions"])
def test_c1_point_raises_for_missing_key(h, key):
    # oracle: Requirement 1 by hand: a report without coverage or directions is refused (ValueError).
    rep = h.coverage_report()
    del rep[key]
    with pytest.raises(ValueError, match=r"\S"):
        coverage_view(rep)


@pytest.mark.criterion("C1")
def test_c1_point_raises_for_duplicate_index(h):
    # oracle: Requirement 1 by hand: two directions with index 1 are refused (ValueError).
    rep = h.coverage_report()
    rep["directions"][1]["index"] = 1
    with pytest.raises(ValueError, match=r"\S"):
        coverage_view(rep)


@pytest.mark.criterion("C1")
def test_c1_point_report_not_mutated(h):
    # oracle: Requirement 1 by hand: the report equals a deep copy taken before the call (directions are
    #   not sorted in place, losses not replaced); the view is the hand view.
    rep = h.coverage_report()
    before = copy.deepcopy(rep)
    out = coverage_view(rep)
    assert rep == before
    h.assert_close(out, h.coverage_view())


DYADIC = st.integers(0, 64).map(lambda k: k / 64)
NAMES = ["ai_bust", "credit_crunch", "rates_up", "taiwan", "usd_spike"]


@st.composite
def _coverage_report(draw):
    n = draw(st.integers(1, 6))
    indices = draw(st.permutations(list(range(1, n + 1))))
    directions = [{"index": i, "variance": 1.0, "variance_share": draw(DYADIC), "spanned_share": draw(DYADIC),
                   "uncovered": draw(st.booleans()), "book_exposure": 0.5,
                   "top_loadings": [{"trade_id": f"T{i}", "loading": draw(DYADIC)}]} for i in indices]
    names = draw(st.lists(st.sampled_from(NAMES), unique=True))
    losses = {nm: draw(st.integers(-8, 8).map(lambda k: k / 4)) for nm in names}
    return {"coverage": draw(DYADIC), "n_directions": n, "directions": directions,
            "diversity": {"participation_ratio": 1.5, "target": [1.0, 2.0], "in_target": True, "losses": losses},
            "merge_pairs": [], "missing": []}


@pytest.mark.criterion("C1")
@given(rep=_coverage_report())
def test_c1_property_uncovered_share_is_sum_over_uncovered(rep):
    # oracle: Requirement 1 by hand: uncovered_share = sum of variance_share over uncovered directions,
    #   so 0 <= uncovered_share <= sum of all variance_share (shares are non-negative); directions come out
    #   in index order 1..n.
    out = coverage_view(rep)
    expected = sum(d["variance_share"] for d in rep["directions"] if d["uncovered"])
    total = sum(d["variance_share"] for d in rep["directions"])
    # tolerance: dyadic k/64 shares, the sums are exact; approx only guards summation order.
    assert out["uncovered_share"] == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert 0.0 <= out["uncovered_share"] <= total
    assert [d["index"] for d in out["directions"]] == list(range(1, len(rep["directions"]) + 1))


@pytest.mark.criterion("C1")
@given(rep=_coverage_report())
def test_c1_property_losses_sorted_permutation(rep):
    # oracle: Requirement 1 by hand: losses are the report's (scenario, loss) pairs, ordered by loss
    #   descending and then by scenario name ascending.
    out = coverage_view(rep)
    pairs = [(e["scenario"], e["loss"]) for e in out["losses"]]
    assert sorted(pairs) == sorted(rep["diversity"]["losses"].items())
    assert pairs == sorted(pairs, key=lambda p: (-p[1], p[0]))
    for e in out["losses"]:
        assert set(e) == {"scenario", "loss"}


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(rep=_coverage_report(), shuffle=st.randoms(use_true_random=False))
def test_c1_property_input_unchanged_and_order_invariant(rep, shuffle):
    # oracle: Requirement 1 by hand: the report equals a deep copy taken before the call, and reordering
    #   the report's directions (directions are sorted by index) gives the same view.
    before = copy.deepcopy(rep)
    out = coverage_view(rep)
    assert rep == before
    shuffled = copy.deepcopy(rep)
    shuffle.shuffle(shuffled["directions"])
    assert coverage_view(shuffled) == out
    assert len(out["directions"]) == len(rep["directions"])
