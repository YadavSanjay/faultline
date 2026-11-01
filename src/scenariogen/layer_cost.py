# Layer cost: calls, latency and (when a service reports one) cost of each enrichment layer, set
# next to the layer's Shapley contribution from the ablation runner.
#
# ``call_layer`` maps one traced call to the layer it serves; ``response_cost`` reads the cost a
# service reported in a REST response; ``layer_usage`` totals call records per scenario and layer;
# ``layer_value`` joins those totals to ablation attributions into a value table.

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .ablation import LAYERS

# The bucket for calls that belong to no layer (the narrative LLM, health probes, other XbrlRag paths).
OVERHEAD = "overhead"


def call_layer(service: str, purpose: str) -> str:
    """Maps one traced call to the enrichment layer it serves.

    Args:
        service: The traced service, e.g. ``"macrorag"``, ``"arag"`` or ``"llm"``.
        purpose: The call's purpose, e.g. the REST path ``"/api/v1/facts/lookup"``.

    Returns:
        ``"macrorag"`` for a MacroRAG call, ``"xbrlrag_narrative"`` / ``"xbrlrag_facts"`` for an XbrlRag
        call whose purpose contains ``"/narrative/"`` / ``"/facts/"``, else ``"overhead"``.
    """
    if service == "macrorag":
        return "macrorag"
    if service == "arag":
        if "/narrative/" in purpose:
            return "xbrlrag_narrative"
        if "/facts/" in purpose:
            return "xbrlrag_facts"
    return OVERHEAD


def response_cost(response: Any) -> float | None:
    """Reads the cost a service reported in a response's ``usage.cost_usd``.

    Args:
        response: The decoded response body (any value).

    Returns:
        The reported cost in USD as a float, or ``None`` when the response reports no cost.

    Raises:
        ValueError: If ``cost_usd`` is present but negative, NaN, infinite, a ``bool`` or not a number.
    """
    if not isinstance(response, Mapping):
        return None
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        return None
    cost = usage.get("cost_usd")
    if cost is None:
        return None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        raise ValueError(f"usage.cost_usd must be a number, got {cost!r}")
    value = float(cost)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"usage.cost_usd must be finite and >= 0, got {cost!r}")
    return value


def layer_usage(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Totals call records per scenario and layer.

    Args:
        records: Call records with at least ``service``, ``purpose``, ``ok``, ``latency_ms``,
            ``scenario``, ``cost_usd`` and ``cost_reported``.

    Returns:
        ``{scenario: {layer: usage}}`` with every layer in ``LAYERS`` and ``"overhead"`` present for each
        scenario; ``usage`` has ``n_calls``, ``n_errors``, ``latency_s``, ``n_cost_reported`` and
        ``cost_usd`` (``None`` when no call reported a cost).

    Raises:
        ValueError: If a record has a negative ``latency_ms``, or a negative ``cost_usd`` while
            ``cost_reported`` is true.
    """
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in records:
        latency_ms = float(r["latency_ms"])
        if latency_ms < 0.0:
            raise ValueError(f"negative latency_ms {latency_ms} in a {r['service']} call record")
        reported = bool(r["cost_reported"])
        cost = float(r["cost_usd"]) if reported else 0.0
        if cost < 0.0:
            raise ValueError(f"negative reported cost_usd {cost} in a {r['service']} call record")
        scenario = r.get("scenario") or ""
        if scenario not in out:
            out[scenario] = {layer: _empty_usage() for layer in (*LAYERS, OVERHEAD)}
        u = out[scenario][call_layer(str(r["service"]), str(r["purpose"]))]
        u["n_calls"] += 1
        u["n_errors"] += 0 if r["ok"] else 1
        u["latency_s"] += latency_ms / 1000.0
        if reported:
            u["n_cost_reported"] += 1
            u["cost_usd"] = (u["cost_usd"] or 0.0) + cost
    return out


def layer_value(usage: Mapping[str, Mapping[str, Mapping[str, Any]]],
                attributions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Joins per-layer usage to ablation attributions into a value table.

    Args:
        usage: The output of ``layer_usage``.
        attributions: Ablation records, each with ``scenario``, ``severity`` and ``shapley``
            (layer -> P&L contribution).

    Returns:
        A dict with ``rows`` (one per attribution and layer), ``totals`` (one per severity and layer)
        and ``overhead`` (calls, latency and cost of the ``"overhead"`` calls over every scenario).

    Raises:
        ValueError: If two attributions share a ``(scenario, severity)``.
    """
    seen: set[tuple[str, str]] = set()
    for a in attributions:
        key = (str(a["scenario"]), str(a["severity"]))
        if key in seen:
            raise ValueError(f"duplicate attribution for scenario {key[0]!r} at severity {key[1]!r}")
        seen.add(key)

    rows: list[dict[str, Any]] = []
    severities: dict[str, list[str]] = {}          # severity -> distinct scenarios, first-seen order
    for a in attributions:
        scenario, severity = str(a["scenario"]), str(a["severity"])
        scen_list = severities.setdefault(severity, [])
        if scenario not in scen_list:
            scen_list.append(scenario)
        shapley = a.get("shapley") or {}
        for layer in LAYERS:
            value = float(shapley.get(layer, 0.0))
            u = _usage_of(usage, scenario, layer)
            rows.append({"scenario": scenario, "severity": severity, "layer": layer, "shapley": value,
                         "contribution": abs(value), "n_calls": u["n_calls"], "latency_s": u["latency_s"],
                         "cost_usd": u["cost_usd"], **_ratios(abs(value), u["latency_s"], u["cost_usd"])})

    totals: list[dict[str, Any]] = []
    for severity, scenarios in severities.items():
        for layer in LAYERS:
            contribution = sum(r["contribution"] for r in rows
                               if r["severity"] == severity and r["layer"] == layer)
            parts = [_usage_of(usage, s, layer) for s in scenarios]
            agg = _sum_usage(parts)
            totals.append({"severity": severity, "layer": layer, "contribution": contribution, **agg,
                           **_ratios(contribution, agg["latency_s"], agg["cost_usd"])})

    overhead = _sum_usage([_usage_of(usage, s, OVERHEAD) for s in usage])
    return {"rows": rows, "totals": totals, "overhead": overhead}


def _empty_usage() -> dict[str, Any]:
    return {"n_calls": 0, "n_errors": 0, "latency_s": 0.0, "n_cost_reported": 0, "cost_usd": None}


def _usage_of(usage: Mapping[str, Mapping[str, Mapping[str, Any]]], scenario: str, layer: str) -> Mapping[str, Any]:
    """A scenario's usage on one layer; no calls, no latency and no cost when it is absent."""
    return usage.get(scenario, {}).get(layer) or _empty_usage()


def _sum_usage(parts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calls, latency and cost summed; cost is None when no part reported one."""
    costs = [float(p["cost_usd"]) for p in parts if p["cost_usd"] is not None]
    return {"n_calls": sum(int(p["n_calls"]) for p in parts),
            "latency_s": sum(float(p["latency_s"]) for p in parts),
            "cost_usd": sum(costs) if costs else None}


def _ratios(contribution: float, latency_s: float, cost_usd: float | None) -> dict[str, float | None]:
    return {"contribution_per_s": contribution / latency_s if latency_s > 0 else None,
            "contribution_per_usd": contribution / cost_usd if cost_usd is not None and cost_usd > 0 else None}
