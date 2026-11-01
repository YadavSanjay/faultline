"""scenario-set-coverage C5: the loss-weighted participation ratio of the scenario rows.

Oracle: Requirement 5 by hand. With kept rows scaled to unit length times ``sqrt(w_i)`` (``w_i = L_i / sum L``),
the squared singular values ``mu`` are the eigenvalues of the Gram matrix ``G_ij = sqrt(w_i w_j) cos_ij``, so
``sum mu = trace G = 1`` and ``sum mu^2 = ||G||_F^2 = sum_ij w_i w_j cos_ij^2``: the ratio is
``1 / sum_ij w_i w_j cos_ij^2``. Hence ``k`` for ``k`` orthogonal rows with equal losses, 1 for positive
multiples of one row, and ``1 / sum w_i^2`` for orthogonal rows. Properties: the ratio lies in
``[1, kept rows]``; it is unchanged by scaling a row by a positive number, scaling every loss by ``c > 0``,
or rotating the columns by an orthogonal matrix.
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.coverage import participation_ratio

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


@pytest.mark.criterion("C5")
def test_c5_point_orthogonal_and_parallel_rows():
    # oracle: by hand: three orthogonal rows of different lengths with equal losses -> 3; three positive
    # multiples of (1, 2, 2) with losses (1, 3, 2) -> 1; orthogonal rows with losses (1, 2, 1), shares
    # w = (1/4, 1/2, 1/4), sum w^2 = 3/8 -> 8/3.
    orth = np.array([[2.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 0.5]])
    # rel 1e-12: spec tolerance
    assert participation_ratio(orth, np.array([1.0, 1.0, 1.0])) == pytest.approx(3.0, rel=1e-12)
    parallel = np.array([[1.0, 2.0, 2.0], [2.0, 4.0, 4.0], [0.5, 1.0, 1.0]])
    assert participation_ratio(parallel, np.array([1.0, 3.0, 2.0])) == pytest.approx(1.0, rel=1e-12)
    assert participation_ratio(orth, np.array([1.0, 2.0, 1.0])) == pytest.approx(8.0 / 3.0, rel=1e-12)


@pytest.mark.criterion("C5")
def test_c5_point_gains_and_zero_rows_left_out():
    # oracle: Requirement 5 by hand: rows e1, e2, e3, 0 with losses (1, 1, -5, 7): the gain (row 3) and the
    # zero row (row 4) are left out, leaving two orthogonal rows with equal losses -> 2. With losses
    # (0, -1, -2, 3) no row is kept (the only loss is on the zero row) -> 0.
    rows = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]])
    # rel 1e-12: spec tolerance; abs 1e-12 for the empty case
    assert participation_ratio(rows, np.array([1.0, 1.0, -5.0, 7.0])) == pytest.approx(2.0, rel=1e-12)
    assert participation_ratio(rows, np.array([0.0, -1.0, -2.0, 3.0])) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.criterion("C5")
def test_c5_refusals():
    # oracle: Requirement 5: mismatched lengths or a non-finite value raises ValueError.
    rows = np.array([[1.0, 0.0], [0.0, 1.0]])
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        participation_ratio(rows, np.array([1.0, 1.0, 1.0]))
    for bad in (np.nan, np.inf):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            participation_ratio(np.array([[1.0, bad], [0.0, 1.0]]), np.array([1.0, 1.0]))
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            participation_ratio(rows, np.array([1.0, bad]))


def _gram_oracle(rows: np.ndarray, losses: np.ndarray) -> tuple[float, int]:
    """1 / sum_ij w_i w_j cos_ij^2 over the kept rows, and the kept count (0.0 when none is kept)."""
    norms = np.linalg.norm(rows, axis=1)
    keep = (losses > 0) & (norms > 0)
    if not keep.any():
        return 0.0, 0
    u = rows[keep] / norms[keep][:, None]
    w = losses[keep] / losses[keep].sum()
    cos2 = (u @ u.T) ** 2
    return float(1.0 / (w @ cos2 @ w)), int(keep.sum())


_LOSS = st.integers(min_value=-16, max_value=16).map(lambda i: i / 4)


@st.composite
def _case(draw: st.DrawFn) -> tuple[np.ndarray, np.ndarray]:
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    m = draw(st.integers(min_value=1, max_value=6))
    n = draw(st.integers(min_value=2, max_value=6))
    rng = np.random.default_rng(seed)
    rows = rng.standard_normal((m, n))
    zero = draw(st.lists(st.booleans(), min_size=m, max_size=m))
    rows[np.array(zero)] = 0.0
    losses = np.array(draw(st.lists(_LOSS, min_size=m, max_size=m)))
    return rows, losses


@pytest.mark.criterion("C5")
@given(case=_case())
def test_c5_property_bounded_by_kept_rows(case: tuple[np.ndarray, np.ndarray]):
    # oracle: C5 property: with at least one kept row the ratio lies in [1, kept rows]; its value is the Gram
    # closed form 1 / sum_ij w_i w_j cos_ij^2 computed in the test (0 when nothing is kept).
    rows, losses = case
    want, kept = _gram_oracle(rows, losses)
    got = participation_ratio(rows, losses)
    # rel 1e-9 + abs 1e-12: SVD vs Gram-matrix rounding; abs covers the empty case
    assert got == pytest.approx(want, rel=1e-9, abs=1e-12)
    if kept:
        assert 1.0 - 1e-9 <= got <= kept + 1e-9


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(
    case=_case(),
    row_scale=st.sampled_from([0.125, 2.0, 1e3]),
    c=st.sampled_from([0.25, 3.0, 1e4]),
    rot_seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_c5_metamorphic_row_scale_loss_scale_rotation(
    case: tuple[np.ndarray, np.ndarray], row_scale: float, c: float, rot_seed: int
):
    # oracle: C5 properties: cosines and loss shares are unchanged by scaling a row by a positive number,
    # scaling every loss by c > 0, or rotating the columns by an orthogonal Q (from numpy's QR); the base
    # value is the Gram closed form.
    rows, losses = case
    want, _ = _gram_oracle(rows, losses)
    base = participation_ratio(rows, losses)
    # rel 1e-9 + abs 1e-12: SVD vs Gram-matrix rounding; abs covers the empty case
    assert base == pytest.approx(want, rel=1e-9, abs=1e-12)
    scaled = rows.copy()
    scaled[0] *= row_scale
    assert participation_ratio(scaled, losses) == pytest.approx(base, rel=1e-9, abs=1e-12)
    assert participation_ratio(rows, losses * c) == pytest.approx(base, rel=1e-9, abs=1e-12)
    q, _ = np.linalg.qr(np.random.default_rng(rot_seed).standard_normal((rows.shape[1], rows.shape[1])))
    assert participation_ratio(rows @ q, losses) == pytest.approx(base, rel=1e-9, abs=1e-12)
