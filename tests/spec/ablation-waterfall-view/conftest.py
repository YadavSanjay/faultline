"""Hand-built inputs and hand oracles for the ablation-waterfall-view spec tests.

Everything is synthetic (docs/specs/ablation-waterfall-view.md, Notes). P&L, Shapley and cost values
are small dyadic fractions so ladder differences and Shapley sums are exact in binary; ratios such as
4.0 / 1.5 are not, so ``assert_close`` compares float leaves with ``pytest.approx``.

The waterfall step table (Requirement 2) is written out literally here (``STEPS``); the tests never
read it from ``src``.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

LAYER_IDS = ("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state")
STEPS = (
    ("macrorag", ("macrorag",)),
    ("xbrlrag", ("xbrlrag_narrative", "xbrlrag_facts")),
    ("events", ("news",)),
    ("market_intelligence", ("crowding", "state")),
)
STEP_IDS = tuple(s for s, _ in STEPS)


# --------------------------------------------------------------------------------------------- builders
def ladder(order: list[str], totals: list[float]) -> list[dict[str, Any]]:
    """A ladder shaped like ``ablation.ladder_attribution`` output (only the keys the views read + layers).

    ``totals[0]`` is the authored total; ``totals[i]`` the total after ``order[i-1]``.
    """
    assert len(totals) == len(order) + 1
    out = [{"layer": "authored", "layers": [], "total_pnl": totals[0]}]
    for i, layer in enumerate(order):
        out.append({"layer": layer, "layers": sorted(order[: i + 1]), "total_pnl": totals[i + 1]})
    return out


def record(scenario: str, severity: str, rule: str, order: list[str], totals: list[float],
           shapley: dict[str, float], subsets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"scenario": scenario, "severity": severity, "composition_rule": rule,
                           "order": list(order), "ladder": ladder(order, totals), "shapley": dict(shapley)}
    if subsets is not None:
        out["subsets"] = subsets
    return out


def cost_row(scenario: str, severity: str, layer: str, n_calls: int, latency_s: float,
             cost_usd: float | None) -> dict[str, Any]:
    """One ``layer_cost["rows"]`` entry (extra cost-per-layer keys included; the view reads only some)."""
    return {"scenario": scenario, "severity": severity, "layer": layer, "shapley": 0.0, "contribution": 0.0,
            "n_calls": n_calls, "latency_s": latency_s, "cost_usd": cost_usd,
            "contribution_per_s": None, "contribution_per_usd": None}


def step(step_id: str, layers: list[str], before: float, after: float, shapley: float, floor_hits: list[str],
         n_calls: int | None = None, latency_s: float | None = None, cost_usd: float | None = None,
         per_s: float | None = None, per_usd: float | None = None) -> dict[str, Any]:
    return {"step": step_id, "layers": layers, "pnl_before": before, "pnl_after": after,
            "delta": after - before, "shapley": shapley, "floor_hits": floor_hits, "n_calls": n_calls,
            "latency_s": latency_s, "cost_usd": cost_usd, "contribution_per_s": per_s,
            "contribution_per_usd": per_usd}


def no_cost(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same steps with every cost field ``None`` (``cost_rows=None``)."""
    out = []
    for s in steps:
        s = dict(s)
        for k in ("n_calls", "latency_s", "cost_usd", "contribution_per_s", "contribution_per_usd"):
            s[k] = None
        out.append(s)
    return out


# ------------------------------------------------------------------------- the full six-layer record
TAIWAN_TOTALS = [-10.0, -14.0, -13.0, -15.0, -19.0, -18.0, -18.5]
TAIWAN_SHAPLEY = {"macrorag": -4.0, "xbrlrag_narrative": 1.5, "xbrlrag_facts": -2.5, "news": -4.0,
                  "crowding": 1.75, "state": -1.25}


