"""Offline fakes and builders for the dualrag-macro-gate spec tests.

Fakes sit only at the HTTP transports the spec allows (docs/specs/dualrag-macro-gate.md, Notes): the
``MacroRAGClient`` transport and the ARag client transport. Everything else is real:
``MacroScenarioProvider``, ``AragCompanyProvider``, ``MacroGate``, ``DualRagEnrichmentProvider``, the
committed ``configs/scenariogen/{macro,scenarios}.json`` and synthetic ``arag.json``-shaped dicts.

The ARag side is configured with ``companies: []`` for every scenario, so ``AragCompanyProvider.resolve``
never needs its transport; the fake ARag transport refuses any call so an unexpected one is visible.

Dates: the valuation date is the defect run's 2026-10-06 (spec C3), passed explicitly. A "stale"
chunk is dated 2025-02-26 (587 days old by hand: 365 to 2026-02-26, then 28+31+30+31+30+31+31 to
2026-09-26 and 10 more to 2026-10-06 = 587 > 550); a "fresh" chunk is dated 2026-09-01 (35 days old).
An "episode" chunk has ``doc_type`` ``"market_episode"`` (MacroRAG ``src/ingest/usstats/episodes.py``).
"""

from __future__ import annotations

import copy
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from src.pricer.config import get_project_root
from src.scenariogen.arag.client import AragClient
from src.scenariogen.arag.provider import AragCompanyProvider
from src.scenariogen.config import load_macro_config, load_scenario_config
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.macro.client import MacroRAGClient, MacroTransportError
from src.scenariogen.macro.gate import MacroGate, MacroGateResult
from src.scenariogen.macro.mapping import to_canonical_drivers
from src.scenariogen.macro.models import MacroSynthesis
from src.scenariogen.macro.provider import MacroScenarioProvider
from src.scenariogen.scenario_gen import to_canonical

AS_OF = date(2026, 10, 6)
FRESHNESS_DAYS = 550                       # committed macro.json gate.freshness_days (spec Notes)
STALE_DATE = "2025-02-26"                  # 587 days before AS_OF (hand count in the module docstring)
STALE_AGE_DAYS = 587
FRESH_DATE = "2026-09-01"                  # 35 days before AS_OF
EPISODE = "market_episode"                 # MacroRAG doc_type of measured episode cards (spec Requirement 4)
HARD_CODES = ("implausible_magnitude", "unit_scale_mismatch", "horizon_mismatch")  # spec Requirement 5
SOFT_CODES = ("horizon_unknown", "exceeds_observed_range")                          # kept (Requirement 5)
TIERS = {"mild": 1.0, "adverse": 1.5, "severe": 2.0}   # committed scenarios.json severity_tiers

# Every IR control node a parallel "UST" field maps to (spec Notes; src/scenariogen/factor_keys.py).
IR_NODES = ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3", "DGS5", "DGS7", "DGS10", "DGS20", "DGS30")


class FakeMacroTransport:
    """MacroRAG HTTP boundary: answers POST /query with a canned body, or raises a canned error."""

    def __init__(self, resp: dict[str, Any] | None = None, exc: BaseException | None = None):
        self.resp = resp
        self.exc = exc
        self.posts = 0
        self.payloads: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str) -> dict[str, Any]:
        return {"status": "ok"}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.posts += 1
        self.payloads.append((path, copy.deepcopy(payload)))
        if self.exc is not None:
            raise self.exc
        return copy.deepcopy(self.resp or {})


class InertAragTransport:
    """ARag HTTP boundary: health is fine; any data call is unexpected (every scenario has no companies)."""

    def get(self, path: str) -> dict[str, Any]:
        return {"status": "ok"}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"unexpected ARag call {path}")


