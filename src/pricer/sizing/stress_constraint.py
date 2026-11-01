# Stress-loss sizing constraint: the scenario losses as a limit next to the VaR budget.
#
# Both functions work on the per-trade P&L of the base (unsized) book under each scenario: a scenarios x
# trades matrix whose rows are scaled by the per-trade size multipliers. P&L is linear in position size for
# a fixed scenario, so a homogeneous rescale of the multipliers rescales every scenario loss by the same
# factor. ``worst_stress`` finds the scenario with the largest book loss; ``stress_scale`` is the factor
# that brings that loss within a budget.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class StressInput:
    """A stress-loss constraint for one sizing solve.

    Attributes:
        pnl: Scenarios x trades per-trade P&L of the base book, columns in the model's trade order.
        names: One scenario name per row.
        budget: The profile's stress-loss budget in dollars (positive).
    """

    pnl: np.ndarray
    names: tuple[str, ...]
    budget: float


def _losses(multipliers: np.ndarray, scenario_pnl: np.ndarray, n_names: int | None = None) -> np.ndarray:
    """Per-scenario book loss ``-P @ m``, after checking shapes and finiteness."""
    m = np.asarray(multipliers, dtype=float)
    p = np.asarray(scenario_pnl, dtype=float)
    if m.ndim != 1 or p.ndim != 2:
        raise ValueError(f"multipliers must be 1-D and scenario_pnl 2-D, got {m.shape} and {p.shape}")
    if p.shape[0] == 0:
        raise ValueError("scenario_pnl has no scenarios")
    if p.shape[1] != m.size:
        raise ValueError(f"scenario_pnl has {p.shape[1]} trades, multipliers {m.size}")
    if n_names is not None and n_names != p.shape[0]:
        raise ValueError(f"{n_names} names for {p.shape[0]} scenarios")
    if not (np.isfinite(m).all() and np.isfinite(p).all()):
        raise ValueError("multipliers or scenario_pnl has a non-finite value")
    return np.asarray(-(p @ m), dtype=float)


def worst_stress(multipliers: np.ndarray, scenario_pnl: np.ndarray, names: Sequence[str]) -> tuple[str, float]:
    """Finds the scenario with the largest book loss.

    Args:
        multipliers: 1-D per-trade size multipliers (trades).
        scenario_pnl: Scenarios x trades matrix of per-trade P&L of the base book.
        names: One scenario name per row of ``scenario_pnl``.

    Returns:
        ``(name, loss)`` for the scenario whose book loss ``-sum_i m_i p_si`` is largest. ``loss`` is
        positive for a loss and negative when every scenario gains; a tie goes to the first scenario.

    Raises:
        ValueError: An empty scenario matrix, mismatched shapes or a non-finite value.
    """
    losses = _losses(multipliers, scenario_pnl, len(names))
    i = int(np.argmax(losses))                     # argmax returns the first maximum: ties go first
    return str(names[i]), float(losses[i])


def stress_scale(multipliers: np.ndarray, scenario_pnl: np.ndarray, budget: float) -> float:
    """Computes the factor that scales the multipliers so the worst stress loss is within the budget.

    Args:
        multipliers: 1-D per-trade size multipliers (trades).
        scenario_pnl: Scenarios x trades matrix of per-trade P&L of the base book.
        budget: Stress-loss budget in dollars, positive.

    Returns:
        ``budget / worst_loss`` when the worst stress loss exceeds ``budget``, else ``1.0``; always in
        ``(0, 1]``.

    Raises:
        ValueError: A non-positive ``budget``, or any refusal of ``worst_stress``.
    """
    if not budget > 0.0:
        raise ValueError(f"stress budget must be positive, got {budget}")
    worst = float(np.max(_losses(multipliers, scenario_pnl)))
    return budget / worst if worst > budget else 1.0
