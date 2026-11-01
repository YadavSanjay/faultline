"""scenario-set-coverage C1: the principal loss directions of a P&L history.

Oracle: a history generated in the test as ``A @ V + offsets``, with ``V`` chosen orthonormal rows and ``A``
score series built from mutually orthogonal, mean-zero Hadamard columns, so the column-demeaned history's
principal components are exactly the rows of ``V`` and their sample variances (ddof 1) are the score
columns' variances, computed by hand with numpy. ``k`` follows Requirement 1's rule (smallest count reaching
``variance_share`` of the total, capped at ``max_components``) and each direction is oriented so its
component sum is >= 0. Properties: a constant added to any column changes nothing; scaling the history by
``c != 0`` scales the variances by ``c^2`` and leaves the directions; the directions are orthonormal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.coverage import principal_directions

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"


def _hadamard(n: int) -> np.ndarray:
    """Sylvester Hadamard matrix of order ``n`` (a power of two): +/-1 entries, orthogonal columns."""
    h = np.array([[1.0]])
    while h.shape[0] < n:
        h = np.block([[h, h], [h, -h]])
    return h


def _oriented(v: np.ndarray) -> np.ndarray:
    """Each row flipped so its component sum is >= 0 (Requirement 1's orientation)."""
    return v * np.where(v.sum(axis=1) < 0, -1.0, 1.0)[:, None]


# Three orthonormal rows with non-zero component sums (5/3, -1/3, 1/3): pairwise dot products are 0 and
# each squared norm is 9/9.
V3 = np.array([[2.0, 2.0, 1.0], [2.0, -1.0, -2.0], [1.0, -2.0, 2.0]]) / 3.0


def _point_history() -> tuple[np.ndarray, np.ndarray]:
    # Scores 3*h1, 2*h2, 1*h3 from Hadamard(8) columns 1..3 (+/-1, mean 0, orthogonal), each column's
    # sample variance 8/7 x scale^2: 72/7, 32/7, 8/7 (total 16).
    h = _hadamard(8)[:, 1:4]
    scores = h * np.array([3.0, 2.0, 1.0])
    offsets = np.array([10.0, -4.0, 0.5])
    return scores @ V3 + offsets, scores


@pytest.mark.criterion("C1")
def test_c1_point_recovers_constructed_components():
    # oracle: the history is built from V3 and orthogonal score columns, so the principal directions are
    # V3's rows (oriented: row 2 flips, its sum is -1/3) and the variances are the score columns' ddof-1
    # variances, 72/7, 32/7, 8/7. Shares of the total 16: 9/14, 13/14, 1, so the default 0.9 keeps k = 2.
    history, scores = _point_history()
    want_var = np.array([72.0, 32.0, 8.0]) / 7.0
    # rel 1e-12: hand value against numpy's own variance, exact up to rounding
    assert scores.var(axis=0, ddof=1) == pytest.approx(want_var, rel=1e-12)
    directions, variances = principal_directions(history)
    assert directions.shape == (2, 3)
    assert variances.shape == (2,)
    # abs 1e-9 / rel 1e-9: spec tolerance for directions and variances
    assert directions == pytest.approx(_oriented(V3)[:2], abs=1e-9)
    assert variances == pytest.approx(want_var[:2], rel=1e-9)
    assert np.all(directions.sum(axis=1) >= 0.0)
    # each variance is the sample variance of the history's projection on its direction
    proj = (history - history.mean(axis=0)) @ directions.T
    assert variances == pytest.approx(proj.var(axis=0, ddof=1), rel=1e-9)


@pytest.mark.criterion("C1")
def test_c1_point_k_follows_share_rule_and_cap():
    # oracle: cumulative variance shares 9/14 (0.643), 13/14 (0.929), 1 by hand: share 0.5 -> k = 1,
    # 0.95 -> k = 3, 1.0 -> k = 3; max_components 2 caps share 1.0 at k = 2, max_components 1 caps 0.95 at 1.
    history, _ = _point_history()
    want_var = np.array([72.0, 32.0, 8.0]) / 7.0
    oriented = _oriented(V3)
    for share, cap, k in [(0.5, 10, 1), (0.95, 10, 3), (1.0, 10, 3), (1.0, 2, 2), (0.95, 1, 1)]:
        directions, variances = principal_directions(history, variance_share=share, max_components=cap)
        assert directions.shape == (k, 3)
        # abs 1e-9 / rel 1e-9: spec tolerance
        assert directions == pytest.approx(oriented[:k], abs=1e-9)
        assert variances == pytest.approx(want_var[:k], rel=1e-9)


@pytest.mark.criterion("C1")
def test_c1_refusals():
    # oracle: Requirement 1: fewer than two rows, a non-finite value, variance_share outside (0, 1], or zero
    # total variance raises ValueError.
    history, _ = _point_history()
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        principal_directions(history[:1])
    for bad in (np.nan, np.inf, -np.inf):
        h = history.copy()
        h[3, 1] = bad
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            principal_directions(h)
    for share in (0.0, -0.25, 1.5):
        with pytest.raises(ValueError, match=ANY_MESSAGE):
            principal_directions(history, variance_share=share)
    constant = np.tile(np.array([1.0, -2.0, 0.5]), (6, 1))
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        principal_directions(constant)


_SCALES = [8.0, 5.0, 3.0, 2.0, 1.0, 0.5]  # adjacent squared ratios >= 2.25: well-separated variances


@dataclass(frozen=True)
class _Case:
    history: np.ndarray
    v: np.ndarray  # oriented construction rows
    var: np.ndarray  # hand variances of the score columns
    share: float
    cap: int
    k: int
    n: int


@st.composite
def _case(draw: st.DrawFn) -> _Case:
    n = draw(st.integers(min_value=2, max_value=6))
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.standard_normal((n, n)))
    v = q.T
    assume(bool(np.all(np.abs(v.sum(axis=1)) > 0.1)))  # orientation well-defined (spec Notes)
    scales = np.array(sorted(draw(st.lists(st.sampled_from(_SCALES), min_size=n, max_size=n, unique=True)),
                             reverse=True))
    scores = _hadamard(16)[:, 1 : n + 1] * scales
    offsets = np.array(draw(st.lists(st.integers(-64, 64), min_size=n, max_size=n)), dtype=float) / 4.0
    var = scales**2 * 16.0 / 15.0
    cum = np.cumsum(var) / var.sum()
    k_rule = draw(st.integers(min_value=1, max_value=n))
    lo = 0.0 if k_rule == 1 else float(cum[k_rule - 2])
    share = (lo + float(cum[k_rule - 1])) / 2.0 if k_rule < n else 1.0
    cap = draw(st.integers(min_value=1, max_value=n + 1))
    return _Case(scores @ v + offsets, _oriented(v), var, share, cap, min(k_rule, cap), n)


