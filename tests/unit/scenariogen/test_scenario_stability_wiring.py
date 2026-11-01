"""Unit tests for scenario-stability wiring (Requirement 4).

The locked spec tests cover ``stability.spread`` / ``leg_stability`` / ``pnl_stability``; these cover the
MacroRAG ``no_cache`` flag (client body, provider, dual-provider builder), the stability runner's
repetition loop (fresh provider per repetition, stopped scenarios and tiers, legs by source, trace
tagging and per-repetition latency), the report shape and file, the log lines and the CLI. Fakes stand
only at the I/O boundaries (MacroRAG transport, the services behind the provider, pricing); no network,
no data/.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.common import storage
from src.pricer.config import get_project_root
from src.scenariogen import runner
from src.scenariogen import stability_runner as sr
from src.scenariogen.config import load_macro_config
from src.scenariogen.errors import ScenarioGenerationError
from src.scenariogen.macro.client import MacroRAGClient
from src.scenariogen.macro.models import MacroFilter
from src.scenariogen.macro.provider import MacroScenarioProvider
from src.scenariogen.trace.capture import new_collector, record_rest_call

AS_OF = date(2026, 6, 18)
SCENARIO = "taiwan_geopolitical"


class CapturingTransport:
    """Records each POST body and answers with a minimal valid synthesis."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def get(self, path: str) -> dict[str, Any]:
        return {}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(payload)
        return {"summary": "s", "world_view_id": "w",
                "shock_vector": {"USDTWD": {"value": 12.0, "unit": "pct", "chunk_ids": ["c1"]}},
                "chunks": [{"chunk_id": "c1", "release_date": "2026-06-12"}]}


# --- no_cache request flag ------------------------------------------------------------------------ #

def test_client_sends_no_cache_at_the_top_level_only_when_true():
    t = CapturingTransport()
    client = MacroRAGClient(t)
    client.synthesize("q", MacroFilter(), horizon_days=10, fields=["USDTWD"])
    client.synthesize("q", MacroFilter(), horizon_days=10, fields=["USDTWD"], no_cache=True)
    default, uncached = t.bodies
    assert "no_cache" not in default
    assert uncached["no_cache"] is True
    assert "no_cache" not in uncached["synthesis"]
    # Apart from the flag the two bodies are identical, so the default body is byte-identical to before.
    assert json.dumps({k: v for k, v in uncached.items() if k != "no_cache"}, sort_keys=True) == \
        json.dumps(default, sort_keys=True)


def test_client_default_body_has_no_no_cache_key_without_synthesis_options():
    t = CapturingTransport()
    MacroRAGClient(t).synthesize("q")
    assert t.bodies[-1] == {"question": "q", "mode": "synthesize"}


def _macro_provider(transport: CapturingTransport, **kw: Any) -> MacroScenarioProvider:
    return MacroScenarioProvider(copy.deepcopy(load_macro_config()), as_of=AS_OF,
                                 client=MacroRAGClient(transport), project_root=get_project_root(), **kw)


def test_provider_passes_no_cache_on_every_synthesis_call():
    t = CapturingTransport()
    prov = _macro_provider(t, no_cache=True)
    prov.resolve(SCENARIO)
    prov.resolve(SCENARIO)
    assert len(t.bodies) == 2
    assert all(b["no_cache"] is True for b in t.bodies)


def test_provider_default_leaves_no_cache_out():
    t = CapturingTransport()
    _macro_provider(t).resolve(SCENARIO)
    assert "no_cache" not in t.bodies[-1]


