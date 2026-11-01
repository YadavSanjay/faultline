"""Offline fakes and builders for the macro-curve-coherence spec tests.

Fakes sit only at the HTTP transports the spec allows (docs/specs/macro-curve-coherence.md, Notes): the
``MacroRAGClient`` transport and the ARag client transport. Everything else is real:
``MacroScenarioProvider``, ``DualRagEnrichmentProvider``, ``MacroGate``, the mapping and the committed
configs (or synthetic copies of them).

Dates: valuation date 2026-10-06 (the live run of spec Requirement 1), passed explicitly. A "fresh" chunk
is dated 2026-09-01 (35 days old, well inside the committed 550-day freshness window); a "stale" research
chunk is dated 2025-02-26 (587 days old: 365 to 2026-02-26, 28+31+30+31+30+31+31 = 212 to 2026-09-26, 10
more to 2026-10-06).
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.pricer.config import get_project_root
from src.scenariogen.arag.client import AragClient
from src.scenariogen.arag.provider import AragCompanyProvider
from src.scenariogen.config import load_macro_config
from src.scenariogen.dualrag import DualRagEnrichmentProvider
from src.scenariogen.macro.client import MacroRAGClient
from src.scenariogen.macro.gate import MacroGate, MacroGateResult
from src.scenariogen.macro.mapping import to_canonical_drivers
from src.scenariogen.macro.models import MacroSynthesis
from src.scenariogen.macro.provider import MacroScenarioProvider

AS_OF = date(2026, 10, 6)
FRESH_DATE = "2026-09-01"
STALE_DATE = "2025-02-26"
IR_NODES = ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3", "DGS5", "DGS7", "DGS10", "DGS20", "DGS30")


class FakeMacroTransport:
    """MacroRAG HTTP boundary: records every POST and answers with a canned body (or raises)."""

    def __init__(self, resp: dict[str, Any] | None = None, exc: BaseException | None = None):
        self.resp = resp
        self.exc = exc
        self.payloads: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str) -> dict[str, Any]:
        return {"status": "ok"}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append((path, copy.deepcopy(payload)))
        if self.exc is not None:
            raise self.exc
        return copy.deepcopy(self.resp or {})


class InertAragTransport:
    """ARag HTTP boundary: health is fine; any data call is unexpected (no scenario has companies)."""

    def get(self, path: str) -> dict[str, Any]:
        return {"status": "ok"}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"unexpected ARag call {path}")


def field(value: float, unit: str, chunk_ids: list[str] | None = None, *,
          validation: list[str] | None = None) -> dict[str, Any]:
    """One shock-vector field (MacroRAG synthesize shape), citing ``f1`` by default."""
    out: dict[str, Any] = {"value": value, "unit": unit, "chunk_ids": list(chunk_ids or ["f1"]),
                           "grounding_mode": "analog", "source_horizon_days": 10, "rationale": "synthetic"}
    if validation is not None:
        out["validation"] = list(validation)
    return out


def response(shock_vector: dict[str, Any], chunks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A synthesize response; the default chunk pool is one fresh market_episode card ``f1``."""
    if chunks is None:
        chunks = [{"chunk_id": "f1", "release_date": FRESH_DATE, "doc_type": "market_episode"}]
    return {"summary": "synthetic", "world_view_id": "macro-2026-10-14", "shock_vector": shock_vector,
            "chunks": chunks}


def macro_cfg(**scenario_overrides: dict[str, Any]) -> dict[str, Any]:
    """A deep copy of the committed macro.json; ``name={...}`` replaces keys of that scenario's entry
    (a value of None removes the key)."""
    cfg = copy.deepcopy(load_macro_config(get_project_root()))
    for name, over in scenario_overrides.items():
        entry = cfg["scenarios"].setdefault(name, {"synthesis_question": f"synthetic {name}", "filters": {}})
        for k, v in over.items():
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
    return cfg


def provider(cfg: dict[str, Any] | None = None, *, transport: FakeMacroTransport | None = None,
             resp: dict[str, Any] | None = None, root: Path | None = None) -> MacroScenarioProvider:
    if transport is None:
        transport = FakeMacroTransport(resp=resp)
    return MacroScenarioProvider(cfg if cfg is not None else macro_cfg(), as_of=AS_OF,
                                 client=MacroRAGClient(transport), project_root=root or get_project_root())


def synth_root(tmp: str, scenarios: dict[str, dict[str, Any]]) -> Path:
    """A temporary project root whose scenarios.json holds exactly ``scenarios`` (authored drivers)."""
    d = Path(tmp) / "configs" / "scenariogen"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"severity_tiers": {"mild": 1.0, "adverse": 1.5, "severe": 2.0},
           "scenarios": {n: {"method": "conditional_covariance", "horizon_days": 10, "drivers": drv,
                             "overlays": {}} for n, drv in scenarios.items()}}
    (d / "scenarios.json").write_text(json.dumps(doc), encoding="utf-8")
    return Path(tmp)


def dual(macro: MacroScenarioProvider, scenario: str, required: list[str]) -> DualRagEnrichmentProvider:
    arag_cfg = {"gate": {"freshness_years": 2, "min_chunks_per_leg": 1, "require_citations": True},
                "defaults": {"section_hint": "Risk Factors", "top_k": 8, "top_n_chunks": 3},
                "scenarios": {scenario: {"required_services": list(required), "companies": []}}}
    arag = AragCompanyProvider(arag_cfg, as_of=AS_OF, client=AragClient(InertAragTransport()),
                               project_root=get_project_root())
    return DualRagEnrichmentProvider(macro_provider=macro, arag_provider=arag, project_root=get_project_root())


def gate_check(resp: dict[str, Any]) -> MacroGateResult:
    """The real parse + mapping + MacroGate (committed gate settings: 550 days, 1 citation)."""
    synth = MacroSynthesis.from_response(resp)
    gate = MacroGate(AS_OF, freshness_days=550, min_chunks_per_driver=1, require_citations=True)
    return gate.check(to_canonical_drivers(synth.fields, 100.0), synth.chunks)


def synthesis_opts(transport: FakeMacroTransport) -> dict[str, Any]:
    """The ``synthesis`` object of the first POSTed body ({} when absent)."""
    assert transport.payloads, "no synthesis request was posted"
    return dict(transport.payloads[0][1].get("synthesis") or {})


@pytest.fixture(scope="session")
def mc() -> SimpleNamespace:
    return SimpleNamespace(
        AS_OF=AS_OF, FRESH_DATE=FRESH_DATE, STALE_DATE=STALE_DATE, IR_NODES=IR_NODES,
        FakeMacroTransport=FakeMacroTransport, field=field, response=response, macro_cfg=macro_cfg,
        provider=provider, synth_root=synth_root, dual=dual, gate_check=gate_check,
        synthesis_opts=synthesis_opts,
    )