def taiwan_subsets() -> list[dict[str, Any]]:
    return [
        {"layers": [], "floor_hits": ["NVDA", "AMD"]},
        {"layers": ["macrorag"], "floor_hits": ["TSM"]},
        {"layers": ["macrorag", "xbrlrag_narrative"], "floor_hits": ["XX"]},            # not a step prefix
        {"layers": ["xbrlrag_facts", "macrorag", "xbrlrag_narrative"], "floor_hits": ["TSM", "AMD"]},
        {"layers": ["state", "news", "macrorag", "crowding", "xbrlrag_facts", "xbrlrag_narrative"],
         "floor_hits": []},
    ]


def taiwan_record(with_subsets: bool = True) -> dict[str, Any]:
    return record("taiwan", "severe", "dualrag-v3", list(LAYER_IDS), TAIWAN_TOTALS, TAIWAN_SHAPLEY,
                  taiwan_subsets() if with_subsets else None)


def taiwan_cost_rows() -> list[dict[str, Any]]:
    return [
        cost_row("taiwan", "severe", "macrorag", 3, 1.5, None),
        cost_row("taiwan", "severe", "xbrlrag_narrative", 2, 0.5, 0.25),
        cost_row("taiwan", "severe", "xbrlrag_facts", 1, 0.25, None),
        cost_row("taiwan", "severe", "news", 0, 0.0, None),
        cost_row("taiwan", "severe", "state", 1, 0.5, 0.125),
        cost_row("ai_bust", "severe", "macrorag", 9, 9.0, 1.0),       # another scenario
        cost_row("taiwan", "extreme", "news", 4, 2.0, 0.5),           # another severity
    ]


def taiwan_waterfall(with_subsets: bool = True, with_cost: bool = True) -> dict[str, Any]:
    """Requirement 2 by hand on ``taiwan_record`` and ``taiwan_cost_rows``.

    Ladder -10 | macrorag -14 | narrative -13, facts -15 | news -19 | crowding -18, state -18.5.
    macrorag: -10 -> -14, delta -4, shapley -4.0; floor = subsets[{macrorag}] = [TSM];
      cost: 3 calls, 1.5 s, None -> per_s 4.0 / 1.5, per_usd None.
    xbrlrag: -14 -> -15 (before = entry before narrative = macrorag), delta -1, shapley 1.5 - 2.5 = -1.0;
      floor = subsets[{macrorag, narrative, facts}] sorted = [AMD, TSM]; cost: 2 + 1 calls, 0.5 + 0.25 s,
      0.25 + None = 0.25 -> per_s 1.0 / 0.75, per_usd 1.0 / 0.25 = 4.0.
    events: -15 -> -19, delta -4, shapley -4.0; no subset for the 4-layer prefix -> []; one row with 0
      calls, 0.0 s, None -> both ratios None.
    market_intelligence: -19 -> -18.5, delta 0.5, shapley 1.75 - 1.25 = 0.5; floor [] (the six-layer
      entry has none); cost: state row only, 1 call, 0.5 s, 0.125 -> per_s 1.0, per_usd 4.0.
    The ai_bust and taiwan/extreme rows are ignored. authored_floor_hits = sorted [NVDA, AMD].
    """
    def fh(hits: list[str]) -> list[str]:
        return hits if with_subsets else []

    steps = [
        step("macrorag", ["macrorag"], -10.0, -14.0, -4.0, fh(["TSM"]), 3, 1.5, None, 4.0 / 1.5, None),
        step("xbrlrag", ["xbrlrag_narrative", "xbrlrag_facts"], -14.0, -15.0, -1.0, fh(["AMD", "TSM"]),
             3, 0.75, 0.25, 1.0 / 0.75, 1.0 / 0.25),
        step("events", ["news"], -15.0, -19.0, -4.0, [], 0, 0.0, None, None, None),
        step("market_intelligence", ["crowding", "state"], -19.0, -18.5, 0.5, [], 1, 0.5, 0.125,
             0.5 / 0.5, 0.5 / 0.125),
    ]
    return {"scenario": "taiwan", "severity": "severe", "composition_rule": "dualrag-v3",
            "authored_pnl": -10.0, "final_pnl": -18.5, "authored_floor_hits": fh(["AMD", "NVDA"]),
            "steps": steps if with_cost else no_cost(steps)}


