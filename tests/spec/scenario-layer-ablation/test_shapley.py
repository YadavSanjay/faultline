"""scenario-layer-ablation C4: exact Shapley attribution of total P&L to layers.

Oracles: Shapley (1953); the three-player glove game as given in standard cooperative-game texts
(v(S) = 1 iff S holds the left glove `a` and at least one right glove `b`, `c`: phi = (2/3, 1/6, 1/6));
the additive game; and the efficiency, symmetry and null-player axioms as properties on random games.
Games are synthetic callables on frozensets; at most 5 players to keep enumeration small.
"""

from __future__ import annotations

from itertools import combinations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.ablation import shapley_attribution

_VALUE = st.integers(min_value=-1000, max_value=1000).map(lambda i: i / 8)
# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


def _subsets(players: list[str]) -> list[frozenset[str]]:
    return [frozenset(c) for r in range(len(players) + 1) for c in combinations(players, r)]


@pytest.mark.criterion("C4")
def test_c4_glove_game():
    # oracle: glove game (standard texts): a = 2/3, b = c = 1/6.
    def v(s: frozenset[str]) -> float:
        return 1.0 if "a" in s and ("b" in s or "c" in s) else 0.0

    phi = shapley_attribution(v, ["a", "b", "c"])
    assert set(phi) == {"a", "b", "c"}
    # abs 1e-12: averages of six 0/1 marginals
    assert phi["a"] == pytest.approx(2 / 3, abs=1e-12)
    assert phi["b"] == pytest.approx(1 / 6, abs=1e-12)
    assert phi["c"] == pytest.approx(1 / 6, abs=1e-12)


@pytest.mark.criterion("C4")
def test_c4_additive_game_with_nonzero_base():
    # oracle: additive game v(S) = c0 + sum_{i in S} c_i -> phi_i = c_i (every marginal is c_i); the base
    # c0 = 10 is the reference v(empty) and is not attributed.
    c = {"macrorag": 1.5, "news": -2.0, "state": 4.0}

    def v(s: frozenset[str]) -> float:
        return 10.0 + sum(c[p] for p in s)

    phi = shapley_attribution(v, list(c))
    assert set(phi) == set(c)
    for p, expected in c.items():
        # abs 1e-12: sums of dyadic decimals, exact
        assert phi[p] == pytest.approx(expected, abs=1e-12)


@pytest.mark.criterion("C4")
def test_c4_repeated_players_raise():
    # oracle: C4: repeated player ids -> ValueError.
    for players in (["a", "a"], ["a", "b", "a"], ["news", "state", "state"]):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            shapley_attribution(lambda s: float(len(s)), players)


@st.composite
def _game(draw: st.DrawFn):
    n = draw(st.integers(min_value=1, max_value=5))
    players = [f"p{i}" for i in range(n)]
    table = {s: draw(_VALUE) for s in _subsets(players)}
    return players, table


@pytest.mark.criterion("C4")
@given(game=_game())
def test_c4_property_efficiency(game):
    # oracle: efficiency axiom: sum phi = v(all) - v(empty).
    players, table = game
    phi = shapley_attribution(lambda s: table[frozenset(s)], players)
    assert set(phi) == set(players)
    # abs 1e-9: spec tolerance; sums of up to 120 weighted marginals
    assert sum(phi[p] for p in players) == pytest.approx(
        table[frozenset(players)] - table[frozenset()], abs=1e-9
    )


@pytest.mark.criterion("C4")
@given(n_rest=st.integers(min_value=0, max_value=3), data=st.data())
def test_c4_property_symmetry(n_rest: int, data: st.DataObject):
    # oracle: symmetry axiom: x and y are interchangeable (v depends on S only through |S & {x, y}| and the
    # rest) -> phi_x = phi_y; efficiency still holds.
    rest = [f"r{i}" for i in range(n_rest)]
    g = {(k, s): data.draw(_VALUE) for k in range(3) for s in _subsets(rest)}

    def v(s: frozenset[str]) -> float:
        return g[(len(s & {"x", "y"}), frozenset(s - {"x", "y"}))]

    players = ["x", *rest, "y"]
    phi = shapley_attribution(v, players)
    # abs 1e-9: spec tolerance
    assert phi["x"] == pytest.approx(phi["y"], abs=1e-9)
    assert sum(phi.values()) == pytest.approx(v(frozenset(players)) - v(frozenset()), abs=1e-9)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(game=_game(), position=st.integers(min_value=0, max_value=5))
def test_c4_metamorphic_null_player(game, position: int):
    # oracle: null-player axiom: adding a player z that never changes v gives phi_z = 0 and leaves every
    # other phi unchanged (the game on the others is the same game).
    players, table = game

    def v_null(s: frozenset[str]) -> float:
        return table[frozenset(s - {"z"})]

    with_null = [*players[:position], "z", *players[position:]]
    phi = shapley_attribution(lambda s: table[frozenset(s)], players)
    phi_null = shapley_attribution(v_null, with_null)
    # abs 1e-9: spec tolerance
    assert phi_null["z"] == pytest.approx(0.0, abs=1e-9)
    for p in players:
        assert phi_null[p] == pytest.approx(phi[p], abs=1e-9)
