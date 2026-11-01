"""scenario-decision-impact C1: the scenario with the largest book loss and that loss.

Oracle: Requirement 1 by hand. The book loss of scenario ``s`` is ``-sum_i m_i p_si``; on small matrices of
dyadic values the sums are exact, so the hand totals are written in each test. Properties: scaling the
multipliers by ``c > 0`` scales the loss by ``c`` and keeps the scenario; permuting the scenario rows with
their names changes neither; appending a scenario that loses less changes nothing.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.sizing.stress_constraint import worst_stress

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C1")
def test_c1_point_largest_loss():
    # oracle: by hand, m = (1, 2, 0.5):
    #   up:   1*4    + 2*1    + 0.5*(-2) =  4 + 2 - 1   =  5   -> loss -5
    #   down: 1*(-3) + 2*(-1) + 0.5*2    = -3 - 2 + 1   = -4   -> loss  4
    #   rate: 1*(-1) + 2*(-2.5) + 0.5*(-4) = -1 - 5 - 2 = -8   -> loss  8 (the worst)
    m = np.array([1.0, 2.0, 0.5])
    pnl = np.array([[4.0, 1.0, -2.0], [-3.0, -1.0, 2.0], [-1.0, -2.5, -4.0]])
    name, loss = worst_stress(m, pnl, ["up", "down", "rate"])
    assert name == "rate"
    # rel 1e-12: spec tolerance (the dyadic sums are exact)
    assert loss == pytest.approx(8.0, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_point_every_scenario_gains_gives_negative_loss():
    # oracle: by hand, m = (2, 1): a: 2*1 + 1*0.5 = 2.5 (loss -2.5); b: 2*0.25 + 1*0.25 = 0.75 (loss -0.75);
    # c: 2*3 + 1*1 = 7 (loss -7). The largest loss is the smallest gain, b, at -0.75.
    m = np.array([2.0, 1.0])
    pnl = np.array([[1.0, 0.5], [0.25, 0.25], [3.0, 1.0]])
    name, loss = worst_stress(m, pnl, ["a", "b", "c"])
    assert name == "b"
    # rel 1e-12: spec tolerance (exact dyadic sums)
    assert loss == pytest.approx(-0.75, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_point_tie_goes_to_first_name():
    # oracle: by hand, m = (1, 1): x: -2 + 0 = -2 (loss 2); y: -1 - 1 = -2 (loss 2); z: -0.5 - 0.5 = -1
    # (loss 1). x and y tie at 2; the first in names wins, whatever order the names sort in.
    m = np.array([1.0, 1.0])
    pnl = np.array([[-2.0, 0.0], [-1.0, -1.0], [-0.5, -0.5]])
    name, loss = worst_stress(m, pnl, ["zeta", "alpha", "mid"])
    assert name == "zeta"
    # rel 1e-12: spec tolerance (exact dyadic sums)
    assert loss == pytest.approx(2.0, rel=1e-12)
    name2, loss2 = worst_stress(m, pnl[[1, 0, 2]], ["alpha", "zeta", "mid"])
    assert name2 == "alpha"
    assert loss2 == pytest.approx(2.0, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_point_single_scenario_and_zero_multiplier():
    # oracle: by hand, one scenario, m = (0, 3): 0*(-100) + 3*0.5 = 1.5 (loss -1.5); a zero multiplier
    # removes that trade's loss.
    name, loss = worst_stress(np.array([0.0, 3.0]), np.array([[-100.0, 0.5]]), ["only"])
    assert name == "only"
    # rel 1e-12: spec tolerance (exact dyadic sums)
    assert loss == pytest.approx(-1.5, rel=1e-12)


@pytest.mark.criterion("C1")
def test_c1_refusals():
    # oracle: Requirement 1: an empty scenario matrix, mismatched shapes or a non-finite value raises
    # ValueError.
    m = np.array([1.0, 2.0])
    pnl = np.array([[1.0, -1.0], [-2.0, 0.5]])
    names = ["a", "b"]
    cases = [
        (m, np.zeros((0, 2)), []),  # empty scenario matrix
        (np.array([1.0, 2.0, 3.0]), pnl, names),  # multipliers longer than the trades
        (np.array([1.0]), pnl, names),  # multipliers shorter than the trades
        (m, pnl, ["a"]),  # fewer names than scenarios
        (m, pnl, ["a", "b", "c"]),  # more names than scenarios
        (m, np.array([1.0, -1.0]), ["a"]),  # scenario P&L not a 2-D matrix
    ]
    for bad in (np.nan, np.inf, -np.inf):
        cases.append((np.array([1.0, bad]), pnl, names))
        cases.append((m, np.array([[1.0, -1.0], [bad, 0.5]]), names))
    for mult, mat, nm in cases:
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            worst_stress(mult, mat, nm)


def _book(seed: int, scenarios: int, trades: int) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Dyadic multipliers and P&L (multiples of 1/4 up to 64) so every book sum is exact."""
    rng = np.random.default_rng(seed)
    m = rng.integers(0, 17, size=trades) / 4.0
    pnl = rng.integers(-256, 257, size=(scenarios, trades)) / 4.0
    return m, pnl, [f"s{k}" for k in range(scenarios)]


