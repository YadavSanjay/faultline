"""scenario-decision-impact C2: the factor that brings the worst stress loss within the budget.

Oracle: Requirement 2 by hand. With the worst book loss ``L = max_s -sum_i m_i p_si`` the scale is
``budget / L`` when ``L > budget`` and ``1`` otherwise; on dyadic inputs ``L`` is exact, so the hand values are
written in each test. Properties: after scaling, the worst loss is at most the budget and equals it when the
scale is below 1; the scale is in ``(0, 1]``; scaling the budget and the P&L matrix by the same ``c > 0`` leaves
it unchanged; a larger budget never gives a smaller scale.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.sizing.stress_constraint import stress_scale, worst_stress

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

# m = (1, 2, 0.5) on these rows gives book P&L 5, -4 and -8: losses -5, 4 and 8, so the worst loss is 8.
M = np.array([1.0, 2.0, 0.5])
PNL = np.array([[4.0, 1.0, -2.0], [-3.0, -1.0, 2.0], [-1.0, -2.5, -4.0]])


@pytest.mark.criterion("C2")
def test_c2_point_binding_budget():
    # oracle: by hand, worst loss 8 (see PNL): budget 4 -> 4/8 = 0.5; budget 3 -> 3/8 = 0.375;
    # budget 7.5 -> 7.5/8 = 0.9375.
    # rel 1e-12: spec tolerance (exact dyadic quotients)
    assert stress_scale(M, PNL, 4.0) == pytest.approx(0.5, rel=1e-12)
    assert stress_scale(M, PNL, 3.0) == pytest.approx(0.375, rel=1e-12)
    assert stress_scale(M, PNL, 7.5) == pytest.approx(0.9375, rel=1e-12)


@pytest.mark.criterion("C2")
def test_c2_point_non_binding_budget_is_one():
    # oracle: Requirement 2: a worst loss of 8 that does not exceed the budget (equal to it, or below it)
    # leaves the book unscaled: 1. A book that gains in every scenario (worst loss -0.75) is unconstrained.
    # rel 1e-12: spec tolerance
    assert stress_scale(M, PNL, 8.0) == pytest.approx(1.0, rel=1e-12)
    assert stress_scale(M, PNL, 16.0) == pytest.approx(1.0, rel=1e-12)
    assert stress_scale(M, PNL, 1e9) == pytest.approx(1.0, rel=1e-12)
    gains = np.array([[1.0, 0.5], [0.25, 0.25], [3.0, 1.0]])
    assert stress_scale(np.array([2.0, 1.0]), gains, 0.5) == pytest.approx(1.0, rel=1e-12)


@pytest.mark.criterion("C2")
def test_c2_refusals():
    # oracle: Requirement 2: a non-positive budget raises ValueError, as do the refusals of worst_stress
    # (empty scenario matrix, mismatched shapes, a non-finite value).
    for budget in (0.0, -0.0, -1.0, -1e9):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            stress_scale(M, PNL, budget)
    cases = [
        (M, np.zeros((0, 3))),  # empty scenario matrix
        (np.array([1.0, 2.0]), PNL),  # multipliers shorter than the trades
        (np.array([1.0, 2.0, 0.5, 1.0]), PNL),  # multipliers longer than the trades
        (M, np.array([4.0, 1.0, -2.0])),  # scenario P&L not a 2-D matrix
    ]
    for bad in (np.nan, np.inf, -np.inf):
        cases.append((np.array([1.0, bad, 0.5]), PNL))
        mat = PNL.copy()
        mat[1, 2] = bad
        cases.append((M, mat))
    for mult, mat in cases:
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            stress_scale(mult, mat, 4.0)


def _book(seed: int, scenarios: int, trades: int) -> tuple[np.ndarray, np.ndarray]:
    """Dyadic multipliers and P&L (multiples of 1/4 up to 64) so every book sum is exact."""
    rng = np.random.default_rng(seed)
    m = rng.integers(0, 17, size=trades) / 4.0
    pnl = rng.integers(-256, 257, size=(scenarios, trades)) / 4.0
    return m, pnl


def _hand_worst_loss(m: np.ndarray, pnl: np.ndarray) -> float:
    """Requirement 1 by explicit loop: max over scenarios of -sum_i m_i p_si."""
    return max(-sum(float(m[i]) * float(pnl[k, i]) for i in range(len(m))) for k in range(pnl.shape[0]))


_CASE = st.tuples(
    st.integers(min_value=0, max_value=2**32 - 1),  # seed
    st.integers(min_value=1, max_value=6),  # scenarios
    st.integers(min_value=1, max_value=5),  # trades
)
# Dyadic budgets 0.25 .. 2048: below, at and above the typical worst loss.
_BUDGET = st.integers(min_value=1, max_value=8192).map(lambda q: q / 4.0)


@pytest.mark.criterion("C2")
@given(case=_CASE, budget=_BUDGET)
def test_c2_property_closed_form_and_budget_respected(case: tuple[int, int, int], budget: float):
    # oracle: Requirement 2 closed form computed in the test from the explicit-loop worst loss L: budget / L
    # when L > budget, else 1; it lies in (0, 1]; rescaling the multipliers by it leaves a worst loss of at
    # most the budget, equal to it when the scale is below 1.
    seed, k, n = case
    m, pnl = _book(seed, k, n)
    worst = _hand_worst_loss(m, pnl)
    want = budget / worst if worst > budget else 1.0
    got = stress_scale(m, pnl, budget)
    # rel 1e-12: spec tolerance
    assert got == pytest.approx(want, rel=1e-12)
    assert 0.0 < got <= 1.0
    _, after = worst_stress(got * m, pnl, [f"s{j}" for j in range(k)])
    assert after <= budget * (1.0 + 1e-12)
    if want < 1.0:
        assert after == pytest.approx(budget, rel=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    case=_CASE,
    budget=_BUDGET,
    extra=_BUDGET,
    c=st.sampled_from([0.125, 0.5, 2.0, 16.0, 1024.0]),
)
def test_c2_metamorphic_joint_scaling_and_monotone_budget(
    case: tuple[int, int, int], budget: float, extra: float, c: float
):
    # oracle: C2 properties: scaling the budget and the P&L matrix by the same c > 0 scales L by c too, so
    # the scale is unchanged; a larger budget never gives a smaller scale. Anchored to the closed form.
    seed, k, n = case
    m, pnl = _book(seed, k, n)
    worst = _hand_worst_loss(m, pnl)
    base = stress_scale(m, pnl, budget)
    # rel 1e-12: spec tolerance
    assert base == pytest.approx(budget / worst if worst > budget else 1.0, rel=1e-12)
    assert stress_scale(m, c * pnl, c * budget) == pytest.approx(base, rel=1e-12)
    larger = stress_scale(m, pnl, budget + extra)
    assert larger >= base * (1.0 - 1e-12)
