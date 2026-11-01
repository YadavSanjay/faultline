# Scenario-set coverage: does the set span the book's loss directions, and are its scenarios distinct?
#
# All measures work on per-trade P&L over one ordered list of trades: the history matrix (days x trades,
# each trade's daily full-revaluation P&L over the VaR lookback) and the scenario matrix (scenarios x
# trades, each scenario's per-trade P&L at one tier). ``principal_directions`` finds the book's main loss
# directions; ``spanned_share`` and ``set_coverage`` measure how much of them the scenario rows span;
# ``remove_direction``, ``participation_ratio`` and ``merge_pairs`` measure how distinct the scenarios are
# once the book's dominant direction is removed.

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

# Relative singular-value cut-off for a scenario matrix's row space (spec Requirement 2).
_RANK_RTOL = 1e-12


def _finite_2d(name: str, x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=float)
    if a.ndim != 2:
        raise ValueError(f"{name} must be a 2-D matrix, got shape {a.shape}")
    if not np.isfinite(a).all():
        raise ValueError(f"{name} has a non-finite value")
    return a


def principal_directions(
    history: np.ndarray, *, variance_share: float = 0.9, max_components: int = 10
) -> tuple[np.ndarray, np.ndarray]:
    """Computes the principal loss directions of a P&L history.

    Args:
        history: Days x trades matrix of daily per-trade P&L.
        variance_share: Share of the total variance the kept directions must reach, in ``(0, 1]``.
        max_components: Most directions kept.

    Returns:
        ``(directions, variances)``: the principal components of the column-demeaned history as
        unit-length rows (``k x trades``) in decreasing order of variance, each oriented so its component
        sum is >= 0, and the sample variance (ddof 1) of the history's projection on each. ``k`` is the
        smallest count whose variances reach ``variance_share`` of the total, capped at
        ``max_components``.

    Raises:
        ValueError: Fewer than two rows, a non-finite value, ``variance_share`` outside ``(0, 1]``, or
            zero total variance.
    """
    h = _finite_2d("history", history)
    if h.shape[0] < 2:
        raise ValueError(f"history needs at least two rows, got {h.shape[0]}")
    if not 0.0 < variance_share <= 1.0:
        raise ValueError(f"variance_share must be in (0, 1], got {variance_share}")
    centred = h - h.mean(axis=0)
    _, sing, vt = np.linalg.svd(centred, full_matrices=False)
    variances = sing**2 / (h.shape[0] - 1)
    total = float(variances.sum())
    if not total > 0.0:
        raise ValueError("history has zero total variance")
    cum = np.cumsum(variances) / total
    # Smallest k whose cumulative share reaches the target; the tiny slack keeps a share of exactly 1.0
    # (or an exact cumulative share) from being missed by rounding.
    k = int(np.searchsorted(cum, variance_share - 1e-12)) + 1
    k = max(1, min(k, len(variances), int(max_components)))
    directions = vt[:k].copy()
    signs = np.where(directions.sum(axis=1) < 0.0, -1.0, 1.0)
    return directions * signs[:, None], variances[:k].copy()


def spanned_share(directions: np.ndarray, scenarios: np.ndarray) -> np.ndarray:
    """Computes the share of each direction that lies in the span of the scenario rows.

    Args:
        directions: k x trades matrix, one (unit-length) direction per row.
        scenarios: Scenarios x trades matrix of per-trade scenario P&L.

    Returns:
        For each direction ``v``, ``||P v||^2`` with ``P`` the orthogonal projection onto the
        scenario row space (singular vectors whose singular value exceeds ``1e-12`` x the largest). An
        empty or all-zero scenario matrix spans nothing (share 0).

    Raises:
        ValueError: Mismatched trade counts or a non-finite value.
    """
    d = _finite_2d("directions", directions)
    s = np.asarray(scenarios, dtype=float)
    if s.size == 0:
        s = s.reshape(0, d.shape[1])
    s = _finite_2d("scenarios", s)
    if s.shape[1] != d.shape[1]:
        raise ValueError(f"directions have {d.shape[1]} trades, scenarios {s.shape[1]}")
    if s.shape[0] == 0 or not np.any(s):
        return np.zeros(d.shape[0])
    _, sing, vt = np.linalg.svd(s, full_matrices=False)
    basis = vt[sing > _RANK_RTOL * sing[0]]
    coords = d @ basis.T
    return np.asarray((coords**2).sum(axis=1), dtype=float)


