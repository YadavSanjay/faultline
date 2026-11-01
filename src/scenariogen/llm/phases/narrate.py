"""Narrate phase (P2).

A single free-text call (no tools): produces the 1-2 page markdown narrative explaining how the
scenario's shocks were derived, including the priced impact and the LLM derivation cost. The
inputs are all concrete (calibrated scenario, materialized vector, priced result, cost), so the
model writes the story rather than inventing numbers.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import LLMSettings
from ..gateway import InstrumentedChatClient
from ..prompts import PromptManager
from ..results import PricedScenario
from ..schema import CalibratedScenario


def narrate_scenario(
    *,
    gateway: InstrumentedChatClient,
    prompts: PromptManager,
    settings: LLMSettings,
    calibrated: CalibratedScenario,
    priced: PricedScenario,
    cost: dict[str, Any],
) -> str:
    priced_summary = json.dumps(
        {
            "total_pnl": priced.total_pnl,
            "pnl_by_asset_class": priced.pnl_by_asset_class,
            "capital_delta": priced.capital_delta,
            "es_base": priced.es_base,
            "es_shocked": priced.es_shocked,
            "plausibility": priced.plausibility_status,
        },
        indent=2,
    )
    system = prompts.render("narrate_system")
    user = prompts.render(
        "narrate_user",
        scenario=calibrated.model_dump_json(indent=2),
        shocks=json.dumps(priced.vector, indent=2, sort_keys=True),
        priced_summary=priced_summary,
        cost_summary=json.dumps(cost, indent=2),
    )
    result = gateway.complete(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=settings.model_for("narrate"),
        stage="narrate",
        max_completion_tokens=settings.max_tokens_for("narrate"),
    )
    return result.content
