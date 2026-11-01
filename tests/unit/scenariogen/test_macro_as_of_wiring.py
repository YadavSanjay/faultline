"""Unit tests for macro-as-of-filter wiring: every MacroRAG synthesis request is pinned to the run's as_of.

The locked spec tests cover ``point_in_time_filter``; these cover the provider sending the capped
``filters.date_to``, keeping an earlier configured cap, recording it, and failing a misconfigured
window as ``macro_filter_invalid`` (a fake transport; no network, no data/).
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

from src.pricer.config import get_project_root
from src.scenariogen.config import load_macro_config
from src.scenariogen.macro.client import MacroRAGClient
from src.scenariogen.macro.provider import FALLBACK_FILTER_INVALID, MacroScenarioProvider

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


def _provider(transport: CapturingTransport, date_from: str | None = None,
              date_to: str | None = None) -> MacroScenarioProvider:
    cfg = copy.deepcopy(load_macro_config())
    filters = cfg["scenarios"][SCENARIO].setdefault("filters", {})
    filters["date_from"], filters["date_to"] = date_from, date_to
    return MacroScenarioProvider(cfg, as_of=AS_OF, client=MacroRAGClient(transport),
                                 project_root=get_project_root())


def test_request_is_capped_at_the_run_as_of_and_recorded():
    t = CapturingTransport()
    res = _provider(t).resolve(SCENARIO)
    assert t.bodies[-1]["filters"]["date_to"] == "2026-06-18"
    assert res.date_to == "2026-06-18" and res.to_dict()["date_to"] == "2026-06-18"
    assert res.used_macro


def test_an_earlier_configured_cap_is_kept_and_a_later_one_is_capped():
    t = CapturingTransport()
    _provider(t, date_to="2025-12-31").resolve(SCENARIO)
    assert t.bodies[-1]["filters"]["date_to"] == "2025-12-31"
    _provider(t, date_to="2027-01-01").resolve(SCENARIO)
    assert t.bodies[-1]["filters"]["date_to"] == "2026-06-18"


def test_a_window_starting_after_as_of_fails_without_a_request():
    t = CapturingTransport()
    res = _provider(t, date_from="2026-07-01").resolve(SCENARIO)
    assert res.fallback_reason == FALLBACK_FILTER_INVALID and "2026-07-01" in res.fallback_detail
    assert not res.used_macro and t.bodies == []
