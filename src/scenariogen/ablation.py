"""Layer ablation for dual-RAG named scenarios (scenario-layer-ablation).

A dual-RAG scenario is composed in layers on top of the authored ``scenarios.json`` base:
MacroRAG macro legs, XbrlRag company legs (narrative seed, then XBRL fact calibration), the news and
crowding multipliers on those legs, and the GPR x throughput state multiplier. ``compose_definition``
is the single composition rule (``DualRagEnrichmentProvider.definition_for`` delegates to it), so a
subset of layers can be rebuilt from already-resolved inputs without calling any service again.
``ladder_attribution`` and ``shapley_attribution`` then attribute P&L to the layers.

The ablation measures each layer's influence on a scenario, not its value.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from itertools import combinations
from typing import Any

import numpy as np

# Layer ids in default ladder order. The base ("authored") is the empty set and has no id.
LAYERS: tuple[str, ...] = ("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state")
BASE_LAYER = "authored"
# Bumped whenever the composition rule changes (e.g. news/crowding reaching non-XbrlRag legs), so
# ablation results from different rules are never compared by mistake.
COMPOSITION_RULE = "dualrag-v1"


def _check_scale(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")


def compose_definition(
    *,
    layers: frozenset[str],
    authored_drivers: dict[str, float],
    authored_overlays: dict[str, float],
    macro_drivers: dict[str, float] | None,
    seed_legs: dict[str, float],
    calibrated_legs: dict[str, float],
    news_mult: dict[str, float],
    crowding_mult: dict[str, float],
    tier_scale: float,
    state_mult: float,
    leg_corrections: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float], float]:
    """Builds a scenario's drivers, overlays and severity scale from resolved inputs and enabled layers.

    Args:
        layers: Enabled layer ids (the base ``authored`` layer has no id and is always on).
        authored_drivers: ``scenarios.json`` drivers, canonical factor key -> decimal move.
        authored_overlays: ``scenarios.json`` overlays (not scaled by severity).
        macro_drivers: The provider's merged authored + MacroRAG drivers, or None if unresolved.
        seed_legs: XbrlRag narrative company legs at their seed move.
        calibrated_legs: The same legs after XBRL fact calibration.
        news_mult: News multiplier per company leg (missing = 1).
        crowding_mult: Crowding multiplier per company leg (missing = 1).
        tier_scale: Severity tier scale (finite, > 0).
        state_mult: GPR x throughput severity multiplier (finite, > 0).
        leg_corrections: Company leg -> correction added to its overlay times ``severity_scale`` (not
            the multipliers), so the leg's systematic part is the anchored path's one-factor value
            instead of the propagation of the other drivers (lead-beta-systematic). None = unchanged.

    Returns:
        ``(drivers, overlays, severity_scale)``.

    Raises:
        ValueError: Unknown layer id, no drivers, mismatched leg keys, or a bad scale/multiplier.
    """
    unknown = set(layers) - set(LAYERS)
    if unknown:
        raise ValueError(f"unknown layer id(s) {sorted(unknown)}; expected a subset of {list(LAYERS)}")
    _check_scale("tier_scale", tier_scale)
    _check_scale("state_mult", state_mult)
    if set(seed_legs) != set(calibrated_legs):
        raise ValueError(
            f"seed_legs and calibrated_legs differ in keys: {sorted(set(seed_legs) ^ set(calibrated_legs))}")

    severity_scale = tier_scale * (state_mult if "state" in layers else 1.0)
    use_macro = "macrorag" in layers and macro_drivers is not None
    drivers = dict(macro_drivers or {}) if use_macro else dict(authored_drivers)
    overlays = dict(authored_overlays)

    if "xbrlrag_narrative" in layers and seed_legs:
        legs = calibrated_legs if "xbrlrag_facts" in layers else seed_legs

        def mult(factor: str) -> float:
            m = news_mult.get(factor, 1.0) if "news" in layers else 1.0
            return m * (crowding_mult.get(factor, 1.0) if "crowding" in layers else 1.0)

        systematic = {k: v for k, v in drivers.items() if k not in legs}
        if systematic:
            # Additive model: the company leg is an idiosyncratic overlay on top of the systematic
            # co-move, pre-scaled here because the generator scales drivers but not overlays.
            drivers = systematic
            corr = leg_corrections or {}
            for factor, idio in legs.items():
                overlays[factor] = (overlays.get(factor, 0.0) + idio * severity_scale * mult(factor)
                                    + corr.get(factor, 0.0) * severity_scale)
        else:
            # Idiosyncratic-only: the company leg is the driver (the generator scales it).
            drivers = {factor: idio * mult(factor) for factor, idio in legs.items()}

    if not drivers:
        raise ValueError("no drivers after composing the enabled layers")
    return drivers, overlays, severity_scale


def _validate_order(order: Sequence[str]) -> None:
    seen: set[str] = set()
    for layer in order:
        if layer not in LAYERS:
            raise ValueError(f"unknown layer id {layer!r} in order; expected ids from {list(LAYERS)}")
        if layer in seen:
            raise ValueError(f"layer id {layer!r} repeated in order")
        seen.add(layer)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks with ties sharing their average rank."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    sorted_vals = values[order]
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def _spearman(prev: Mapping[str, float], cur: Mapping[str, float]) -> float | None:
    """Spearman rank correlation over the union of trade ids (missing = 0); None if undefined."""
    ids = sorted(set(prev) | set(cur))
    a = np.array([float(prev.get(t, 0.0)) for t in ids])
    b = np.array([float(cur.get(t, 0.0)) for t in ids])
    if len(ids) < 2 or np.all(a == a[0]) or np.all(b == b[0]):
        return None
    ra, rb = _average_ranks(a), _average_ranks(b)
    ra -= ra.mean()
    rb -= rb.mean()
    return float(np.dot(ra, rb) / math.sqrt(float(np.dot(ra, ra)) * float(np.dot(rb, rb))))


def ladder_attribution(
    pnl_for: Callable[[frozenset[str]], Mapping[str, float]],
    order: Sequence[str],
) -> list[dict[str, Any]]:
    """Prices each prefix of a layer ladder and reports total, marginal, reallocation and rank change.

    Args:
        pnl_for: Prices a layer subset to per-trade P&L keyed by trade id.
        order: Layer ids in ladder order.

    Returns:
        One step per prefix (base first) with ``layer``, ``layers``, ``total_pnl``, ``marginal_pnl``,
        ``gross_reallocation`` and ``rank_corr_vs_prev``.

    Raises:
        ValueError: An unknown or repeated layer id in ``order``.
    """
    _validate_order(order)
    steps: list[dict[str, Any]] = []
    enabled: frozenset[str] = frozenset()
    prev: Mapping[str, float] | None = None
    prev_total = 0.0
    for layer in [BASE_LAYER, *order]:
        if layer != BASE_LAYER:
            enabled = enabled | {layer}
        pnl = dict(pnl_for(enabled))
        total = float(sum(pnl.values()))
        if prev is None:
            gross: float | None = None
            rank: float | None = None
            marginal = total
        else:
            ids = set(prev) | set(pnl)
            gross = float(sum(abs(pnl.get(t, 0.0) - prev.get(t, 0.0)) for t in ids))
            rank = _spearman(prev, pnl)
            marginal = total - prev_total
        steps.append({
            "layer": layer, "layers": sorted(enabled), "total_pnl": total,
            "marginal_pnl": marginal, "gross_reallocation": gross, "rank_corr_vs_prev": rank,
        })
        prev, prev_total = pnl, total
    return steps


def shapley_attribution(
    value_for: Callable[[frozenset[str]], float],
    players: Sequence[str],
) -> dict[str, float]:
    """Returns the exact Shapley value of each player, with the empty set as the reference.

    Args:
        value_for: The characteristic function ``v(S)``.
        players: Player ids.

    Returns:
        Player id -> Shapley value.

    Raises:
        ValueError: Repeated player ids.
    """
    if len(set(players)) != len(players):
        raise ValueError(f"repeated player ids in {list(players)}")
    n = len(players)
    cache: dict[frozenset[str], float] = {}

    def v(s: frozenset[str]) -> float:
        if s not in cache:
            cache[s] = float(value_for(s))
        return cache[s]

    # phi_i = sum over S subset of N\{i} of |S|!(n-|S|-1)!/n! * (v(S u {i}) - v(S))
    weights = [math.factorial(k) * math.factorial(n - k - 1) / math.factorial(n) for k in range(n)]
    result: dict[str, float] = {}
    for p in players:
        others = [q for q in players if q != p]
        phi = 0.0
        for k in range(n):
            for combo in combinations(others, k):
                s = frozenset(combo)
                phi += weights[k] * (v(s | {p}) - v(s))
        result[p] = phi
    return result