def _patch_dual_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """MacroRAG enabled, XbrlRag disabled, no preflight, no news/crowding/events (no network, no data/)."""
    cfg = copy.deepcopy(load_macro_config())
    cfg.setdefault("connection", {})["enabled"] = True
    monkeypatch.setattr(runner, "load_macro_config", lambda root=None: cfg)
    monkeypatch.setattr(runner, "load_arag_config", lambda root=None: {"connection": {"enabled": False}})
    monkeypatch.setattr(runner, "preflight_services", lambda *a, **k: None)
    monkeypatch.setattr(runner, "load_news_config", lambda root=None: {})
    monkeypatch.setattr(runner, "load_crowding_config", lambda root=None: {})
    monkeypatch.setattr(runner.NewsEventProvider, "load", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(runner.CrowdingProvider, "load", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(runner, "load_events", lambda *a, **k: None)


def test_build_dual_provider_passes_macro_no_cache(monkeypatch):
    _patch_dual_builder(monkeypatch)
    scen_cfg: dict[str, Any] = {"scenarios": {}}
    on = runner._build_dual_provider(get_project_root(), AS_OF, [], scen_cfg, macro_no_cache=True)
    off = runner._build_dual_provider(get_project_root(), AS_OF, [], scen_cfg)
    assert on.macro is not None and on.macro.no_cache is True
    assert off.macro is not None and off.macro.no_cache is False


# --- repetition loop ------------------------------------------------------------------------------ #

def _macro_res(drivers: dict[str, float], origins: dict[str, str], *, used: bool = True) -> SimpleNamespace:
    return SimpleNamespace(drivers=drivers, sources={k: {"origin": v} for k, v in origins.items()},
                           used_macro=used, world_view_id="wv-1" if used else "",
                           fallback_reason="" if used else "macro_gate_failed")


class FakeProvider:
    """Stands in for the services behind ``DualRagEnrichmentProvider``: per-repetition answers + traced calls."""

    def __init__(self, rep: int) -> None:
        self.rep = rep
        self.calls: list[str] = []

    def layer_inputs(self, name: str) -> dict[str, Any]:
        self.calls.append(name)
        record_rest_call(service="macrorag", purpose="/query", ok=True, latency_ms=1500.0 + 500.0 * self.rep)
        if name == "B" and self.rep == 1:
            raise ScenarioGenerationError("B", "macro_synthesis_failed", "timeout")
        record_rest_call(service="arag", purpose="/api/v1/narrative/search", ok=True, latency_ms=250.0)
        if name == "A":
            # MacroRAG supplies ir:USD10Y (and eq:XLK in rep 0); eq:SPY is a static driver, never a macro leg.
            drivers = {"ir:USD10Y": 0.004 + 0.001 * self.rep, "eq:SPY": -0.05}
            origins = {"ir:USD10Y": "macrorag", "eq:SPY": "static"}
            if self.rep == 0:
                drivers["eq:XLK"], origins["eq:XLK"] = -0.08, "macrorag"
            return {"macro_res": _macro_res(drivers, origins), "arag_res": SimpleNamespace(used_arag=True),
                    "calibrated_legs": {"eq:NVDA": -0.20 - 0.03 * self.rep},
                    "seed_legs": {"eq:NVDA": -0.25}}
        # B: macro legs not applied (gate) and no XbrlRag resolution.
        return {"macro_res": _macro_res({"ir:USD10Y": 0.01}, {"ir:USD10Y": "macrorag"}, used=False),
                "arag_res": None, "calibrated_legs": {}, "seed_legs": {}}


def _run(n: int = 3) -> tuple[sr.StabilityRun, list[FakeProvider], Any]:
    built: list[FakeProvider] = []

    def build(rep: int) -> Any:
        record_rest_call(service="macrorag", purpose="/healthz", ok=True, latency_ms=99.0)   # preflight
        built.append(FakeProvider(rep))
        return built[-1]

    def materialize(provider: Any, name: str, tier: str) -> Any:
        if name == "A" and tier == "severe" and provider.rep == 0:
            raise ScenarioGenerationError("A", "crisis_sigma_unavailable", "no crisis window")
        return SimpleNamespace(name=name, tier=tier, rep=provider.rep)

    def price(ms: Any) -> dict[str, float]:
        base = {"mild": -1.0e6, "severe": -3.0e6}[ms.tier] * (2.0 if ms.name == "B" else 1.0)
        return {"T1": base + 1.0e5 * ms.rep, "T2": -5.0e4}

    collector = new_collector("run-1")
    run = sr.run_repetitions(["A", "B"], ["mild", "severe"], n_repetitions=n, build_provider=build,
                             materialize=materialize, price=price, collector=collector)
    return run, built, collector


def test_each_repetition_builds_a_fresh_provider():
    _, built, _ = _run()
    assert [p.rep for p in built] == [0, 1, 2]
    assert len({id(p) for p in built}) == 3
    assert all(p.calls == ["A", "B"] for p in built)


def test_leg_records_take_macro_legs_by_origin_and_both_xbrlrag_sources():
    run, _, _ = _run()
    a0 = run.leg_records[0]
    assert a0 == {"repetition": 0, "scenario": "A",
                  "legs": {"macrorag": {"ir:USD10Y": pytest.approx(0.004), "eq:XLK": -0.08},
                           "xbrlrag": {"eq:NVDA": pytest.approx(-0.20)},
                           "xbrlrag_seed": {"eq:NVDA": -0.25}}}
    b0 = run.leg_records[1]
    assert b0["legs"] == {"macrorag": {}, "xbrlrag": {}, "xbrlrag_seed": {}}   # macro not applied, no XbrlRag


def test_a_stopped_scenario_gives_a_stopped_record_and_entry_and_no_pnl():
    run, _, _ = _run()
    assert {"repetition": 1, "scenario": "B", "stopped": True} in run.leg_records
    assert {"repetition": 1, "scenario": "B", "reason": "macro_synthesis_failed", "detail": "timeout"} \
        in run.stopped
    assert not [r for r in run.pnl_records if r["scenario"] == "B" and r["repetition"] == 1]
    assert not [r for r in run.resolutions if r["scenario"] == "B" and r["repetition"] == 1]


def test_a_stopped_tier_stays_unpriced_with_a_severity_entry():
    run, _, _ = _run()
    assert {"repetition": 0, "scenario": "A", "severity": "severe", "reason": "crisis_sigma_unavailable",
            "detail": "no crisis window"} in run.stopped
    assert [r["repetition"] for r in run.pnl_records if (r["scenario"], r["severity"]) == ("A", "severe")] == [1, 2]
    a_mild = [r for r in run.pnl_records if (r["scenario"], r["severity"]) == ("A", "mild")]
    assert [r["total_pnl"] for r in a_mild] == pytest.approx([-1.05e6, -0.95e6, -0.85e6])  # sum of trade P&L


def test_resolution_summaries():
    run, _, _ = _run()
    assert run.resolutions[0] == {"repetition": 0, "scenario": "A", "world_view_id": "wv-1",
                                  "used_macro": True, "fallback_reason": "", "used_arag": True}
    assert run.resolutions[1] == {"repetition": 0, "scenario": "B", "world_view_id": None,
                                  "used_macro": False, "fallback_reason": "macro_gate_failed", "used_arag": False}


def test_calls_are_traced_per_scenario_but_preflight_is_not():
    _, _, collector = _run()
    assert all(r.purpose != "/healthz" for r in collector.records)
    assert {r.scenario for r in collector.records} == {"A", "B"}
    # Per repetition: A makes 2 calls, B 2 (1 when it stops in rep 1).
    assert len(collector.records) == 4 + 3 + 4


def test_latency_by_repetition_sums_each_services_calls():
    run, _, _ = _run()
    lat = run.latency_by_repetition
    assert [x["repetition"] for x in lat] == [0, 1, 2]
    assert lat[0]["macrorag_s"] == pytest.approx(3.0) and lat[0]["n_macrorag_calls"] == 2
    assert lat[1]["macrorag_s"] == pytest.approx(4.0)
    assert lat[0]["xbrlrag_s"] == pytest.approx(0.5) and lat[0]["n_xbrlrag_calls"] == 2
    assert lat[1]["xbrlrag_s"] == pytest.approx(0.25) and lat[1]["n_xbrlrag_calls"] == 1


def test_check_scenario_failure_stops_the_scenario_before_resolution():
    def check(name: str) -> None:
        if name == "B":
            raise ScenarioGenerationError("B", "replay_window_uncovered", "")

    providers: list[FakeProvider] = []

    def build(rep: int) -> Any:
        providers.append(FakeProvider(rep))
        return providers[-1]

    run = sr.run_repetitions(["A", "B"], ["mild"], n_repetitions=2, build_provider=build,
                             materialize=lambda p, n, t: SimpleNamespace(name=n, tier=t, rep=p.rep),
                             price=lambda ms: {"T": 1.0}, collector=new_collector("r"), check_scenario=check)
    assert all(p.calls == ["A"] for p in providers)
    assert [s["reason"] for s in run.stopped] == ["replay_window_uncovered"] * 2


def test_an_unexpected_error_is_not_swallowed():
    class Boom(FakeProvider):
        def layer_inputs(self, name: str) -> dict[str, Any]:
            raise RuntimeError("ARag run budget exceeded")

    with pytest.raises(RuntimeError, match="budget"):
        sr.run_repetitions(["A"], ["mild"], n_repetitions=2, build_provider=Boom,
                           materialize=lambda p, n, t: None, price=lambda ms: {}, collector=new_collector("r"))


# --- report ---------------------------------------------------------------------------------------- #

def _payload() -> dict[str, Any]:
    run, _, collector = _run()
    return sr.report_payload(run, collector, valuation_date="2026-10-06", run_id="run-1", n_repetitions=3,
                             severities=["mild", "severe"], provenance={"git_sha": "abc"}, snapshot_id="s1",
                             factor_snapshot_id="f1", covariance={"cov_id": "c1"})


def test_report_payload_shape():
    p = _payload()
    assert set(p) == {"valuation_date", "run_id", "generated_at", "n_repetitions", "no_cache", "severities",
                      "composition_rule", "provenance", "snapshot_id", "factor_snapshot_id", "covariance",
                      "resolutions", "legs", "pnl", "stopped", "trace", "latency_by_repetition", "note"}
    assert p["no_cache"] is True and p["n_repetitions"] == 3
    assert p["note"] == "Run-to-run spread at one as_of; no threshold is applied."
    assert p["trace"]["n_calls"] == 11
    assert [r["scenario"] for r in p["legs"]] == ["A", "B"]
    a = p["legs"][0]
    assert (a["n_resolved"], a["n_stopped"]) == (3, 0)
    assert a["macrorag"]["eq:XLK"]["n_missing"] == 2                    # absent in reps 1 and 2: missing
    assert a["macrorag"]["ir:USD10Y"]["range"] == pytest.approx(0.002)
    assert a["xbrlrag_seed"]["eq:NVDA"]["range"] == 0.0
    b = p["legs"][1]
    assert (b["n_resolved"], b["n_stopped"]) == (2, 1)
    rows = {(r["scenario"], r["severity"]): r for r in p["pnl"]}
    assert rows[("A", "severe")]["n_priced"] == 2 and rows[("A", "severe")]["values"][0] is None
    assert rows[("B", "mild")]["n_missing"] == 1
    assert rows[("A", "mild")]["range"] == pytest.approx(2.0e5)
    json.dumps(p)                                                        # serialisable as written


def test_write_report_path(tmp_path: Path):
    p = _payload()
    out = sr.write_report(p, tmp_path)
    expected = storage.join(storage.data_base(tmp_path), "pricer", "scenario", "2026-10-06",
                            "stability_run-1.json")
    assert out == expected
    assert json.loads(Path(out).read_text(encoding="utf-8"))["run_id"] == "run-1"


def test_summary_lines_name_the_widest_leg_with_ties_first_sorted():
    entry = {"range": 0.01}
    legs = [{"scenario": "A", "macrorag": {"eq:B": entry, "eq:A": entry, "ir:C": {"range": 0.001}},
             "xbrlrag": {}, "xbrlrag_seed": {"eq:N": {"range": 0.0}}}]
    pnl = [{"scenario": "A", "severity": "mild", "min": -1.2e6, "max": -9.0e5, "range": 3.0e5,
            "n_priced": 2, "n_repetitions": 3}]
    lines = sr.summary_lines(legs, pnl)
    assert len(lines) == 4
    assert "min -1,200,000 max -900,000 range 300,000 priced 2/3" in lines[0]
    assert "3 leg(s); widest eq:A" in lines[1]
    assert "xbrlrag " in lines[2] and "0 leg(s)" in lines[2]
    assert "1 leg(s); widest eq:N" in lines[3]


# --- CLI ------------------------------------------------------------------------------------------- #

def test_cli_defaults_and_repeatable_severity():
    args = sr._parse_args([])
    assert args.repetitions == 3 and args.severity is None
    args = sr._parse_args(["--repetitions", "5", "--severity", "mild", "--severity", "severe"])
    assert args.repetitions == 5 and args.severity == ["mild", "severe"]


def test_cli_rejects_fewer_than_two_repetitions():
    with pytest.raises(SystemExit):
        sr._parse_args(["--repetitions", "1"])


def test_run_stability_rejects_fewer_than_two_repetitions_before_reading_data():
    with pytest.raises(ValueError, match="at least 2"):
        sr.run_stability(n_repetitions=1)
