"""Synthetic configs for the llm-provider-profiles spec tests.

``make_doc()`` and ``make_pricing()`` return fresh deep copies of one valid ``llm.json`` document and
its ``pricing.json`` rate card, written by hand from Requirements 1, 3 and 5 of
docs/specs/llm-provider-profiles.md. Tests read expected values straight off these dicts.

Profiles:
- ``openai``: single provider (gpt-5 for propose, gpt-5-mini elsewhere);
- ``anthropic``: claude-sonnet-5-5 for propose/calibrate, claude-haiku-5-5 elsewhere;
- ``mixed``: all three providers.

The fixtures are session-scoped factories so hypothesis tests may use them.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

STAGES = ("discover", "evidence", "propose", "calibrate", "narrate", "verify", "attribution")

DOC: dict[str, Any] = {
    "active_profile": "${SCENARIOGEN_LLM_PROFILE:-openai}",
    "providers": {
        "openai": {"api_key_env": "OPENAI_API_KEY", "timeout_s": 600.0},
        "anthropic": {"api_key_env": "ANTHROPIC_API_KEY", "timeout_s": 300.0},
        "deepseek": {
            "api_key_env": "DEEPSEEK_API_KEY",
            "timeout_s": 120.0,
            "base_url": "https://api.deepseek.example/v1",
        },
    },
    "models": {
        "gpt-5": {"provider": "openai", "max_output_tokens": 128000, "json_mode": "json_schema",
                  "temperature": False},
        "gpt-5-mini": {"provider": "openai", "max_output_tokens": 128000, "json_mode": "json_object"},
        "claude-sonnet-5-5": {"provider": "anthropic", "max_output_tokens": 64000, "json_mode": "json_schema",
                              "effort": "high", "thinking": "adaptive"},
        "claude-haiku-5-5": {"provider": "anthropic", "max_output_tokens": 32000, "json_mode": "json_object",
                             "effort": "low", "thinking": "disabled"},
        "deepseek-v4-pro": {"provider": "deepseek", "max_output_tokens": 32000, "json_mode": "json_object",
                            "effort": "high"},
        "deepseek-flash": {"provider": "deepseek", "max_output_tokens": 8000, "json_mode": "json_object"},
    },
    "profiles": {
        "openai": {
            "discover": "gpt-5-mini", "evidence": "gpt-5-mini", "propose": "gpt-5", "calibrate": "gpt-5-mini",
            "narrate": "gpt-5-mini", "verify": "gpt-5-mini", "attribution": "gpt-5-mini",
        },
        "anthropic": {
            "discover": "claude-haiku-5-5", "evidence": "claude-haiku-5-5", "propose": "claude-sonnet-5-5",
            "calibrate": "claude-sonnet-5-5", "narrate": "claude-haiku-5-5", "verify": "claude-haiku-5-5",
            "attribution": "claude-haiku-5-5",
        },
        "mixed": {
            "discover": "deepseek-flash", "evidence": "claude-haiku-5-5", "propose": "gpt-5",
            "calibrate": "deepseek-v4-pro", "narrate": "claude-sonnet-5-5", "verify": "gpt-5-mini",
            "attribution": "deepseek-flash",
        },
    },
    "stage_params": {
        "discover": {"max_tokens": 9000},
        "evidence": {"max_tokens": 16000, "temperature": 0.2},
        "propose": {"max_tokens": 24000},
        "calibrate": {"temperature": 0.5},
        "narrate": {"max_tokens": 40000},
        "verify": {"temperature": 0.1},
        "attribution": {"max_tokens": 6000},
    },
    "retry": {"max_attempts": 4, "min_wait_s": 1.0, "max_wait_s": 30.0},
    "budgets": {"max_tool_calls_per_phase": 6, "max_total_cost_usd": 5.0, "max_scenarios": 10,
                "max_candidates": 10},
}

# USD per 1,000 tokens. Haiku-style and DeepSeek-flash-style rows are the spec Notes' rates.
PRICING: dict[str, Any] = {
    "gpt-5": {"input": 0.00125, "output": 0.01, "cached_input": 0.000125},
    "gpt-5-mini": {"input": 0.00025, "output": 0.002, "cached_input": 0.000025},
    "claude-sonnet-5-5": {"input": 0.0003, "output": 0.0015, "cached_input": 0.00003, "cache_write": 0.000375},
    "claude-haiku-5-5": {
        "input": 0.0001, "output": 0.0005, "cached_input": 0.00001, "cache_write": 0.000125,
        "tiers": [{"above_prompt_tokens": 100000, "input": 0.0005, "output": 0.0025, "cached_input": 0.00005,
                   "cache_write": 0.000625}],
    },
    "deepseek-v4-pro": {"input": 0.0004, "output": 0.0016},
    "deepseek-flash": {
        "input": 0.00015, "cached_input": 0.000003, "output": 0.0006,
        "peak": {"weekdays_utc": ["01:00-04:00", "06:00-10:00"], "input": 0.0003, "cached_input": 0.000006,
                 "output": 0.0012},
    },
}


@pytest.fixture(scope="session")
def make_doc() -> Callable[[], dict[str, Any]]:
    """A factory for a fresh copy of the valid llm.json document."""
    return lambda: copy.deepcopy(DOC)


@pytest.fixture(scope="session")
def make_pricing() -> Callable[[], dict[str, Any]]:
    """A factory for a fresh copy of the valid pricing.json rate card."""
    return lambda: copy.deepcopy(PRICING)
