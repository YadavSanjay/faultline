"""Hand-built inputs and hand oracles for the cost-per-layer spec tests.

Everything is synthetic (docs/specs/cost-per-layer.md, Notes). ``mixed_records`` is a call-record list
for two scenarios (MacroRAG calls with an error, XbrlRag narrative and facts calls, reported and
unreported costs, an LLM and a health-probe call that are overhead); ``mixed_usage`` is Requirement 3
applied to it by hand. ``attributions`` is a hand-built ablation list (two scenarios at "severe", the
first again at "extreme" plus a scenario absent from ``usage``, missing ``shapley`` keys), and
``mixed_value`` is Requirement 4 applied by hand to ``mixed_usage`` and ``attributions``.

Costs are dyadic fractions (0.25, 0.125, 0.0625, 0.1875) so sums are exact in binary; latencies in
seconds are not (1200 ms / 1000), so ``assert_close`` compares float leaves with ``pytest.approx``.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

LAYER_IDS = ("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state")
ALL_KEYS = (*LAYER_IDS, "overhead")


def rec(scenario: str, service: str, purpose: str, *, ok: bool = True, latency_ms: float = 100.0,
        cost_usd: float = 0.0, cost_reported: bool = False) -> dict[str, Any]:
    """One call record shaped like ``CallRecord.to_dict`` (only the fields the spec names)."""
    return {"scenario": scenario, "service": service, "purpose": purpose, "ok": ok,
            "latency_ms": latency_ms, "cost_usd": cost_usd, "cost_reported": cost_reported}


def usage_entry(n_calls: int = 0, n_errors: int = 0, latency_s: float = 0.0, n_cost_reported: int = 0,
                cost_usd: float | None = None) -> dict[str, Any]:
    return {"n_calls": n_calls, "n_errors": n_errors, "latency_s": latency_s,
            "n_cost_reported": n_cost_reported, "cost_usd": cost_usd}


def empty_scenario_usage() -> dict[str, dict[str, Any]]:
    return {k: usage_entry() for k in ALL_KEYS}


def mixed_records() -> list[dict[str, Any]]:
    """Two scenarios, interleaved on purpose."""
    return [
        rec("taiwan", "macrorag", "/query", ok=True, latency_ms=1200.0),
        rec("ai_bust", "arag", "/api/v1/facts/lookup", latency_ms=250.0, cost_usd=0.0625, cost_reported=True),
        rec("taiwan", "macrorag", "/query", ok=False, latency_ms=300.0),
        rec("taiwan", "arag", "/api/v1/narrative/search", latency_ms=800.0, cost_usd=0.25, cost_reported=True),
        rec("ai_bust", "arag", "/api/v1/facts/lookup", ok=False, latency_ms=750.0, cost_usd=0.1875,
            cost_reported=True),
        rec("taiwan", "arag", "/api/v1/facts/lookup", latency_ms=500.0),
        rec("ai_bust", "arag", "/health", latency_ms=50.0),
        rec("taiwan", "llm", "narrative", latency_ms=2000.0, cost_usd=0.125, cost_reported=True),
        rec("ai_bust", "macrorag", "/query", latency_ms=400.0),
    ]


def mixed_usage() -> dict[str, dict[str, dict[str, Any]]]:
    """Requirement 3 by hand on ``mixed_records``.

    taiwan: macrorag 2 calls (1 error), 1200+300 = 1500 ms = 1.5 s, no reported cost -> None;
      narrative 1 call 0.8 s, reported 0.25; facts 1 call 0.5 s, unreported -> None;
      overhead (llm) 1 call 2.0 s, reported 0.125; news/crowding/state empty.
    ai_bust: macrorag 1 call 0.4 s, None; facts 2 calls (1 error), 250+750 = 1000 ms = 1.0 s, both
      reported 0.0625+0.1875 = 0.25; overhead (/health) 1 call 0.05 s, None; the rest empty.
    """
    taiwan = empty_scenario_usage()
    taiwan["macrorag"] = usage_entry(2, 1, 1.5, 0, None)
    taiwan["xbrlrag_narrative"] = usage_entry(1, 0, 0.8, 1, 0.25)
    taiwan["xbrlrag_facts"] = usage_entry(1, 0, 0.5, 0, None)
    taiwan["overhead"] = usage_entry(1, 0, 2.0, 1, 0.125)
    ai_bust = empty_scenario_usage()
    ai_bust["macrorag"] = usage_entry(1, 0, 0.4, 0, None)
    ai_bust["xbrlrag_facts"] = usage_entry(2, 1, 1.0, 2, 0.25)
    ai_bust["overhead"] = usage_entry(1, 0, 0.05, 0, None)
    return {"taiwan": taiwan, "ai_bust": ai_bust}


def attributions() -> list[dict[str, Any]]:
    return [
        {"scenario": "taiwan", "severity": "severe",
         "shapley": {"macrorag": -3.0, "xbrlrag_narrative": 2.0, "xbrlrag_facts": 0.5, "news": -1.0,
                     "crowding": 0.0}},                                        # state missing
        {"scenario": "ai_bust", "severity": "severe",
         "shapley": {"macrorag": 1.5, "xbrlrag_facts": -4.0, "state": 2.5}},  # narrative/news/crowding missing
        {"scenario": "taiwan", "severity": "extreme",
         "shapley": {"macrorag": -6.0, "xbrlrag_narrative": 4.0, "xbrlrag_facts": 1.0, "news": -2.0,
                     "crowding": 0.5, "state": 3.0}},
        {"scenario": "gulf", "severity": "extreme",                           # not in usage
         "shapley": {"macrorag": 2.0, "news": 1.0}},
    ]


def row(scenario: str, severity: str, layer: str, shapley: float, n_calls: int, latency_s: float,
        cost_usd: float | None, per_s: float | None, per_usd: float | None) -> dict[str, Any]:
    return {"scenario": scenario, "severity": severity, "layer": layer, "shapley": shapley,
            "contribution": abs(shapley), "n_calls": n_calls, "latency_s": latency_s, "cost_usd": cost_usd,
            "contribution_per_s": per_s, "contribution_per_usd": per_usd}


def total(severity: str, layer: str, contribution: float, n_calls: int, latency_s: float,
          cost_usd: float | None, per_s: float | None, per_usd: float | None) -> dict[str, Any]:
    return {"severity": severity, "layer": layer, "contribution": contribution, "n_calls": n_calls,
            "latency_s": latency_s, "cost_usd": cost_usd, "contribution_per_s": per_s,
            "contribution_per_usd": per_usd}


def mixed_value() -> dict[str, Any]:
    """Requirement 4 by hand on ``mixed_usage`` and ``attributions``.

    Rows: contribution = |shapley|; per_s = contribution / latency_s when latency_s > 0; per_usd =
    contribution / cost_usd when cost is a positive number. E.g. taiwan/severe narrative: 2.0 / 0.8 =
    2.5 per s, 2.0 / 0.25 = 8.0 per $; ai_bust/severe facts: 4.0 / 1.0 = 4.0, 4.0 / 0.25 = 16.0.
    gulf is absent from usage -> 0 calls, 0.0 s, None cost, both ratios None.

    Totals: severe = {taiwan, ai_bust}; extreme = {taiwan, gulf}. E.g. severe macrorag: contribution
    3.0 + 1.5 = 4.5, calls 2 + 1 = 3, latency 1.5 + 0.4 = 1.9 s, cost None (None for both), per_s
    4.5 / 1.9; severe narrative: cost 0.25 (taiwan) + nothing (ai_bust None) = 0.25; severe facts:
    contribution 0.5 + 4.0 = 4.5, calls 1 + 2 = 3, latency 0.5 + 1.0 = 1.5 s, cost 0.25 (ai_bust only),
    per_s 3.0, per_usd 18.0; extreme macrorag: contribution 6.0 + 2.0 = 8.0, calls 2 + 0, 1.5 s,
    per_s 8.0 / 1.5.

    Overhead over taiwan + ai_bust: calls 1 + 1 = 2, latency 2.0 + 0.05 = 2.05 s, cost 0.125 (ai_bust
    None).
    """
    rows = [
        row("taiwan", "severe", "macrorag", -3.0, 2, 1.5, None, 3.0 / 1.5, None),
        row("taiwan", "severe", "xbrlrag_narrative", 2.0, 1, 0.8, 0.25, 2.0 / 0.8, 2.0 / 0.25),
        row("taiwan", "severe", "xbrlrag_facts", 0.5, 1, 0.5, None, 0.5 / 0.5, None),
        row("taiwan", "severe", "news", -1.0, 0, 0.0, None, None, None),
        row("taiwan", "severe", "crowding", 0.0, 0, 0.0, None, None, None),
        row("taiwan", "severe", "state", 0.0, 0, 0.0, None, None, None),
        row("ai_bust", "severe", "macrorag", 1.5, 1, 0.4, None, 1.5 / 0.4, None),
        row("ai_bust", "severe", "xbrlrag_narrative", 0.0, 0, 0.0, None, None, None),
        row("ai_bust", "severe", "xbrlrag_facts", -4.0, 2, 1.0, 0.25, 4.0 / 1.0, 4.0 / 0.25),
        row("ai_bust", "severe", "news", 0.0, 0, 0.0, None, None, None),
        row("ai_bust", "severe", "crowding", 0.0, 0, 0.0, None, None, None),
        row("ai_bust", "severe", "state", 2.5, 0, 0.0, None, None, None),
        row("taiwan", "extreme", "macrorag", -6.0, 2, 1.5, None, 6.0 / 1.5, None),
        row("taiwan", "extreme", "xbrlrag_narrative", 4.0, 1, 0.8, 0.25, 4.0 / 0.8, 4.0 / 0.25),
        row("taiwan", "extreme", "xbrlrag_facts", 1.0, 1, 0.5, None, 1.0 / 0.5, None),
        row("taiwan", "extreme", "news", -2.0, 0, 0.0, None, None, None),
        row("taiwan", "extreme", "crowding", 0.5, 0, 0.0, None, None, None),
        row("taiwan", "extreme", "state", 3.0, 0, 0.0, None, None, None),
        row("gulf", "extreme", "macrorag", 2.0, 0, 0.0, None, None, None),
        row("gulf", "extreme", "xbrlrag_narrative", 0.0, 0, 0.0, None, None, None),
        row("gulf", "extreme", "xbrlrag_facts", 0.0, 0, 0.0, None, None, None),
        row("gulf", "extreme", "news", 1.0, 0, 0.0, None, None, None),
        row("gulf", "extreme", "crowding", 0.0, 0, 0.0, None, None, None),
        row("gulf", "extreme", "state", 0.0, 0, 0.0, None, None, None),
    ]
    totals = [
        total("severe", "macrorag", 3.0 + 1.5, 2 + 1, 1.5 + 0.4, None, 4.5 / 1.9, None),
        total("severe", "xbrlrag_narrative", 2.0 + 0.0, 1 + 0, 0.8 + 0.0, 0.25, 2.0 / 0.8, 2.0 / 0.25),
        total("severe", "xbrlrag_facts", 0.5 + 4.0, 1 + 2, 0.5 + 1.0, 0.25, 4.5 / 1.5, 4.5 / 0.25),
        total("severe", "news", 1.0 + 0.0, 0, 0.0, None, None, None),
        total("severe", "crowding", 0.0, 0, 0.0, None, None, None),
        total("severe", "state", 0.0 + 2.5, 0, 0.0, None, None, None),
        total("extreme", "macrorag", 6.0 + 2.0, 2 + 0, 1.5, None, 8.0 / 1.5, None),
        total("extreme", "xbrlrag_narrative", 4.0 + 0.0, 1, 0.8, 0.25, 4.0 / 0.8, 4.0 / 0.25),
        total("extreme", "xbrlrag_facts", 1.0 + 0.0, 1, 0.5, None, 1.0 / 0.5, None),
        total("extreme", "news", 2.0 + 1.0, 0, 0.0, None, None, None),
        total("extreme", "crowding", 0.5 + 0.0, 0, 0.0, None, None, None),
        total("extreme", "state", 3.0 + 0.0, 0, 0.0, None, None, None),
    ]
    overhead = {"n_calls": 1 + 1, "latency_s": 2.0 + 0.05, "cost_usd": 0.125}
    return {"rows": rows, "totals": totals, "overhead": overhead}


def assert_close(actual: Any, expected: Any, path: str = "$") -> None:
    """Structural equality with float leaves compared by ``pytest.approx``.

    Tolerance rel=1e-9, abs=1e-12: only float summation order (ms -> s division, cost sums) may
    differ from the hand values. Bools and ints must match exactly; ``None`` must stay ``None``.
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expected dict, got {actual!r}"
        assert set(actual) == set(expected), f"{path}: keys {sorted(actual)} != {sorted(expected)}"
        for k in expected:
            assert_close(actual[k], expected[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expected list, got {actual!r}"
        assert len(actual) == len(expected), f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            assert_close(a, e, f"{path}[{i}]")
    elif expected is None or isinstance(expected, (bool, str)):
        assert actual == expected and type(actual) is type(expected), f"{path}: {actual!r} != {expected!r}"
    elif isinstance(expected, int):
        assert actual == expected and not isinstance(actual, bool), f"{path}: {actual!r} != {expected!r}"
    else:
        assert actual is not None and not isinstance(actual, bool), f"{path}: {actual!r} != {expected!r}"
        assert actual == pytest.approx(expected, rel=1e-9, abs=1e-12), f"{path}: {actual!r} != {expected!r}"


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        layer_ids=LAYER_IDS,
        all_keys=ALL_KEYS,
        rec=rec,
        usage_entry=usage_entry,
        empty_scenario_usage=empty_scenario_usage,
        mixed_records=mixed_records,
        mixed_usage=mixed_usage,
        attributions=attributions,
        mixed_value=mixed_value,
        row=row,
        total=total,
        assert_close=assert_close,
    )