def field(value: float, unit: str, chunk_ids: list[str], mode: str | None, *,
          validation: list[str] | None = None, horizon: Any = None) -> dict[str, Any]:
    """One shock-vector field in the MacroRAG synthesize shape. ``mode``/``validation``/``horizon`` None
    omit ``grounding_mode``/``validation``/``source_horizon_days`` (as MacroRAG does when empty)."""
    out: dict[str, Any] = {"value": value, "unit": unit, "chunk_ids": list(chunk_ids), "rationale": "synthetic"}
    if mode is not None:
        out["grounding_mode"] = mode
    if validation is not None:
        out["validation"] = list(validation)
    if horizon is not None:
        out["source_horizon_days"] = horizon
    return out


def chunk(chunk_id: str, release_date: str, doc_type: str | None = None) -> dict[str, Any]:
    """One ``chunks[]`` entry; doc_type None omits the key."""
    out: dict[str, Any] = {"chunk_id": chunk_id, "release_date": release_date}
    if doc_type is not None:
        out["doc_type"] = doc_type
    return out


def response(shock_vector: dict[str, Any], chunks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"summary": "synthetic", "world_view_id": "macro-2026-10-06",
                            "shock_vector": shock_vector, "chunks": chunks}
    body.update(extra)
    return body


def defect_response(mode: str | None, doc_type: str | None = None) -> dict[str, Any]:
    """The run f76773c1 case (spec C3): USDTWD +5 pct citing two chunks, VIX +10 pts and UST_10Y -30 bp
    citing one each, every chunk dated 2025-02-26 and of ``doc_type`` (None -> no doc_type key)."""
    return response(
        {"USDTWD": field(5.0, "pct", ["k1", "k2"], mode),
         "VIX": field(10.0, "pts", ["k3"], mode),
         "UST_10Y": field(-30.0, "bp", ["k4"], mode)},
        [chunk(c, STALE_DATE, doc_type) for c in ("k1", "k2", "k3", "k4")])


# Failure kinds of spec Requirement 1 -> (transport kwargs, expected reason). Hand-built synthetic cases.
def failure(kind: str) -> tuple[dict[str, Any], str]:
    if kind == "http_error":
        return {"exc": MacroTransportError("HTTP 500 for http://macrorag/query")}, "macro_synthesis_failed"
    if kind == "timeout":
        return {"exc": TimeoutError("read timed out after 600s")}, "macro_synthesis_failed"
    if kind == "connection":
        return {"exc": ConnectionError("macro down")}, "macro_synthesis_failed"
    if kind == "parse":
        # a non-numeric value cannot be parsed into a shock field -> the synthesis call fails to parse
        return {"resp": response({"VIX": {"value": "ten", "unit": "pts", "chunk_ids": ["f1"]}},
                                 [{"chunk_id": "f1", "release_date": FRESH_DATE}])}, "macro_synthesis_failed"
    if kind == "empty":
        return {"resp": response({}, [{"chunk_id": "f1", "release_date": FRESH_DATE}])}, "macro_unavailable"
    if kind == "refused":
        return {"resp": response({"VIX": field(10.0, "pts", ["f1"], "direct")},
                                 [{"chunk_id": "f1", "release_date": FRESH_DATE}], refused=True)}, "macro_unavailable"
    if kind == "gate_direct":
        return {"resp": defect_response("direct")}, "macro_gate_failed"
    if kind == "gate_unmarked":
        return {"resp": defect_response(None)}, "macro_gate_failed"
    if kind == "gate_analog":
        # Requirement 4: analog grounding no longer exempts a stale research paper (the 9/11-analog case)
        return {"resp": defect_response("analog", "research_paper")}, "macro_gate_failed"
    if kind == "hard_invalid":
        # Requirement 5: every field carries a hard code -> no usable field remains -> macro_unavailable
        return {"resp": response(
            {"VIX": field(10.0, "pts", ["f1"], "direct", validation=["implausible_magnitude"]),
             "UST_10Y": field(-30.0, "bp", ["f1"], "direct", validation=["horizon_unknown", "horizon_mismatch"])},
            [chunk("f1", FRESH_DATE, EPISODE)])}, "macro_unavailable"
    if kind == "unmapped":
        # Requirement 1: XCCY_BASIS maps to no canonical factor -> no usable field -> macro_unavailable
        return {"resp": response({"XCCY_BASIS": field(-15.0, "bp", ["f1"], "direct")},
                                 [chunk("f1", FRESH_DATE)])}, "macro_unavailable"
    raise KeyError(kind)


