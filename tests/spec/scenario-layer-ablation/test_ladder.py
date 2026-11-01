"""scenario-layer-ablation C3: ``ladder_attribution`` over a layer ladder.

Requirement 2 of docs/specs/scenario-layer-ablation.md applied by hand. The pricing callable is a fake at
the pricing boundary: a fixed per-trade P&L table keyed by the enabled layer set, or a linear function
of layer indicators. Spearman is the Pearson correlation of average ranks (ties share the mean rank),
computed by hand in the point tests and by ``_spearman`` below (a restatement of that definition) in the
property test. scipy is not a project dependency, so it is not used.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.ablation import ladder_attribution

LAYERS = ("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state")
# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


def _table_pricer(table: dict[frozenset[str], dict[str, float]]):
    def pnl_for(layers: frozenset[str]) -> Mapping[str, float]:
        return dict(table[frozenset(layers)])

    return pnl_for


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    return ranks


def _spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(set(x)) < 2 or len(set(y)) < 2:
        return None
    rx, ry = _average_ranks(x), _average_ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy)


# Hand example: three trades, ladder macrorag -> news.
TABLE = {
    frozenset(): {"T1": 10.0, "T2": -5.0, "T3": 0.0},
    frozenset({"macrorag"}): {"T1": 12.0, "T2": -9.0, "T3": 1.0},
    frozenset({"macrorag", "news"}): {"T1": 4.0, "T2": -9.0, "T3": 6.0},
}


@pytest.mark.criterion("C3")
def test_c3_hand_example_fields():
    # oracle: Requirement 2 by hand.
    # base: total 10 - 5 + 0 = 5, marginal 5, rank None.
    # +macrorag: total 12 - 9 + 1 = 4, marginal -1, gross |2| + |-4| + |1| = 7;
    #   ranks prev (T1 3, T2 1, T3 2), cur (3, 1, 2) -> rho = 1.
    # +news: total 4 - 9 + 6 = 1, marginal -3, gross |-8| + 0 + |5| = 13;
    #   ranks prev (3, 1, 2), cur (2, 1, 3), d^2 = 1 + 0 + 1 = 2 -> rho = 1 - 6*2/(3*8) = 0.5.
    steps = ladder_attribution(_table_pricer(TABLE), ["macrorag", "news"])
    assert [s["layer"] for s in steps] == ["authored", "macrorag", "news"]
    assert [s["layers"] for s in steps] == [[], ["macrorag"], ["macrorag", "news"]]
    # rel 1e-12: sums of a few integers, exact in float
    assert [s["total_pnl"] for s in steps] == pytest.approx([5.0, 4.0, 1.0], rel=1e-12)
    assert [s["marginal_pnl"] for s in steps] == pytest.approx([5.0, -1.0, -3.0], rel=1e-12)
    assert [s["gross_reallocation"] for s in steps[1:]] == pytest.approx([7.0, 13.0], rel=1e-12)
    assert steps[0]["rank_corr_vs_prev"] is None
    # abs 1e-12: a ratio of small integers
    assert steps[1]["rank_corr_vs_prev"] == pytest.approx(1.0, abs=1e-12)
    assert steps[2]["rank_corr_vs_prev"] == pytest.approx(0.5, abs=1e-12)


@pytest.mark.criterion("C3")
def test_c3_layers_sorted_and_gross_over_union_of_trades():
    # oracle: Requirement 2 by hand. Ladder state -> crowding: layers lists are sorted
    # (["crowding", "state"]). Step 2 drops T3 and adds T4 (missing = 0):
    # gross = |1 - 3| + |0 - (-2)| + |0 - 4| + |5 - 0| = 2 + 2 + 4 + 5 = 13; total 6, marginal 6 - 5 = 1.
    # Step 1: gross = |3 - 1| + |-2 - (-1)| + |4 - 4| = 3; total 5, marginal 5 - 4 = 1.
    table = {
        frozenset(): {"T1": 1.0, "T2": -1.0, "T3": 4.0},
        frozenset({"state"}): {"T1": 3.0, "T2": -2.0, "T3": 4.0},
        frozenset({"state", "crowding"}): {"T1": 1.0, "T4": 5.0},
    }
    steps = ladder_attribution(_table_pricer(table), ["state", "crowding"])
    assert [s["layers"] for s in steps] == [[], ["state"], ["crowding", "state"]]
    assert [s["layer"] for s in steps] == ["authored", "state", "crowding"]
    # rel 1e-12: integer sums, exact in float
    assert [s["total_pnl"] for s in steps] == pytest.approx([4.0, 5.0, 6.0], rel=1e-12)
    assert [s["marginal_pnl"] for s in steps] == pytest.approx([4.0, 1.0, 1.0], rel=1e-12)
    assert [s["gross_reallocation"] for s in steps[1:]] == pytest.approx([3.0, 13.0], rel=1e-12)


@pytest.mark.criterion("C3")
def test_c3_spearman_ties_and_constant_vectors():
    # oracle: hand ranks with average ranks for ties. prev (A 1, B 1, C 2, D 3) -> ranks (1.5, 1.5, 3, 4);
    # cur (A 4, B 3, C 2, D 1) -> ranks (4, 3, 2, 1). Means 2.5. Deviations prev (-1, -1, .5, 1.5),
    # cur (1.5, .5, -.5, -1.5). cov = -1.5 - .5 - .25 - 2.25 = -4.5; var prev = 1 + 1 + .25 + 2.25 = 4.5;
    # var cur = 5 -> rho = -4.5 / sqrt(22.5) = -0.9486832980505138.
    # The third step is constant (all 7) -> None.
    table = {
        frozenset(): {"A": 1.0, "B": 1.0, "C": 2.0, "D": 3.0},
        frozenset({"news"}): {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0},
        frozenset({"news", "crowding"}): {"A": 7.0, "B": 7.0, "C": 7.0, "D": 7.0},
    }
    steps = ladder_attribution(_table_pricer(table), ["news", "crowding"])
    # abs 1e-12: closed-form ratio, a few ulps of rounding
    assert steps[1]["rank_corr_vs_prev"] == pytest.approx(-4.5 / math.sqrt(22.5), abs=1e-12)
    assert steps[2]["rank_corr_vs_prev"] is None


@pytest.mark.criterion("C3")
def test_c3_spearman_none_when_previous_is_constant():
    # oracle: Requirement 2: rank correlation is None when either vector is constant (here the previous).
    # gross = |1 - 2| + |2 - 2| + |3 - 2| = 2.
    table = {
        frozenset(): {"A": 2.0, "B": 2.0, "C": 2.0},
        frozenset({"macrorag"}): {"A": 1.0, "B": 2.0, "C": 3.0},
    }
    steps = ladder_attribution(_table_pricer(table), ["macrorag"])
    assert steps[1]["rank_corr_vs_prev"] is None
    # rel 1e-12: integer sum, exact
    assert steps[1]["gross_reallocation"] == pytest.approx(2.0, rel=1e-12)


@pytest.mark.criterion("C3")
def test_c3_bad_order_raises():
    # oracle: C3: an unknown or repeated layer in the order -> ValueError.
    pricer = _table_pricer(TABLE)
    for order in (["bogus"], ["macrorag", "bogus"], ["macrorag", "macrorag"], ["news", "macrorag", "news"]):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            ladder_attribution(pricer, order)


@pytest.mark.criterion("C3")
def test_c3_authored_in_order_raises():
    # oracle: Requirement 2: the base is implicit, so "authored" is not a valid id in the order -> ValueError.
    pricer = _table_pricer(TABLE)
    for order in (["authored"], ["authored", "macrorag"], ["macrorag", "authored"]):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            ladder_attribution(pricer, order)


@pytest.mark.criterion("C3")
def test_c3_base_step_has_no_gross_or_rank():
    # oracle: Requirement 2: the base step has no previous step -> gross_reallocation and
    # rank_corr_vs_prev are both None; its marginal is its total (10 - 5 + 0 = 5).
    steps = ladder_attribution(_table_pricer(TABLE), ["macrorag", "news"])
    assert steps[0]["layer"] == "authored"
    assert steps[0]["gross_reallocation"] is None
    assert steps[0]["rank_corr_vs_prev"] is None
    # rel 1e-12: integer sum, exact
    assert steps[0]["marginal_pnl"] == pytest.approx(5.0, rel=1e-12)


@pytest.mark.criterion("C3")
def test_c3_spearman_over_union_of_trades():
    # oracle: Requirement 2 by hand, Spearman over the union {T1, T2, T3, T4} with missing = 0.
    # prev (step "state") = (T1 3, T2 -2, T3 4, T4 0 [missing]) -> ranks (3, 1, 4, 2).
    # cur (step "crowding") = (T1 1, T2 0 [missing], T3 0 [missing], T4 5) -> 0, 0 tie at ranks 1, 2
    # -> (3, 1.5, 1.5, 4). Means 2.5. Deviations prev (.5, -1.5, 1.5, -.5), cur (.5, -1, -1, 1.5).
    # cov = .25 + 1.5 - 1.5 - .75 = -.5; var prev = .25 + 2.25 + 2.25 + .25 = 5; var cur = .25 + 1 + 1 + 2.25
    # = 4.5 -> rho = -.5 / sqrt(22.5) = -0.10540925533894598. (Over the intersection {T1} alone the
    # vectors would be constant -> None, so this tells the two readings apart.)
    # Step "state" vs base: same trades, prev (1, -1, 4) ranks (2, 1, 3), cur (3, -2, 4) ranks (2, 1, 3) -> 1.
    table = {
        frozenset(): {"T1": 1.0, "T2": -1.0, "T3": 4.0},
        frozenset({"state"}): {"T1": 3.0, "T2": -2.0, "T3": 4.0},
        frozenset({"state", "crowding"}): {"T1": 1.0, "T4": 5.0},
    }
    steps = ladder_attribution(_table_pricer(table), ["state", "crowding"])
    # abs 1e-12: closed-form ratio, a few ulps of rounding
    assert steps[1]["rank_corr_vs_prev"] == pytest.approx(1.0, abs=1e-12)
    assert steps[2]["rank_corr_vs_prev"] == pytest.approx(-0.5 / math.sqrt(22.5), abs=1e-12)


_TRADES = ["T1", "T2", "T3", "T4", "T5"]
_INT = st.integers(min_value=-50, max_value=50).map(float)


@st.composite
def _ladder_case(draw: st.DrawFn):
    order = draw(st.lists(st.sampled_from(LAYERS), unique=True, min_size=1, max_size=len(LAYERS)))
    trades = _TRADES[: draw(st.integers(min_value=1, max_value=len(_TRADES)))]
    base = {t: draw(_INT) for t in trades}
    weight = {(layer, t): draw(_INT) for layer in order for t in trades}
    cross = {t: draw(_INT) for t in trades}
    return order, trades, base, weight, cross


@pytest.mark.criterion("C3")
@given(case=_ladder_case())
def test_c3_property_telescoping_and_definitions(case):
    # oracle: Requirement 2 restated per step, telescoping Σ marginal = last total as the property.
    # Fake pricer (linear in layer indicators, plus one interaction term so the ladder is not additive):
    # pnl_S(t) = base(t) + Σ_{l in S} w(l, t) + cross(t) * [|S| >= 2].
    order, trades, base, weight, cross = case

    def pnl_for(layers: frozenset[str]) -> Mapping[str, float]:
        return {
            t: base[t] + sum(weight[(layer, t)] for layer in layers) + (cross[t] if len(layers) >= 2 else 0.0)
            for t in trades
        }

    steps = ladder_attribution(pnl_for, order)
    assert len(steps) == len(order) + 1
    vectors = [pnl_for(frozenset(order[:k])) for k in range(len(order) + 1)]
    for k, step in enumerate(steps):
        assert step["layer"] == ("authored" if k == 0 else order[k - 1])
        assert step["layers"] == sorted(order[:k])
        total = sum(vectors[k].values())
        # rel 1e-12: integer-valued sums, exact in float
        assert step["total_pnl"] == pytest.approx(total, rel=1e-12, abs=1e-12)
        if k == 0:
            assert step["marginal_pnl"] == pytest.approx(total, rel=1e-12, abs=1e-12)
            assert step["rank_corr_vs_prev"] is None
            assert step["gross_reallocation"] is None
            continue
        prev = vectors[k - 1]
        assert step["marginal_pnl"] == pytest.approx(total - sum(prev.values()), rel=1e-12, abs=1e-12)
        gross = sum(abs(vectors[k][t] - prev[t]) for t in trades)
        assert step["gross_reallocation"] == pytest.approx(gross, rel=1e-12, abs=1e-12)
        rho = _spearman([prev[t] for t in trades], [vectors[k][t] for t in trades])
        if rho is None:
            assert step["rank_corr_vs_prev"] is None
        else:
            # abs 1e-12: ratio of rank sums, a few ulps of rounding
            assert step["rank_corr_vs_prev"] == pytest.approx(rho, abs=1e-12)
    # telescoping
    assert sum(s["marginal_pnl"] for s in steps) == pytest.approx(steps[-1]["total_pnl"], rel=1e-12, abs=1e-12)
