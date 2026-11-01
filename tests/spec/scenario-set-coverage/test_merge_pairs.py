"""scenario-set-coverage C6: the pairs of scenarios similar enough to merge.

Oracle: Requirement 6 by hand: every pair ``(a, b, cos)`` of distinct non-zero rows, ``a`` before ``b``, whose
cosine ``r_a.r_b / (||r_a|| ||r_b||)`` is strictly above the threshold, in decreasing order of cosine.
Properties: scaling a row by a positive number doesn't change the result; negating a row removes its pairs
(for a positive threshold).
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from src.scenariogen.coverage import merge_pairs

# The spec fixes the exception type, not its wording: any non-empty message.
ANY_MESSAGE = r"\S"

NAMES = ["a", "b", "c", "d", "e"]
# a = e1, b = (4, 3, 0), c = (12, 5, 0), d = e3, e = 0. Cosines by hand: (a, b) 4/5, (a, c) 12/13,
# (b, c) (48 + 15) / (5 x 13) = 63/65, every pair with d 0, e in no pair.
ROWS = np.array(
    [[1.0, 0.0, 0.0], [4.0, 3.0, 0.0], [12.0, 5.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]]
)


def _split(pairs: list[tuple[str, str, float]]) -> tuple[list[tuple[str, str]], list[float]]:
    return [(a, b) for a, b, _ in pairs], [cos for _, _, cos in pairs]


@pytest.mark.criterion("C6")
def test_c6_point_default_threshold_and_order():
    # oracle: by hand (cosines above): above the default 0.85 are (b, c) 63/65 = 0.969 and (a, c)
    # 12/13 = 0.923, in that order; (a, b) at 0.8 and every pair with d (0) are not.
    got = merge_pairs(NAMES, ROWS)
    keys, cos = _split(got)
    assert keys == [("b", "c"), ("a", "c")]
    # rel 1e-12: spec tolerance
    assert cos == pytest.approx([63.0 / 65.0, 12.0 / 13.0], rel=1e-12)


@pytest.mark.criterion("C6")
def test_c6_point_exact_threshold_excluded_and_zero_row_in_no_pair():
    # oracle: by hand: p = (1, 1, 1, 1), q = (2, 0, 0, 0), r = (1, 1, 1, 0.5) and z = 0. cos(p, q) =
    # 2 / (2 x 2) = 0.5 exactly (exact in binary), so at threshold 0.5 it is not returned; cos(p, r) =
    # 3.5 / (2 x 1.80277...) = 0.9707, cos(q, r) = 2 / (2 x 1.80277...) = 0.5547. At threshold -1 every pair of
    # non-zero rows is above (cos > -1 here), and the zero row z is still in no pair.
    names = ["p", "q", "z", "r"]
    rows = np.array([[1.0, 1.0, 1.0, 1.0], [2.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 0.5]])
    nr = float(np.sqrt(3.25))
    keys, cos = _split(merge_pairs(names, rows, threshold=0.5))
    assert keys == [("p", "r"), ("q", "r")]
    # rel 1e-12: spec tolerance
    assert cos == pytest.approx([3.5 / (2.0 * nr), 2.0 / (2.0 * nr)], rel=1e-12)
    keys, cos = _split(merge_pairs(names, rows, threshold=-1.0))
    assert keys == [("p", "r"), ("q", "r"), ("p", "q")]
    assert cos == pytest.approx([3.5 / (2.0 * nr), 2.0 / (2.0 * nr), 0.5], rel=1e-12)


@pytest.mark.criterion("C6")
def test_c6_mismatch_refused():
    # oracle: Requirement 6: mismatched lengths (names vs rows) raise ValueError.
    with pytest.raises(ValueError, match=ANY_MESSAGE):
        merge_pairs(NAMES[:4], ROWS)


def _oracle(names: list[str], rows: np.ndarray, threshold: float) -> dict[tuple[str, str], float]:
    norms = np.linalg.norm(rows, axis=1)
    out: dict[tuple[str, str], float] = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if norms[i] > 0 and norms[j] > 0:
                cos = float(rows[i] @ rows[j] / (norms[i] * norms[j]))
                if cos > threshold:
                    out[(names[i], names[j])] = cos
    return out


def _all_cos(rows: np.ndarray) -> list[float]:
    norms = np.linalg.norm(rows, axis=1)
    m = rows.shape[0]
    return [float(rows[i] @ rows[j] / (norms[i] * norms[j]))
            for i in range(m) for j in range(i + 1, m) if norms[i] > 0 and norms[j] > 0]


@st.composite
def _case(draw: st.DrawFn) -> tuple[list[str], np.ndarray, float]:
    seed = draw(st.integers(min_value=0, max_value=2**32 - 1))
    m = draw(st.integers(min_value=2, max_value=7))
    n = draw(st.integers(min_value=2, max_value=5))
    rng = np.random.default_rng(seed)
    base = rng.standard_normal((3, n))
    # rows are noisy copies of three bases so that close pairs (cos near 1) are common
    pick = rng.integers(0, 3, size=m)
    noise = draw(st.sampled_from([0.05, 0.3, 1.0]))
    rows = base[pick] + noise * rng.standard_normal((m, n))
    zero = draw(st.lists(st.booleans(), min_size=m, max_size=m))
    rows[np.array(zero)] = 0.0
    threshold = draw(st.sampled_from([0.5, 0.85, 0.95]))
    # a cosine within 1e-9 of the threshold would make "strictly above" a rounding question
    assume(all(abs(abs(c) - threshold) > 1e-9 for c in _all_cos(rows)))
    names = [f"s{i}" for i in range(m)]
    return names, rows, threshold


def _check(got: list[tuple[str, str, float]], want: dict[tuple[str, str], float], names: list[str]) -> None:
    keys, cos = _split(got)
    assert sorted(keys) == sorted(want)
    assert len(keys) == len(set(keys))
    assert all(names.index(a) < names.index(b) for a, b in keys)
    # rel 1e-12: spec tolerance
    assert cos == pytest.approx([want[k] for k in keys], rel=1e-12)
    assert all(x >= y - 1e-12 for x, y in pairwise(cos))


@pytest.mark.criterion("C6")
@given(case=_case(), c=st.sampled_from([0.125, 3.0, 1e4]), which=st.integers(min_value=0, max_value=6))
def test_c6_property_matches_hand_and_row_scale_free(case: tuple[list[str], np.ndarray, float], c: float, which: int):
    # oracle: C6 properties: the result is exactly the hand list (pairs strictly above the threshold, a before
    # b, decreasing cosine, zero rows excluded), and scaling a row by c > 0 leaves every cosine unchanged.
    names, rows, threshold = case
    want = _oracle(names, rows, threshold)
    assume(want)  # a non-empty pair list, so the property says something
    _check(merge_pairs(names, rows, threshold=threshold), want, names)
    scaled = rows.copy()
    scaled[which % len(names)] *= c
    _check(merge_pairs(names, scaled, threshold=threshold), want, names)


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(case=_case(), which=st.integers(min_value=0, max_value=6))
def test_c6_metamorphic_negating_a_row_removes_its_pairs(case: tuple[list[str], np.ndarray, float], which: int):
    # oracle: C6 property: negating row i flips the sign of its cosines, so for a positive threshold none of
    # its pairs stays above it; the results before and after match the hand list.
    names, rows, threshold = case
    i = which % len(names)
    before = _oracle(names, rows, threshold)
    assume(any(names[i] in k for k in before))  # row i has a pair to lose
    _check(merge_pairs(names, rows, threshold=threshold), before, names)
    flipped = rows.copy()
    flipped[i] *= -1.0
    after = merge_pairs(names, flipped, threshold=threshold)
    _check(after, _oracle(names, flipped, threshold), names)
    lost = [k for k in before if names[i] in k]
    assert not any((a, b) in lost for a, b, _ in after)