@pytest.mark.criterion("C1")
@given(case=_case())
def test_c1_property_orthonormal_and_recovered(case: _Case):
    # oracle: C1 property: the directions are orthonormal (D D^T = I) and oriented (sums >= 0); against the
    # construction, they are V's oriented rows, the variances the score columns' ddof-1 variances
    # (scale^2 x 16/15), and k the share rule (a share midway between two cumulative shares) with the cap.
    k = case.k
    directions, variances = principal_directions(case.history, variance_share=case.share, max_components=case.cap)
    assert directions.shape == (k, case.n)
    # abs 1e-9: spec tolerance for directions
    assert directions @ directions.T == pytest.approx(np.eye(k), abs=1e-9)
    assert np.all(directions.sum(axis=1) >= 0.0)
    assert directions == pytest.approx(case.v[:k], abs=1e-9)
    # rel 1e-9: spec tolerance for variances
    assert variances == pytest.approx(case.var[:k], rel=1e-9)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(case=_case(), c=st.sampled_from([-3.0, -0.5, 0.25, 2.0, 5.0]), shift=st.integers(-256, 256))
def test_c1_metamorphic_shift_and_scale(case: _Case, c: float, shift: int):
    # oracle: C1 properties: adding a constant to a column leaves the demeaned history unchanged, so the
    # result is the same; scaling by c != 0 scales every variance by c^2 and, after orientation, leaves the
    # directions. The base result is anchored to the construction (V's oriented rows, hand variances).
    history, share, cap, k = case.history, case.share, case.cap, case.k
    d0, v0 = principal_directions(history, variance_share=share, max_components=cap)
    # abs 1e-9 / rel 1e-9: spec tolerance
    assert d0 == pytest.approx(case.v[:k], abs=1e-9)
    assert v0 == pytest.approx(case.var[:k], rel=1e-9)
    shifted = history.copy()
    shifted[:, 0] += shift / 8.0
    d1, v1 = principal_directions(shifted, variance_share=share, max_components=cap)
    assert d1 == pytest.approx(d0, abs=1e-9)
    assert v1 == pytest.approx(v0, rel=1e-9)
    d2, v2 = principal_directions(history * c, variance_share=share, max_components=cap)
    assert d2 == pytest.approx(d0, abs=1e-9)
    assert v2 == pytest.approx(v0 * c**2, rel=1e-9)