def _hand_worst(m: np.ndarray, pnl: np.ndarray, names: list[str]) -> tuple[str, float]:
    """Requirement 1 by explicit loop: largest -sum_i m_i p_si, strictly larger to replace (first wins)."""
    best_name, best_loss = names[0], -sum(float(m[i]) * float(pnl[0, i]) for i in range(len(m)))
    for k in range(1, len(names)):
        loss = -sum(float(m[i]) * float(pnl[k, i]) for i in range(len(m)))
        if loss > best_loss:
            best_name, best_loss = names[k], loss
    return best_name, best_loss


_CASE = st.tuples(
    st.integers(min_value=0, max_value=2**32 - 1),  # seed
    st.integers(min_value=1, max_value=6),  # scenarios
    st.integers(min_value=1, max_value=5),  # trades
)


@pytest.mark.criterion("C1")
@given(case=_CASE)
def test_c1_property_matches_explicit_loop(case: tuple[int, int, int]):
    # oracle: Requirement 1 computed by an explicit loop in the test (first strict maximum wins) on exact
    # dyadic inputs, so ties (common with small integers) are checked too.
    seed, k, n = case
    m, pnl, names = _book(seed, k, n)
    want_name, want_loss = _hand_worst(m, pnl, names)
    name, loss = worst_stress(m, pnl, names)
    assert name == want_name
    # abs 1e-12: exact dyadic sums (the loss may be zero, so an absolute bound)
    assert loss == pytest.approx(want_loss, rel=1e-12, abs=1e-12)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(case=_CASE, c=st.sampled_from([0.125, 0.5, 2.0, 8.0, 1024.0]), perm_seed=st.integers(0, 2**32 - 1))
def test_c1_metamorphic_scale_permute_append(case: tuple[int, int, int], c: float, perm_seed: int):
    # oracle: C1 properties: m -> c m with c > 0 scales every loss by c, so the worst scenario is kept and
    # its loss scales by c; permuting rows with their names keeps the worst scenario (by name, when its
    # loss is unique) and its loss; appending a scenario that loses less (the worst row's P&L plus |m|)
    # changes nothing. Anchored to the explicit-loop oracle.
    seed, k, n = case
    m, pnl, names = _book(seed, k, n)
    base_name, base_loss = worst_stress(m, pnl, names)
    want_name, want_loss = _hand_worst(m, pnl, names)
    assert base_name == want_name
    # abs 1e-12: exact dyadic sums (the loss may be zero)
    assert base_loss == pytest.approx(want_loss, rel=1e-12, abs=1e-12)

    s_name, s_loss = worst_stress(c * m, pnl, names)
    assert s_name == base_name
    # rel 1e-12: spec tolerance (powers of two keep the sums exact)
    assert s_loss == pytest.approx(c * base_loss, rel=1e-12, abs=1e-12)

    order = np.random.default_rng(perm_seed).permutation(k)
    p_name, p_loss = worst_stress(m, pnl[order], [names[j] for j in order])
    assert p_loss == pytest.approx(base_loss, rel=1e-12, abs=1e-12)
    # losses in units of 1/16 are exact integers (multiples of 1/4 times multiples of 1/4)
    units = np.rint(-16.0 * (pnl @ m)).astype(np.int64)
    if int(np.count_nonzero(units == units.max())) == 1:
        assert p_name == base_name

    worst_row = pnl[names.index(base_name)]
    gentler = worst_row + 1.0  # loss falls by sum(m); strictly when any multiplier is non-zero
    if float(np.sum(m)) > 0.0:
        a_name, a_loss = worst_stress(m, np.vstack([pnl, gentler]), [*names, "gentler"])
        assert a_name == base_name
        assert a_loss == pytest.approx(base_loss, rel=1e-12, abs=1e-12)
