"""Offline fakes and builders for the macro-real-tp-fields spec tests.

Fakes sit only at the ``MacroRAGClient`` HTTP transport (docs/specs/macro-real-tp-fields.md, Notes).
Everything else is real: ``MacroScenarioProvider``, ``MacroGate``, the mapping, the models and the
committed configs (or synthetic copies of them).

Dates: valuation date 2026-10-06, passed explicitly. A "fresh" chunk is dated 2026-09-01 (35 days old,
well inside the committed 550-day freshness window).
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
from src.scenariogen.config import load_macro_config
from src.scenariogen.macro.client import MacroRAGClient
from src.scenariogen.macro.provider import MacroScenarioProvider

AS_OF = date(2026, 10, 6)
FRESH_DATE = "2026-09-01"
FIXTURES = Path(__file__).parent / "fixtures"

# Requirement 3: the field names with a canonical mapping (transcribed by hand from the spec).
MAPPED = frozenset({"UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "UST", "IG_OAS", "HY_OAS", "MOVE", "VIX",
                    "USDTWD", "USDJPY", "REAL_YIELD_10Y", "TERM_PREMIUM_10Y"})
# Notes: MacroRAG's 19-field shock vocabulary (src/synthesis/vocab.py), transcribed by hand.
VOCAB = ("UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "UST", "IG_OAS", "HY_OAS", "MOVE", "MOVE_1Y", "MOVE_5Y",
         "MOVE_10Y", "MOVE_30Y", "VIX", "EQUITY_US", "USDTWD", "USDJPY", "XCCY_BASIS", "REAL_YIELD_10Y",
         "TERM_PREMIUM_10Y")


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


def field(value: float, unit: str = "bp", chunk_ids: list[str] | None = None, *,
          validation: list[str] | None = None, horizon: int | None = 10,
          rationale: str = "synthetic", grounding_mode: str = "analog") -> dict[str, Any]:
    """One shock-vector field (MacroRAG synthesize shape), citing ``f1`` by default."""
    out: dict[str, Any] = {"value": value, "unit": unit,
                           "chunk_ids": list(["f1"] if chunk_ids is None else chunk_ids),
                           "grounding_mode": grounding_mode, "rationale": rationale}
    if horizon is not None:
        out["source_horizon_days"] = horizon
    if validation is not None:
        out["validation"] = list(validation)
    return out


def response(shock_vector: dict[str, Any], chunks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A synthesize response; the default chunk pool is one fresh market_episode card ``f1``."""
    if chunks is None:
        chunks = [{"chunk_id": "f1", "release_date": FRESH_DATE, "doc_type": "market_episode"}]
    return {"summary": "synthetic", "world_view_id": "macro-2026-10-21", "shock_vector": shock_vector,
            "chunks": chunks}


def committed_cfg() -> dict[str, Any]:
    return copy.deepcopy(load_macro_config(get_project_root()))


def macro_cfg(**scenario_overrides: dict[str, Any]) -> dict[str, Any]:
    """A deep copy of the committed macro.json; ``name={...}`` replaces keys of that scenario's entry
    (a value of None removes the key)."""
    cfg = committed_cfg()
    for name, over in scenario_overrides.items():
        entry = cfg["scenarios"].setdefault(name, {"synthesis_question": f"synthetic {name}", "filters": {}})
        for k, v in over.items():
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
    return cfg


def synth_root(tmp: str, scenarios: dict[str, dict[str, Any]]) -> Path:
    """A temporary project root whose scenarios.json holds exactly ``scenarios`` (authored drivers)."""
    d = Path(tmp) / "configs" / "scenariogen"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"severity_tiers": {"mild": 1.0, "adverse": 1.5, "severe": 2.0},
           "scenarios": {n: {"method": "conditional_covariance", "horizon_days": 10, "drivers": drv,
                             "overlays": {}} for n, drv in scenarios.items()}}
    (d / "scenarios.json").write_text(json.dumps(doc), encoding="utf-8")
    return Path(tmp)


def provider(cfg: dict[str, Any] | None = None, *, transport: FakeMacroTransport | None = None,
             resp: dict[str, Any] | None = None, root: Path | None = None) -> MacroScenarioProvider:
    if transport is None:
        transport = FakeMacroTransport(resp=resp)
    return MacroScenarioProvider(cfg if cfg is not None else macro_cfg(), as_of=AS_OF,
                                 client=MacroRAGClient(transport), project_root=root or get_project_root())


def hard_codes_fixture() -> set[str]:
    doc = json.loads((FIXTURES / "macrorag_hard_codes.json").read_text(encoding="utf-8"))
    return set(doc["HARD_VALIDATION_CODES"])


@pytest.fixture(scope="session")
def rt() -> SimpleNamespace:
    return SimpleNamespace(
        AS_OF=AS_OF, FRESH_DATE=FRESH_DATE, MAPPED=MAPPED, VOCAB=VOCAB,
        FakeMacroTransport=FakeMacroTransport, field=field, response=response, committed_cfg=committed_cfg,
        macro_cfg=macro_cfg, synth_root=synth_root, provider=provider, hard_codes_fixture=hard_codes_fixture,
    )
