# Scenario stability: run-to-run spread of the dual-RAG legs and of the P&L they produce.
#
# The dual-RAG resolution runs several times at the same ``as_of``. ``spread`` summarises one
# quantity's values across repetitions; ``leg_stability`` gives the spread of each MacroRAG and
# XbrlRag leg per scenario; ``pnl_stability`` gives the P&L range per scenario tier. Numbers only:
# no threshold, flag or status is applied.

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def spread(values: Sequence[float | None]) -> dict[str, Any]:
    """Summarises one quantity's values across repetitions.

    Args:
        values: One value per repetition, in repetition order; ``None`` when the quantity is absent in
            that repetition.

    Returns:
        A dict with ``n``, ``n_present``, ``values`` (the input as floats, ``None`` kept), ``min``,
        ``max``, ``range``, ``mean``, ``std`` (sample standard deviation, ``None`` below two present
        values), ``rel_range`` (``range / max |x|``, ``None`` when every present value is zero) and
        ``sign_flip``, computed over the present values only.

    Raises:
        ValueError: If ``values`` is empty, or an element is not ``None`` and not an ``int`` or
            ``float`` (a ``bool`` is rejected), or is NaN or infinite.
    """
    if not values:
        raise ValueError("spread needs at least one value")
    floats: list[float | None] = [_number(v, f"values[{i}]") for i, v in enumerate(values)]
    xs = [x for x in floats if x is not None]
    k = len(xs)
    out: dict[str, Any] = {
        "n": len(floats), "n_present": k, "values": floats,
        "min": None, "max": None, "range": None, "mean": None, "std": None, "rel_range": None,
        "sign_flip": False,
    }
    if k == 0:
        return out
    lo, hi = min(xs), max(xs)
    mean = math.fsum(xs) / k
    std = math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / (k - 1)) if k >= 2 else None
    max_abs = max(abs(x) for x in xs)
    rng = hi - lo
    out.update({
        "min": lo, "max": hi, "range": rng, "mean": mean, "std": std,
        # max |x| is zero only when every present value is exactly 0.0 (an input, not a computed float).
        "rel_range": rng / max_abs if max_abs != 0.0 else None,
        "sign_flip": any(x > 0.0 for x in xs) and any(x < 0.0 for x in xs),
    })
    return out


def leg_stability(resolutions: Sequence[Mapping[str, Any]], *, n_repetitions: int,
                  sources: Sequence[str] = ("macrorag", "xbrlrag", "xbrlrag_seed")) -> list[dict[str, Any]]:
    """Gives the run-to-run spread of each dual-RAG leg per scenario.

    Args:
        resolutions: One record per (scenario, repetition) with ``repetition``, ``scenario``, optional
            ``stopped`` and, when not stopped, ``legs`` as ``{source: {leg: move}}``.
        n_repetitions: The number of repetitions in the run.
        sources: The sources reported for every scenario.

    Returns:
        One dict per scenario in order of first appearance, with ``scenario``, ``n_repetitions``,
        ``n_resolved``, ``n_stopped`` and, per source, ``{leg: spread entry plus n_missing}`` with legs
        in sorted order.

    Raises:
        ValueError: If ``n_repetitions < 1``, a repetition is not an ``int`` or is out of range, a
            ``(scenario, repetition)`` repeats, a resolved record names an unknown source, or a move
            is rejected by ``spread``.
    """
    _check_repetitions(n_repetitions)
    allowed = set(sources)
    resolved: dict[str, dict[int, Mapping[str, Any]]] = {}   # scenario -> repetition -> legs by source
    seen: set[tuple[str, int]] = set()
    for rec in resolutions:
        scenario = rec["scenario"]
        rep = _repetition(rec["repetition"], n_repetitions)
        if (scenario, rep) in seen:
            raise ValueError(f"duplicate record for scenario {scenario!r}, repetition {rep}")
        seen.add((scenario, rep))
        reps = resolved.setdefault(scenario, {})   # first appearance fixes the row order
        if rec.get("stopped", False):
            continue                               # a stopped record's legs are ignored entirely
        legs = rec.get("legs") or {}
        unknown = sorted(set(legs) - allowed)
        if unknown:
            raise ValueError(f"scenario {scenario!r} repetition {rep}: unknown source(s) {unknown}")
        reps[rep] = legs

    rows: list[dict[str, Any]] = []
    for scenario, reps in resolved.items():
        n_resolved = len(reps)
        row: dict[str, Any] = {"scenario": scenario, "n_repetitions": n_repetitions,
                               "n_resolved": n_resolved, "n_stopped": n_repetitions - n_resolved}
        for source in sources:
            by_rep = {rep: legs.get(source) or {} for rep, legs in reps.items()}
            names = sorted({name for moves in by_rep.values() for name in moves})
            entries: dict[str, Any] = {}
            for name in names:
                series = [by_rep[r].get(name) if r in by_rep else None for r in range(n_repetitions)]
                entry = spread(series)
                entry["n_missing"] = n_resolved - entry["n_present"]
                entries[name] = entry
            row[source] = entries
        rows.append(row)
    return rows


