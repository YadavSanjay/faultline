"""scenario-historical-replay C3: splitting episode moves into replay drivers and idiosyncratic exclusions.

Oracle: Requirement 3 by hand: ``drivers`` holds every move not labelled idiosyncratic, ``excluded`` the
labelled ones that had a move; a label without a move is ignored. The two outputs partition the input as a
property.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.replay import replay_drivers

MOVES = {"fx:JPY": 0.0625, "eq:XLK": -0.125, "eq:INTC": -0.25, "eq:SMH": -0.1875, "ir:DGS10": 0.0}


@pytest.mark.criterion("C3")
def test_c3_point_labelled_company_move_is_excluded():
    # oracle: Requirement 3 by hand (crowded_unwind): eq:INTC is labelled and moved, so it is excluded;
    # eq:AMD is labelled but has no move, so it is ignored; the rest are drivers with their values.
    labels = {"eq:INTC": "earnings miss 2024-08-01", "eq:AMD": "not in the episode"}
    before = copy.deepcopy(MOVES)
    drivers, excluded = replay_drivers(MOVES, labels)
    assert before == MOVES
    assert drivers == {"fx:JPY": 0.0625, "eq:XLK": -0.125, "eq:SMH": -0.1875, "ir:DGS10": 0.0}
    assert excluded == {"eq:INTC": -0.25}


@pytest.mark.criterion("C3")
def test_c3_point_any_iterable_of_labels():
    # oracle: Requirement 3 by hand: labels given as a generator; no labels at all keeps every move a driver.
    drivers, excluded = replay_drivers(MOVES, (f for f in ["eq:SMH", "ir:DGS10", "eq:MU"]))
    assert drivers == {"fx:JPY": 0.0625, "eq:XLK": -0.125, "eq:INTC": -0.25}
    assert excluded == {"eq:SMH": -0.1875, "ir:DGS10": 0.0}
    drivers, excluded = replay_drivers(MOVES, [])
    assert drivers == MOVES
    assert excluded == {}


_NAMES = ["eq:XLK", "eq:INTC", "eq:NVDA", "fx:JPY", "ir:DGS10", "credit", "eqvol", "eq:SMH"]
_V = st.integers(min_value=-64, max_value=64).map(lambda i: i / 64)


@pytest.mark.criterion("C3")
@given(
    moves=st.dictionaries(st.sampled_from(_NAMES), _V, min_size=1),
    labels=st.lists(st.sampled_from([*_NAMES, "eq:AMD", "eq:MU"]), unique=True),
)
def test_c3_property_partition_keeps_values(moves: dict[str, float], labels: list[str]):
    # oracle: C3 property: drivers and excluded are disjoint, their union is the input's keys, every value is
    # the input's, and excluded is exactly the labelled keys that had a move.
    before = dict(moves)
    drivers, excluded = replay_drivers(moves, labels)
    assert moves == before
    assert not set(drivers) & set(excluded)
    assert set(drivers) | set(excluded) == set(moves)
    assert set(excluded) == set(moves) & set(labels)
    assert {**drivers, **excluded} == moves
