"""llm-deepseek-profile C1-C2: ``reasoning_extra_body`` and the backends that send it.

C1 checks the function itself (Requirement 1). C2 builds real backends from the committed config with
the ``openai`` SDK replaced by a recording fake (spec Notes) and reads the requests the SDK received.
The committed ``llm.json`` gives effort ``high`` to both DeepSeek models and no effort to any OpenAI model.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.gateway import reasoning_extra_body
from src.scenariogen.llm.profiles import LLMConfig, build_provider_backend

DEEPSEEK_EFFORTS = ("none", "low", "high", "max")
DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")
OPENAI_MODELS = ("gpt-5", "gpt-5-mini", "gpt-5-nano")
MESSAGES: list[dict[str, Any]] = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
TOOLS: list[dict[str, Any]] = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]

# Request variants a stage may send: plain, capped, JSON mode, and with tools.
VARIANTS: tuple[dict[str, Any], ...] = (
    {},
    {"max_completion_tokens": 8000},
    {"max_completion_tokens": 16000, "response_format": {"type": "json_object"}},
    {"tools": TOOLS, "tool_choice": "auto"},
)


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_deepseek_effort_levels() -> None:
    # oracle: Requirement 1 verbatim: ("deepseek", e) -> {"reasoning_effort": e} for each DeepSeek level.
    assert reasoning_extra_body("deepseek", "none") == {"reasoning_effort": "none"}
    assert reasoning_extra_body("deepseek", "low") == {"reasoning_effort": "low"}
    assert reasoning_extra_body("deepseek", "high") == {"reasoning_effort": "high"}
    assert reasoning_extra_body("deepseek", "max") == {"reasoning_effort": "max"}


@pytest.mark.criterion("C1")
def test_c1_no_effort_or_openai_gives_none() -> None:
    # oracle: Requirement 1: None for ("deepseek", None) and for ("openai", any effort or None); the
    # ("deepseek", "high") control is {"reasoning_effort": "high"} by the same requirement.
    assert reasoning_extra_body("deepseek", "high") == {"reasoning_effort": "high"}
    assert reasoning_extra_body("deepseek", None) is None
    assert reasoning_extra_body("openai", None) is None
    assert reasoning_extra_body("openai", "high") is None
    assert reasoning_extra_body("openai", "low") is None


@pytest.mark.criterion("C1")
@given(effort=st.sampled_from(DEEPSEEK_EFFORTS),
       openai_effort=st.one_of(st.none(), st.sampled_from(DEEPSEEK_EFFORTS), st.text(max_size=8)))
def test_c1_property_only_deepseek_with_effort_gets_a_body(effort: str, openai_effort: str | None) -> None:
    # oracle: Requirement 1: the body is exactly {"reasoning_effort": effort} for DeepSeek, and an
    # OpenAI model never gets one whatever effort is passed.
    body = reasoning_extra_body("deepseek", effort)
    assert body == {"reasoning_effort": effort}
    assert reasoning_extra_body("openai", openai_effort) is None


# ----------------------------------------------------------------------------------------- C2


def _requests(config: LLMConfig, provider: str, environ: dict[str, str], models: tuple[str, ...],
              sdk: SimpleNamespace, install: Callable[[Any], None]) -> list[dict[str, Any]]:
    """Sends every request variant for every model through a real backend; returns what the SDK got."""
    recorder = sdk.Recorder()
    install(recorder)
    backend = build_provider_backend(config, provider, environ)
    for model in models:
        for variant in VARIANTS:
            backend.create(model=model, messages=MESSAGES, **variant)
    return list(recorder.requests)


@pytest.mark.criterion("C2")
def test_c2_deepseek_requests_carry_effort_and_openai_requests_do_not(
        committed_config: Callable[[str], LLMConfig], sdk: SimpleNamespace,
        install_fake_openai: Callable[[Any], None]) -> None:
    # oracle: Requirement 2 + committed llm.json (effort "high" on deepseek-flash and deepseek-v4-pro,
    # no effort on gpt-5 / gpt-5-mini / gpt-5-nano): every DeepSeek request carries
    # extra_body == {"reasoning_effort": "high"}; no OpenAI request has an extra_body key.
    ds = _requests(committed_config("deepseek"), "deepseek", {"DEEPSEEK_API_KEY": "k"}, DEEPSEEK_MODELS,
                   sdk, install_fake_openai)
    assert len(ds) == len(DEEPSEEK_MODELS) * len(VARIANTS)
    assert [r["model"] for r in ds] == [m for m in DEEPSEEK_MODELS for _ in VARIANTS]
    for request in ds:
        assert request.get("extra_body") == {"reasoning_effort": "high"}, request["model"]

    oa = _requests(committed_config("openai"), "openai", {"OPENAI_API_KEY": "k"}, OPENAI_MODELS,
                   sdk, install_fake_openai)
    assert len(oa) == len(OPENAI_MODELS) * len(VARIANTS)
    for request in oa:
        assert "extra_body" not in request, request["model"]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_c2_metamorphic_effort_is_invariant_to_the_request_and_the_stage(
        committed_config: Callable[[str], LLMConfig], sdk: SimpleNamespace,
        install_fake_openai: Callable[[Any], None]) -> None:
    # oracle: Requirement 2: the body depends only on the model's effort, so changing the messages,
    # caps, JSON mode or tools never changes it, and every stage of the committed deepseek profile
    # (all mapped to deepseek-flash / deepseek-v4-pro, effort "high") gets {"reasoning_effort": "high"};
    # the same variants sent to the openai profile's stage models never carry extra_body.
    stages = ("discover", "evidence", "propose", "calibrate", "narrate", "verify", "attribution")
    ds_config = committed_config("deepseek")
    ds_models = tuple(ds_config.model_for(s) for s in stages)
    ds = _requests(ds_config, "deepseek", {"DEEPSEEK_API_KEY": "k"}, ds_models, sdk, install_fake_openai)
    bodies = {repr(r.get("extra_body")) for r in ds}
    assert bodies == {repr({"reasoning_effort": "high"})}

    oa_config = committed_config("openai")
    oa_models = tuple(oa_config.model_for(s) for s in stages)
    oa = _requests(oa_config, "openai", {"OPENAI_API_KEY": "k"}, oa_models, sdk, install_fake_openai)
    assert all("extra_body" not in r for r in oa)