def pnl_stability(pnls: Sequence[Mapping[str, Any]], *, n_repetitions: int) -> list[dict[str, Any]]:
    """Gives the run-to-run P&L range per scenario tier.

    Args:
        pnls: One record per priced (scenario, tier, repetition) with ``repetition``, ``scenario``,
            ``severity`` and ``total_pnl``.
        n_repetitions: The number of repetitions in the run.

    Returns:
        One dict per ``(scenario, severity)`` in order of first appearance, with ``scenario``,
        ``severity``, ``n_repetitions``, ``n_priced`` and ``n_missing`` merged with the ``spread`` of
        the per-repetition ``total_pnl``.

    Raises:
        ValueError: If ``n_repetitions < 1``, a repetition is not an ``int`` or is out of range, a
            ``(scenario, severity, repetition)`` repeats, or a ``total_pnl`` is ``None`` or rejected by
            ``spread``.
    """
    _check_repetitions(n_repetitions)
    series: dict[tuple[str, str], list[float | None]] = {}
    for rec in pnls:
        key = (rec["scenario"], rec["severity"])
        rep = _repetition(rec["repetition"], n_repetitions)
        pnl = rec["total_pnl"]
        if pnl is None:
            raise ValueError(f"{key} repetition {rep}: total_pnl is None")
        slots = series.setdefault(key, [None] * n_repetitions)
        if slots[rep] is not None:
            raise ValueError(f"duplicate record for {key}, repetition {rep}")
        slots[rep] = _number(pnl, f"{key} repetition {rep} total_pnl")

    rows: list[dict[str, Any]] = []
    for (scenario, severity), values in series.items():
        stats = spread(values)
        row: dict[str, Any] = {"scenario": scenario, "severity": severity, "n_repetitions": n_repetitions,
                               "n_priced": stats["n_present"],
                               "n_missing": n_repetitions - stats["n_present"]}
        row.update(stats)
        rows.append(row)
    return rows


def _number(value: object, what: str) -> float | None:
    """Returns ``value`` as a float (``None`` kept); raises for a bool, a non-number, NaN or infinity."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what}: expected a number or None, got {value!r}")
    x = float(value)
    if not math.isfinite(x):
        raise ValueError(f"{what}: expected a finite number, got {value!r}")
    return x


def _check_repetitions(n_repetitions: int) -> None:
    if n_repetitions < 1:
        raise ValueError(f"n_repetitions must be at least 1, got {n_repetitions}")


def _repetition(value: object, n_repetitions: int) -> int:
    """Returns a valid repetition index; raises for a non-int (a bool included) or an out-of-range one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"repetition must be an int, got {value!r}")
    if not 0 <= value < n_repetitions:
        raise ValueError(f"repetition {value} outside [0, {n_repetitions})")
    return value
