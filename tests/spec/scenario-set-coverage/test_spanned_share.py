"""scenario-set-coverage C2: the share of each direction that lies in the span of the scenario rows.

Oracle: Requirement 2 by hand. For a single scenario row ``s`` and a unit ``v`` the share is
``(s.v)^2 / (||s||^2 ||v||^2)``; a direction inside the span has share 1, one orthogonal to it share 0, and an
empty or all-zero scenario matrix spans nothing. For random full-rank scenario rows the share is
``||Q^T v||^2`` with ``Q`` an orthonormal basis of the rows from numpy's QR, computed in the test. Properties:
the share lies in ``[0, 1]``; scaling a row by a non-zero number or appending a linear combination of the rows
leaves it unchanged; adding a row never lowers it.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.coverage import spanned_share

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C2")
def test_c2_point_single_row_is_squared_cosine():
    # oracle: by hand, s = (1, 2, 2, 0) with ||s||^2 = 9: for unit v = e1, (s.v)^2 / 9 = 1/9; for unit
    # v = (0, 0.6, 0.8, 0), s.v = 1.2 + 1.6 = 2.8, share 7.84 / 9; for unit v = e4 (orthogonal), 0.
    s = np.array([[1.0, 2.0, 2.0, 0.0]])
    directions = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 0.6, 0.8, 0.0], [0.0, 0.0, 0.0, 1.0]])
    got = spanned_share(directions, s)
    assert got.shape == (3,)
    # rel 1e-12: spec tolerance for a single row; abs 1e-12 for the zero share
    assert got[:2] == pytest.approx([1.0 / 9.0, 7.84 / 9.0], rel=1e-12)
    assert float(got[2]) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.criterion("C2")
def test_c2_point_inside_orthogonal_and_partial():
    # oracle: by hand, the rows (2, 0, 0, 0) and (0, -3, 0, 0) span the e1-e2 plane: (0.6, 0.8, 0, 0) lies in
    # it (share 1), (0, 0, 0.6, 0.8) is orthogonal to it (share 0), and (0.5, 0.5, 0.5, 0.5) has half its
    # squared length in it (0.25 + 0.25 = 0.5).
    s = np.array([[2.0, 0.0, 0.0, 0.0], [0.0, -3.0, 0.0, 0.0]])
    directions = np.array([[0.6, 0.8, 0.0, 0.0], [0.0, 0.0, 0.6, 0.8], [0.5, 0.5, 0.5, 0.5]])
    got = spanned_share(directions, s)
    assert got.shape == (3,)
    # abs 1e-12: spec tolerance for in-span and orthogonal directions
    assert got == pytest.approx([1.0, 0.0, 0.5], abs=1e-12)


@pytest.mark.criterion("C2")
def test_c2_point_rank_cutoff_and_empty():
    # oracle: Requirement 2: a singular value at 1e-14 x the largest is below the 1e-12 cutoff, so its row
    # direction is not in the span (share 0); at 1e-10 x it is (share 1). An empty or all-zero scenario
    # matrix spans nothing: one share of 0 per direction.
    directions = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])
    tiny = spanned_share(directions, np.array([[1.0, 0.0, 0.0], [0.0, 1e-14, 0.0]]))
    small = spanned_share(directions, np.array([[1.0, 0.0, 0.0], [0.0, 1e-10, 0.0]]))
    # abs 1e-12: spec tolerance
    assert tiny == pytest.approx([0.0, 1.0], abs=1e-12)
    assert small == pytest.approx([1.0, 1.0], abs=1e-12)
    for empty in (np.zeros((0, 3)), np.zeros((2, 3))):
        got = spanned_share(directions, empty)
        assert got.shape == (2,)
        assert got == pytest.approx([0.0, 0.0], abs=1e-12)


@pytest.mark.criterion("C2")
def test_c2_refusals():
    # oracle: Requirement 2: mismatched trade counts or a non-finite value raises ValueError.
    directions = np.array([[0.6, 0.8, 0.0]])
    s = np.array([[1.0, 2.0, 3.0]])
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        spanned_share(directions, np.array([[1.0, 2.0, 3.0, 4.0]]))
    for bad in (np.nan, np.inf):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            spanned_share(directions, np.array([[1.0, bad, 3.0]]))
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            spanned_share(np.array([[0.6, bad, 0.0]]), s)


def _unit_rows(rng: np.random.Generator, k: int, n: int) -> np.ndarray:
    d = rng.standard_normal((k, n))
    return d / np.linalg.norm(d, axis=1, keepdims=True)


def _qr_share(directions: np.ndarray, scenarios: np.ndarray) -> np.ndarray:
    """||Q^T v||^2 with Q an orthonormal basis of the (full-rank) scenario rows."""
    q, _ = np.linalg.qr(scenarios.T)
    return np.sum((directions @ q) ** 2, axis=1)


_CASE = st.tuples(
    st.integers(min_value=0, max_value=2**32 - 1),  # seed
    st.integers(min_value=3, max_value=7),  # trades
    st.integers(min_value=1, max_value=3),  # directions
    st.integers(min_value=1, max_value=2),  # gap: scenario rows = trades - gap
)


@pytest.mark.criterion("C2")
@given(case=_CASE)
def test_c2_property_in_unit_interval_and_matches_qr(case: tuple[int, int, int, int]):
    # oracle: C2 property: for unit directions the share is in [0, 1]; for random (almost surely full-rank)
    # Gaussian scenario rows it equals ||Q^T v||^2 with Q from numpy's QR of the rows; a direction that is
    # itself appended as a row then has share 1.
    seed, n, k, gap = case
    rng = np.random.default_rng(seed)
    m = n - gap
    s = rng.standard_normal((m, n))
    d = _unit_rows(rng, k, n)
    got = spanned_share(d, s)
    assert got.shape == (k,)
    # abs 1e-9: projection of a unit vector, rounding of a QR/SVD basis
    assert np.all(got >= -1e-12)
    assert np.all(got <= 1.0 + 1e-12)
    assert got == pytest.approx(_qr_share(d, s), abs=1e-9)
    with_first = spanned_share(d, np.vstack([s, d[:1]]))
    assert float(with_first[0]) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(
    case=_CASE,
    c=st.sampled_from([-4.0, -0.5, 0.125, 3.0, 1e3]),
    coeffs=st.lists(st.integers(-4, 4), min_size=6, max_size=6),
)
def test_c2_metamorphic_scale_combination_and_added_row(case: tuple[int, int, int, int], c: float, coeffs: list[int]):
    # oracle: C2 properties: the span is unchanged when a row is scaled by c != 0 or a linear combination of
    # the rows is appended, so the shares are too; adding any row can only enlarge the span, so no share
    # falls. The base shares are anchored to the QR oracle.
    seed, n, k, gap = case
    rng = np.random.default_rng(seed)
    m = n - gap
    s = rng.standard_normal((m, n))
    d = _unit_rows(rng, k, n)
    base = spanned_share(d, s)
    # abs 1e-9: projection of a unit vector, rounding of a QR/SVD basis
    assert base == pytest.approx(_qr_share(d, s), abs=1e-9)
    scaled = s.copy()
    scaled[0] *= c
    assert spanned_share(d, scaled) == pytest.approx(base, abs=1e-9)
    combo = np.array(coeffs[:m], dtype=float) @ s
    assert spanned_share(d, np.vstack([s, combo])) == pytest.approx(base, abs=1e-9)
    added = spanned_share(d, np.vstack([s, rng.standard_normal(n)]))
    assert np.all(added >= base - 1e-9)
