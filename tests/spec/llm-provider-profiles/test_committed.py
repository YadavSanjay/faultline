"""llm-provider-profiles C9: the committed llm.json and pricing.json load with an empty environ.

Expected stage models and max_tokens are the values listed in the spec's C9 (today's committed
llm.json for the openai profile; design §6 for the anthropic and deepseek profiles).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.scenariogen.llm.profiles import load_llm_config

CONFIG_DIR = Path(__file__).resolve().parents[3] / "configs" / "scenariogen" / "llm"
STAGES = ("discover", "evidence", "propose", "calibrate", "narrate", "verify", "attribution")

OPENAI_MODELS = {
    "discover": "gpt-5-nano", "evidence": "gpt-5-mini", "propose": "gpt-5", "calibrate": "gpt-5-mini",
    "narrate": "gpt-5-mini", "verify": "gpt-5-nano", "attribution": "gpt-5-mini",
}
OPENAI_MAX_TOKENS = {
    "discover": 8000, "evidence": 16000, "propose": 24000, "calibrate": 16000, "narrate": 16000,
    "verify": 8000, "attribution": 6000,
}
BIG = ("propose", "calibrate")


def _load(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        doc: dict[str, Any] = json.load(fh)
    return doc


def _expected(profile: str) -> dict[str, str]:
    big, small = {"anthropic": ("claude-sonnet-5-5", "claude-haiku-5-5"),
                  "deepseek": ("deepseek-v4-pro", "deepseek-flash")}[profile]
    return {s: big if s in BIG else small for s in STAGES}


@pytest.mark.criterion("C9")
def test_c9_default_profile_is_openai_with_todays_models() -> None:
    # oracle: spec C9 list (today's committed llm.json): openai stage models and max_tokens.
    cfg = load_llm_config(_load("llm.json"), _load("pricing.json"), environ={})
    assert cfg.provenance()["profile"] == "openai"
    assert {s: cfg.model_for(s) for s in STAGES} == OPENAI_MODELS
    assert {s: cfg.max_tokens_for(s) for s in STAGES} == OPENAI_MAX_TOKENS
    assert cfg.providers_in_use() == ["openai"]


@pytest.mark.criterion("C9")
@pytest.mark.parametrize("profile", ["anthropic", "deepseek"])
def test_c9_other_profiles_follow_design_table(profile: str) -> None:
    # oracle: spec C9 / design §6: the big model for propose and calibrate, the small one elsewhere.
    cfg = load_llm_config(_load("llm.json"), _load("pricing.json"), profile=profile, environ={})
    assert {s: cfg.model_for(s) for s in STAGES} == _expected(profile)
    assert cfg.providers_in_use() == [profile]


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@pytest.mark.parametrize("profile", ["openai", "anthropic", "deepseek"])
def test_c9_metamorphic_env_selection_equals_explicit(profile: str) -> None:
    # oracle: Requirement 2 on the committed files: selecting a profile through SCENARIOGEN_LLM_PROFILE gives
    # the same provenance as passing it explicitly, and its stage models are the C9 lists above.
    explicit = load_llm_config(_load("llm.json"), _load("pricing.json"), profile=profile, environ={})
    via_env = load_llm_config(_load("llm.json"), _load("pricing.json"),
                              environ={"SCENARIOGEN_LLM_PROFILE": profile})
    assert via_env.provenance() == explicit.provenance()
    expected = OPENAI_MODELS if profile == "openai" else _expected(profile)
    assert {s: explicit.model_for(s) for s in STAGES} == expected