# ------------------------------------------------------------- ai_bust: a three-layer record (C3 view)
def ai_bust_record() -> dict[str, Any]:
    return record("ai_bust", "severe", "dualrag-v1", ["macrorag", "xbrlrag_narrative", "xbrlrag_facts"],
                  [-10.0, -12.0, -11.0, -13.0], {"macrorag": -2.0, "xbrlrag_narrative": 0.5, "xbrlrag_facts": -1.5})


def ai_bust_waterfall_with_taiwan_rows() -> dict[str, Any]:
    """Requirement 2 by hand on ``ai_bust_record`` with ``taiwan_cost_rows`` (only its ai_bust row matches).

    macrorag -10 -> -12, delta -2, shapley -2.0; cost 9 calls, 9.0 s, 1.0 -> per_s 2/9, per_usd 2.0.
    xbrlrag -12 -> -13, delta -1, shapley 0.5 - 1.5 = -1.0; no rows -> 0, 0.0, None, None, None.
    events and market_intelligence have no present layer -> before = after = final -13, delta 0,
    shapley 0.0 (no keys), no rows. No subsets -> floor hits [].
    """
    return {"scenario": "ai_bust", "severity": "severe", "composition_rule": "dualrag-v1",
            "authored_pnl": -10.0, "final_pnl": -13.0, "authored_floor_hits": [],
            "steps": [
                step("macrorag", ["macrorag"], -10.0, -12.0, -2.0, [], 9, 9.0, 1.0, 2.0 / 9.0, 2.0 / 1.0),
                step("xbrlrag", ["xbrlrag_narrative", "xbrlrag_facts"], -12.0, -13.0, -1.0, [], 0, 0.0, None,
                     None, None),
                step("events", [], -13.0, -13.0, 0.0, [], 0, 0.0, None, None, None),
                step("market_intelligence", [], -13.0, -13.0, 0.0, [], 0, 0.0, None, None, None),
            ]}


def stopped_list() -> list[dict[str, Any]]:
    return [{"scenario": "gulf", "reason": "macro_unavailable", "detail": "MacroRAG timeout"}]


def ablation_report() -> dict[str, Any]:
    return {"run_id": "abl-1", "valuation_date": "2026-10-06", "generated_at": "2026-10-18T09:00:00",
            "severity": "severe", "note": "measures influence, not value", "composition_rule": "dualrag-v1",
            "scenarios": [taiwan_record(), ai_bust_record()], "stopped": stopped_list(),
            "layer_cost": {"rows": taiwan_cost_rows(), "totals": [],
                           "overhead": {"n_calls": 2, "latency_s": 2.5, "cost_usd": 0.125}}}


def ablation_report_view() -> dict[str, Any]:
    """Requirement 3 by hand on ``ablation_report``: header copied; rules {v3, v1} -> sorted [v1, v3],
    mixed; scenarios = the two hand waterfalls above; stopped copied; overhead from layer_cost."""
    return {"run_id": "abl-1", "valuation_date": "2026-10-06", "generated_at": "2026-10-18T09:00:00",
            "severity": "severe", "note": "measures influence, not value",
            "composition_rules": ["dualrag-v1", "dualrag-v3"], "mixed_rules": True,
            "scenarios": [taiwan_waterfall(), ai_bust_waterfall_with_taiwan_rows()],
            "stopped": stopped_list(), "overhead": {"n_calls": 2, "latency_s": 2.5, "cost_usd": 0.125}}


# --------------------------------------------------------------------------------- signal health (C4)
def source(name: str, status: str, holds: dict[str, int]) -> dict[str, Any]:
    """A source status shaped like ``signal_health.source_status`` output."""
    return {"source": name, "latest": "2026-10-05", "staleness_days": 1, "stale": False, "covered": 2,
            "expected": 3, "coverage": 2 / 3, "holds": dict(holds), "status": status}


