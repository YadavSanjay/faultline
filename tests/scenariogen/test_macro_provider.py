"""S4 provider tests: macro override + freshness/citation GATE + static fallback."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from src.pricer.config import get_project_root
from src.scenariogen.config import load_macro_config
from src.scenariogen.macro.client import MacroRAGClient
from src.scenariogen.macro.provider import MacroScenarioProvider

AS_OF = date(2026, 6, 18)


class FakeTransport:
    def __init__(self, resp: dict[str, Any]):
        self.resp = resp

    def get(self, path: str) -> dict[str, Any]:
        return self.resp

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.resp


class RaisingTransport:
    def get(self, path: str) -> dict[str, Any]:
        raise ConnectionError("macro down")

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionError("macro down")


def _synth(release_date: str = "2026-06-12") -> dict[str, Any]:
    return {
        "summary": "Taiwan tail.",
        "world_view_id": "macro-2026-06-18",
        "shock_vector": {
            "USDTWD": {"value": 12.0, "unit": "pct", "chunk_ids": ["c1"]},
            "IG_OAS": {"value": 120.0, "unit": "bp", "chunk_ids": ["c1"]},
            "MOVE": {"value": 50.0, "unit": "pts", "chunk_ids": ["c1"]},
        },
        "chunks": [{"chunk_id": "c1", "release_date": release_date}],
    }


def _provider(client: MacroRAGClient | None) -> MacroScenarioProvider:
    return MacroScenarioProvider(load_macro_config(), as_of=AS_OF, client=client,
                                 project_root=get_project_root())


def test_no_client_uses_static_drivers():
    res = _provider(None).resolve("taiwan_geopolitical")
    assert not res.used_macro
    assert res.drivers["fx:TWD"] == pytest.approx(0.08)        # static scenarios.json
    assert res.drivers["eq:TSM"] == pytest.approx(-0.25)
    assert all(s["origin"] == "static" for s in res.sources.values())


def test_macro_override_merges_with_static():
    res = _provider(MacroRAGClient(FakeTransport(_synth()))).resolve("taiwan_geopolitical")
    assert res.used_macro and res.gate is not None and res.gate.passed
    assert res.drivers["fx:TWD"] == pytest.approx(0.12)        # macro overrides static 0.08
    assert res.drivers["credit"] == pytest.approx(0.012)       # macro-added leg
    assert res.drivers["ratesvol"] == pytest.approx(0.50)
    assert res.drivers["eq:TSM"] == pytest.approx(-0.25)       # equity stays house/static
    assert res.sources["fx:TWD"]["origin"] == "macrorag"
    assert res.sources["eq:TSM"]["origin"] == "static"
    assert res.world_view_id == "macro-2026-06-18"        # provenance pin captured


def test_stale_macro_fails_gate_and_falls_back():
    res = _provider(MacroRAGClient(FakeTransport(_synth("2024-01-01")))).resolve(
        "taiwan_geopolitical")
    assert not res.used_macro and res.gate is not None and not res.gate.passed
    assert res.drivers["fx:TWD"] == pytest.approx(0.08)        # fell back to static


def test_transport_error_falls_back():
    res = _provider(MacroRAGClient(RaisingTransport())).resolve("taiwan_geopolitical")
    assert not res.used_macro
    assert res.drivers["fx:TWD"] == pytest.approx(0.08)


def test_definition_for_builds_merged_definition():
    defn, res = _provider(MacroRAGClient(FakeTransport(_synth()))).definition_for(
        "taiwan_geopolitical", severity="severe", severity_scale=2.0)
    assert defn.severity == "severe" and defn.severity_scale == 2.0
    assert defn.drivers["fx:TWD"] == pytest.approx(0.12)       # macro-sourced
    assert "eqvol" in defn.overlays                            # static overlay preserved
    assert defn.version and res.used_macro
