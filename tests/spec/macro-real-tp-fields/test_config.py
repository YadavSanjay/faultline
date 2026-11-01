"""macro-real-tp-fields C5: the committed macro.json asks rate_capital for TERM_PREMIUM_10Y and
inflation_repricing for REAL_YIELD_10Y, every expected field directed "up"; the other scenarios unchanged.

Expected lists are transcribed by hand from Requirement 4; the unchanged scenarios are transcribed by hand
from the committed macro.json on main at 9982365 (before this change).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.pricer.config import get_project_root
from src.scenariogen.macro.provider import MacroScenarioProvider

# Requirement 4, in order; every direction "up".
EXPECTED = {
    "rate_capital": ["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "MOVE", "IG_OAS", "TERM_PREMIUM_10Y"],
    "inflation_repricing": ["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "MOVE", "VIX", "IG_OAS", "REAL_YIELD_10Y"],
}
# Unchanged scenarios: (expect_fields, directions) as committed at 9982365.
UNCHANGED = {
    "ai_deflation": (["IG_OAS", "HY_OAS", "MOVE", "VIX", "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y"],
                     {"IG_OAS": "up", "HY_OAS": "up", "MOVE": "up", "VIX": "up"}),
    "ai_efficiency_shock": (["VIX", "IG_OAS", "UST_2Y", "UST_10Y", "UST_30Y"],
                            {"VIX": "up", "UST_2Y": "down", "UST_10Y": "down", "UST_30Y": "down"}),
    "taiwan_geopolitical": (["USDTWD", "USDJPY", "IG_OAS", "HY_OAS", "MOVE", "VIX", "UST_2Y", "UST_5Y", "UST_10Y",
                             "UST_30Y"],
                            {"USDTWD": "up", "USDJPY": "down", "IG_OAS": "up", "HY_OAS": "up", "MOVE": "up",
                             "VIX": "up", "UST_2Y": "down", "UST_5Y": "down", "UST_10Y": "down",
                             "UST_30Y": "down"}),
    "tariff_shock": (["VIX", "MOVE", "IG_OAS", "HY_OAS", "USDJPY", "UST_2Y", "UST_10Y", "UST_30Y"],
                     {"VIX": "up", "MOVE": "up", "IG_OAS": "up", "HY_OAS": "up", "USDJPY": "down",
                      "UST_30Y": "up"}),
    "crowded_unwind": (["USDJPY", "VIX", "MOVE", "IG_OAS", "HY_OAS", "UST_2Y", "UST_10Y"],
                       {"USDJPY": "down", "VIX": "up", "MOVE": "up", "IG_OAS": "up", "HY_OAS": "up",
                        "UST_2Y": "down", "UST_10Y": "down"}),
    "ai_rotation": (["VIX", "UST_2Y", "UST_10Y"], {"VIX": "up", "UST_2Y": "down"}),
    "keyman_nvda": (["VIX", "IG_OAS", "MOVE"], {"VIX": "up", "IG_OAS": "up", "MOVE": "up"}),
    "ai_capex_credit": (["IG_OAS", "HY_OAS", "VIX", "UST_10Y", "UST_30Y"], {"IG_OAS": "up", "HY_OAS": "up"}),
}


@pytest.mark.criterion("C5")
def test_c5_committed_config_requests_the_new_fields(rt):
    # oracle: Requirement 4 (each synthesis_question and MacroRAG's positive_means): the two lists in order,
    # every direction "up"; the provider constructs on the committed config.
    cfg = rt.committed_cfg()
    for name, fields in EXPECTED.items():
        entry = cfg["scenarios"][name]
        assert entry.get("expect_fields") == fields
        assert entry.get("directions") == dict.fromkeys(fields, "up")
    MacroScenarioProvider(cfg, as_of=rt.AS_OF, client=None, project_root=get_project_root())


@pytest.mark.criterion("C5")
def test_c5_other_scenarios_unchanged(rt):
    # oracle: Requirement 4 ("the other scenarios' expect_fields and directions are unchanged"), transcribed
    # from main at 9982365; and the rate_capital list must have changed to Requirement 4's.
    cfg = rt.committed_cfg()
    assert cfg["scenarios"]["rate_capital"].get("expect_fields") == EXPECTED["rate_capital"]
    for name, (fields, directions) in UNCHANGED.items():
        entry = cfg["scenarios"][name]
        assert entry.get("expect_fields") == fields
        assert entry.get("directions") == directions


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(name=st.sampled_from(sorted(EXPECTED)))
def test_c5_request_sent_on_committed_config_asks_the_fields(rt, name):
    # oracle: Requirement 4: resolving either scenario on the committed config sends synthesis.fields equal to
    # its Requirement 4 list and synthesis.directions all "up" (same config, observed through the request).
    t = rt.FakeMacroTransport(resp=rt.response({}))
    rt.provider(rt.committed_cfg(), transport=t).resolve(name)
    assert t.payloads, "no synthesis request was posted"
    opts = dict(t.payloads[0][1].get("synthesis") or {})
    assert opts.get("fields") == EXPECTED[name]
    assert opts.get("directions") == dict.fromkeys(EXPECTED[name], "up")