def set_coverage(variances: np.ndarray, shares: np.ndarray) -> float:
    """Computes the variance-weighted share of the book's loss directions the set spans.

    Args:
        variances: Variance of each direction.
        shares: Spanned share of each direction.

    Returns:
        ``sum(variances * shares) / sum(variances)``.

    Raises:
        ValueError: Mismatched lengths, a negative variance, or a zero variance sum.
    """
    lam = np.asarray(variances, dtype=float).ravel()
    sh = np.asarray(shares, dtype=float).ravel()
    if lam.shape != sh.shape:
        raise ValueError(f"{lam.size} variances but {sh.size} shares")
    if not (np.isfinite(lam).all() and np.isfinite(sh).all()):
        raise ValueError("non-finite variance or share")
    if (lam < 0.0).any():
        raise ValueError("negative variance")
    total = float(lam.sum())
    if not total > 0.0:
        raise ValueError("variances sum to zero")
    return float((lam * sh).sum() / total)


def remove_direction(rows: np.ndarray, direction: np.ndarray) -> np.ndarray:
    """Removes each row's projection on a direction.

    Args:
        rows: n x trades matrix.
        direction: Direction to remove, length trades (any non-zero norm).

    Returns:
        ``r - (r . u) u`` for each row ``r``, with ``u = direction / ||direction||``.

    Raises:
        ValueError: A zero direction.
    """
    r = np.asarray(rows, dtype=float)
    v = np.asarray(direction, dtype=float).ravel()
    norm = float(np.linalg.norm(v))
    if not norm > 0.0:
        raise ValueError("cannot remove a zero direction")
    if r.shape[-1] != v.size:
        raise ValueError(f"rows have {r.shape[-1]} trades, direction {v.size}")
    u = v / norm
    return np.asarray(r - np.outer(r @ u, u).reshape(r.shape), dtype=float)


def participation_ratio(rows: np.ndarray, losses: np.ndarray) -> float:
    """Computes the loss-weighted participation ratio (effective number of distinct scenarios).

    Args:
        rows: n x trades matrix, one scenario per row.
        losses: Loss of each scenario (positive = loss; a negative value is a gain and counts as none).

    Returns:
        ``(sum mu)^2 / sum mu^2`` with ``mu`` the squared singular values of the kept rows (loss > 0 and
        norm > 0), each scaled to unit length and then by ``sqrt(L_i / sum L)``; 0 when no row is kept.

    Raises:
        ValueError: Mismatched lengths or a non-finite value.
    """
    r = _finite_2d("rows", rows)
    loss = np.asarray(losses, dtype=float).ravel()
    if loss.size != r.shape[0]:
        raise ValueError(f"{r.shape[0]} rows but {loss.size} losses")
    if not np.isfinite(loss).all():
        raise ValueError("losses have a non-finite value")
    norms = np.linalg.norm(r, axis=1)
    keep = (loss > 0.0) & (norms > 0.0)
    if not keep.any():
        return 0.0
    w = loss[keep] / loss[keep].sum()
    scaled = r[keep] / norms[keep][:, None] * np.sqrt(w)[:, None]
    mu = np.linalg.svd(scaled, compute_uv=False) ** 2
    return float(mu.sum() ** 2 / (mu**2).sum())


def merge_pairs(names: Sequence[str], rows: np.ndarray, *, threshold: float = 0.85) -> list[tuple[str, str, float]]:
    """Finds the pairs of scenarios similar enough to merge.

    Args:
        names: Scenario names, one per row.
        rows: n x trades matrix, one scenario per row.
        threshold: Cosine similarity a pair must strictly exceed.

    Returns:
        Every ``(a, b, cos)`` of distinct rows (``a`` before ``b`` in ``names``) whose cosine similarity
        is strictly above ``threshold``, in decreasing order of cosine. A zero row is in no pair.

    Raises:
        ValueError: Mismatched lengths.
    """
    r = np.asarray(rows, dtype=float)
    if r.ndim != 2 or len(names) != r.shape[0]:
        raise ValueError(f"{len(names)} names but rows of shape {r.shape}")
    norms = np.linalg.norm(r, axis=1)
    pairs: list[tuple[str, str, float]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if norms[i] > 0.0 and norms[j] > 0.0:
                cos = float(r[i] @ r[j] / (norms[i] * norms[j]))
                if cos > threshold:
                    pairs.append((names[i], names[j], cos))
    return sorted(pairs, key=lambda p: -p[2])
