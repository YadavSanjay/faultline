"""scenario-set-coverage C4: removing each row's projection on a direction.

Oracle: Requirement 4 by hand: ``r - (r.u) u`` with ``u = direction / ||direction||``. Properties: every
residual is orthogonal to the direction; removing twice equals removing once; scaling the direction by
``c != 0`` changes nothing.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.coverage import remove_direction

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C4")
def test_c4_point_hand_residuals():
    # oracle: by hand, rows (1, 2, 3) and (3, 0, 0):
    #   direction (0, 0, 2) -> u = e3: residuals (1, 2, 0) and (3, 0, 0);
    #   direction (1, 1, 0) -> u = (1, 1, 0)/sqrt 2: r1.u = 3/sqrt 2, projection (1.5, 1.5, 0), residual
    #   (-0.5, 0.5, 3); r2.u = 3/sqrt 2, residual (1.5, -1.5, 0).
    rows = np.array([[1.0, 2.0, 3.0], [3.0, 0.0, 0.0]])
    got = remove_direction(rows, np.array([0.0, 0.0, 2.0]))
    assert got.shape == (2, 3)
    # abs 1e-12: spec tolerance
    assert got == pytest.approx(np.array([[1.0, 2.0, 0.0], [3.0, 0.0, 0.0]]), abs=1e-12)
    got = remove_direction(rows, np.array([1.0, 1.0, 0.0]))
    assert got == pytest.approx(np.array([[-0.5, 0.5, 3.0], [1.5, -1.5, 0.0]]), abs=1e-12)


@pytest.mark.criterion("C4")
def test_c4_zero_direction_refused():
    # oracle: Requirement 4: a zero direction raises ValueError.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        remove_direction(np.array([[1.0, 2.0, 3.0]]), np.zeros(3))


_CASE = st.tuples(
    st.integers(min_value=0, max_value=2**32 - 1),  # seed
    st.integers(min_value=1, max_value=6),  # rows
    st.integers(min_value=2, max_value=7),  # trades
)


def _draw(case: tuple[int, int, int]) -> tuple[np.ndarray, np.ndarray]:
    seed, m, n = case
    rng = np.random.default_rng(seed)
    return rng.standard_normal((m, n)) * 10.0, rng.standard_normal(n)


def _hand(rows: np.ndarray, direction: np.ndarray) -> np.ndarray:
    u = direction / np.linalg.norm(direction)
    return rows - np.outer(rows @ u, u)


@pytest.mark.criterion("C4")
@given(case=_CASE)
def test_c4_property_residual_orthogonal(case: tuple[int, int, int]):
    # oracle: C4 property: r - (r.u) u is orthogonal to u (relative to ||r||), and matches the hand formula.
    rows, direction = _draw(case)
    got = remove_direction(rows, direction)
    assert got.shape == rows.shape
    # abs 1e-9: residuals of norm-10 rows, rounding only
    assert got == pytest.approx(_hand(rows, direction), abs=1e-9)
    u = direction / np.linalg.norm(direction)
    norms = np.linalg.norm(rows, axis=1)
    # abs 1e-9 relative to the row norm: spec tolerance
    assert np.all(np.abs(got @ u) <= 1e-9 * norms)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(case=_CASE, c=st.sampled_from([-5.0, -1.0, 0.25, 2.0, 1e4]))
def test_c4_metamorphic_idempotent_and_scale_free(case: tuple[int, int, int], c: float):
    # oracle: C4 properties: the projection removal is idempotent and depends on the direction only through
    # u, which c != 0 changes at most in sign (u u^T unchanged); the base result is the hand formula.
    rows, direction = _draw(case)
    once = remove_direction(rows, direction)
    # abs 1e-9: residuals of norm-10 rows, rounding only
    assert once == pytest.approx(_hand(rows, direction), abs=1e-9)
    assert remove_direction(once, direction) == pytest.approx(once, abs=1e-9)
    assert remove_direction(rows, direction * c) == pytest.approx(once, abs=1e-9)