def finding(severity: str, check: str) -> dict[str, Any]:
    return {"check": check, "severity": severity, "message": f"{check} out of range"}


def held_out_list() -> list[dict[str, Any]]:
    return [
        {"scenario": "ai_deflation", "severity": "severe", "scenario_version": "v-ai-1", "status": "fail",
         "findings": [finding("fail", "beyond_hist"), finding("fail", "sign"), finding("flag", "corr"),
                      finding("info", "note")]},
        {"scenario": "taiwan", "severity": "extreme", "scenario_version": "v-tw-2", "status": "flag",
         "findings": [finding("flag", "corr")]},
    ]


def health_report() -> dict[str, Any]:
    return {"run_id": "run-7", "valuation_date": "2026-10-06", "generated_at": "2026-10-18T11:00:00",
            "results": [],
            "signal_health": {
                "overall": "down",
                "counts": {"ok": 1, "degraded": 1, "down": 1, "unused": 1},
                "sources": [source("macrorag", "down", {"macro_unavailable": 2, "gate_held": 1}),
                            source("options", "degraded", {"stale": 1}),
                            source("news", "ok", {}),
                            source("gpr", "unused", {})]},
            "held_out": held_out_list(),
            "stopped": stopped_list()}


def health_view() -> dict[str, Any]:
    """Requirement 4 by hand on ``health_report``.

    n_holds: macrorag 2 + 1 = 3, options 1, news 0, gpr 0. held_out: ai_deflation 4 findings, 2 fail,
    1 flag (the "info" one is neither); taiwan 1 finding, 0 fail, 1 flag. attention = down (macrorag) +
    degraded (options) = 2, + 2 held out + 1 stopped = 5.
    """
    rep = health_report()
    srcs = rep["signal_health"]["sources"]
    held = held_out_list()
    return {"available": True, "run_id": "run-7", "valuation_date": "2026-10-06",
            "generated_at": "2026-10-18T11:00:00", "overall": "down",
            "counts": {"ok": 1, "degraded": 1, "down": 1, "unused": 1},
            "sources": [{**srcs[0], "n_holds": 2 + 1}, {**srcs[1], "n_holds": 1}, {**srcs[2], "n_holds": 0},
                        {**srcs[3], "n_holds": 0}],
            "held_out": [{**held[0], "n_findings": 4, "n_fail": 2, "n_flag": 1},
                         {**held[1], "n_findings": 1, "n_fail": 0, "n_flag": 1}],
            "stopped": stopped_list(), "attention": 2 + 2 + 1}


# ------------------------------------------------------------------------------------------- helpers
def assert_close(actual: Any, expected: Any, path: str = "$") -> None:
    """Structural equality with float leaves compared by ``pytest.approx``.

    Tolerance rel=1e-12, abs=1e-12: the hand values are dyadic or one division, so only summation order
    and division rounding may differ. Bools, ints, strings and ``None`` must match exactly.
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
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12), f"{path}: {actual!r} != {expected!r}"


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        layer_ids=LAYER_IDS,
        steps=STEPS,
        step_ids=STEP_IDS,
        ladder=ladder,
        record=record,
        cost_row=cost_row,
        step=step,
        no_cost=no_cost,
        taiwan_shapley=TAIWAN_SHAPLEY,
        taiwan_record=taiwan_record,
        taiwan_cost_rows=taiwan_cost_rows,
        taiwan_waterfall=taiwan_waterfall,
        ai_bust_record=ai_bust_record,
        ai_bust_waterfall_with_taiwan_rows=ai_bust_waterfall_with_taiwan_rows,
        stopped_list=stopped_list,
        ablation_report=ablation_report,
        ablation_report_view=ablation_report_view,
        source=source,
        finding=finding,
        held_out_list=held_out_list,
        health_report=health_report,
        health_view=health_view,
        assert_close=assert_close,
    )