FAILURE_KINDS = ("http_error", "timeout", "connection", "parse", "empty", "refused", "gate_direct", "gate_unmarked",
                 "gate_analog", "hard_invalid", "unmapped")


def macro_cfg(*, drop_scenario: str | None = None) -> dict[str, Any]:
    cfg = copy.deepcopy(load_macro_config(get_project_root()))
    if drop_scenario is not None:
        cfg["scenarios"].pop(drop_scenario, None)
    return cfg


def macro_provider(*, resp: dict[str, Any] | None = None, exc: BaseException | None = None,
                   no_client: bool = False, drop_scenario: str | None = None,
                   transport: FakeMacroTransport | None = None) -> MacroScenarioProvider:
    if transport is None:
        transport = FakeMacroTransport(resp=resp, exc=exc)
    client = None if no_client else MacroRAGClient(transport)
    return MacroScenarioProvider(macro_cfg(drop_scenario=drop_scenario), as_of=AS_OF, client=client,
                                 project_root=get_project_root())


def arag_cfg(scenario: str, required: list[str] | None) -> dict[str, Any]:
    """An arag.json-shaped dict. ``required=None`` -> the scenario has no entry at all."""
    scenarios: dict[str, Any] = {}
    if required is not None:
        scenarios[scenario] = {"required_services": list(required), "companies": []}
    return {"gate": {"freshness_years": 2, "min_chunks_per_leg": 1, "require_citations": True},
            "defaults": {"section_hint": "Risk Factors", "top_k": 8, "top_n_chunks": 3},
            "scenarios": scenarios}


def arag_provider(cfg: dict[str, Any]) -> AragCompanyProvider:
    return AragCompanyProvider(cfg, as_of=AS_OF, client=AragClient(InertAragTransport()),
                               project_root=get_project_root())


def dual(macro: MacroScenarioProvider | None, scenario: str, required: list[str] | None) -> DualRagEnrichmentProvider:
    return DualRagEnrichmentProvider(macro_provider=macro, arag_provider=arag_provider(arag_cfg(scenario, required)),
                                     project_root=get_project_root())


def gate_check(resp: dict[str, Any]) -> MacroGateResult:
    """Run the real MacroGate (committed gate settings) over the real parse + mapping of ``resp``."""
    synth = MacroSynthesis.from_response(resp)
    gate = MacroGate(AS_OF, freshness_days=FRESHNESS_DAYS, min_chunks_per_driver=1, require_citations=True)
    return gate.check(to_canonical_drivers(synth.fields, 100.0), synth.chunks)


def static_drivers(name: str) -> dict[str, float]:
    """Spec C2 oracle: the scenario's scenarios.json drivers converted with to_canonical."""
    spec = load_scenario_config(get_project_root())["scenarios"][name]
    return {k: to_canonical(k, v) for k, v in spec.get("drivers", {}).items()}


@pytest.fixture(scope="session")
def dm() -> SimpleNamespace:
    return SimpleNamespace(
        AS_OF=AS_OF, FRESHNESS_DAYS=FRESHNESS_DAYS, STALE_DATE=STALE_DATE, STALE_AGE_DAYS=STALE_AGE_DAYS,
        FRESH_DATE=FRESH_DATE, EPISODE=EPISODE, HARD_CODES=HARD_CODES, SOFT_CODES=SOFT_CODES,
        TIERS=TIERS, IR_NODES=IR_NODES, FAILURE_KINDS=FAILURE_KINDS,
        field=field, chunk=chunk, response=response, defect_response=defect_response, failure=failure,
        macro_cfg=macro_cfg, macro_provider=macro_provider, arag_cfg=arag_cfg, arag_provider=arag_provider,
        dual=dual, static_drivers=static_drivers, gate_check=gate_check, FakeMacroTransport=FakeMacroTransport,
        InertAragTransport=InertAragTransport,
    )
