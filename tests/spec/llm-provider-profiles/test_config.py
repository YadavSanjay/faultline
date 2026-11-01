"""llm-provider-profiles C1-C4 and C8: loading, profile choice, validation, output cap, provenance.

Every expected value is read off the synthetic ``llm.json`` / ``pricing.json`` dicts built in
``conftest.py`` (docs/specs/llm-provider-profiles.md, Requirements 1-5 and 9), or written out by hand
below from those dicts.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.common.config.models import ConfigError
from src.scenariogen.llm.profiles import load_llm_config

STAGES = ("discover", "evidence", "propose", "calibrate", "narrate", "verify", "attribution")
PROFILES = ("openai", "anthropic", "mixed")
MODELS = ("gpt-5", "gpt-5-mini", "claude-sonnet-5-5", "claude-haiku-5-5", "deepseek-v4-pro", "deepseek-flash")

Factory = Callable[[], dict[str, Any]]


def _expected_lookups(doc: dict[str, Any], prof: str, stage: str) -> dict[str, Any]:
    model = doc["profiles"][prof][stage]
    spec = doc["models"][model]
    return {
        "model": model,
        "provider": spec["provider"],
        "effort": spec.get("effort"),
        "thinking": spec.get("thinking"),
        "json_mode": spec["json_mode"],
        "temperature": doc["stage_params"].get(stage, {}).get("temperature"),
    }


def _assert_lookups(cfg: Any, stage: str, exp: dict[str, Any]) -> None:
    assert cfg.model_for(stage) == exp["model"]
    assert cfg.provider_for(stage) == exp["provider"]
    assert cfg.effort_for(stage) == exp["effort"]
    assert cfg.thinking_for(stage) == exp["thinking"]
    assert cfg.json_mode_for(stage) == exp["json_mode"]
    if exp["temperature"] is None:
        assert cfg.temperature_for(stage) is None
    else:
        # tolerance: the temperature is passed through from the config unchanged.
        assert cfg.temperature_for(stage) == pytest.approx(exp["temperature"], rel=0, abs=0)


# ----------------------------------------------------------------------------------------- C1


@pytest.mark.criterion("C1")
def test_c1_lookups_match_written_values(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirements 1 and 9, read off the test's own dict (conftest DOC), plus literals written by hand.
    doc = make_doc()
    for prof in PROFILES:
        cfg = load_llm_config(make_doc(), make_pricing(), profile=prof, environ={})
        for stage in STAGES:
            _assert_lookups(cfg, stage, _expected_lookups(doc, prof, stage))
    mixed = load_llm_config(make_doc(), make_pricing(), profile="mixed", environ={})
    assert mixed.model_for("narrate") == "claude-sonnet-5-5"
    assert mixed.provider_for("narrate") == "anthropic"
    assert mixed.effort_for("narrate") == "high"
    assert mixed.thinking_for("narrate") == "adaptive"
    assert mixed.json_mode_for("narrate") == "json_schema"
    assert mixed.effort_for("calibrate") == "high"
    assert mixed.thinking_for("calibrate") is None
    assert mixed.effort_for("verify") is None
    # tolerance: the temperature is passed through from the config unchanged.
    assert mixed.temperature_for("evidence") == pytest.approx(0.2, rel=0, abs=0)
    assert mixed.temperature_for("propose") is None


@pytest.mark.criterion("C1")
def test_c1_unknown_stage_raises_keyerror(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 9: an unknown stage raises KeyError for every lookup; known stages still answer.
    cfg = load_llm_config(make_doc(), make_pricing(), profile="openai", environ={})
    assert cfg.model_for("propose") == "gpt-5"
    for lookup in (cfg.model_for, cfg.provider_for, cfg.effort_for, cfg.thinking_for, cfg.json_mode_for,
                   cfg.temperature_for, cfg.max_tokens_for):
        with pytest.raises(KeyError):
            lookup("brainstorm")


@pytest.mark.criterion("C1")
@given(data=st.data())
def test_c1_property_generated_profile(make_doc: Factory, make_pricing: Factory, data: st.DataObject) -> None:
    # oracle: Requirements 1 and 9: for a profile drawn at random (stage -> model) with optional stage
    # temperatures, each lookup equals what the dict says. gpt-5 (temperature: false) never gets a stage
    # temperature, and the fixed profiles only put gpt-5 on propose, so propose never gets one.
    doc = make_doc()
    gen = {s: data.draw(st.sampled_from(MODELS), label=s) for s in STAGES}
    for stage in STAGES:
        params = doc["stage_params"][stage]
        params.pop("temperature", None)
        if stage != "propose" and gen[stage] != "gpt-5":
            temp = data.draw(st.one_of(st.none(), st.floats(min_value=0.05, max_value=1.5)), label=f"t_{stage}")
            if temp is not None:
                params["temperature"] = temp
    doc["profiles"]["gen"] = gen
    expected = {s: _expected_lookups(doc, "gen", s) for s in STAGES}
    cfg = load_llm_config(doc, make_pricing(), profile="gen", environ={})
    for stage in STAGES:
        _assert_lookups(cfg, stage, expected[stage])


# ----------------------------------------------------------------------------------------- C2


@pytest.mark.criterion("C2")
def test_c2_profile_choice(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 2 by hand: "${SCENARIOGEN_LLM_PROFILE:-openai}" -> openai with an empty environ,
    # the variable's value when set; an explicit profile beats both. propose is gpt-5 / sonnet / gpt-5.
    cfg = load_llm_config(make_doc(), make_pricing(), environ={})
    assert cfg.provenance()["profile"] == "openai"
    assert cfg.model_for("propose") == "gpt-5"
    assert cfg.model_for("discover") == "gpt-5-mini"

    env = {"SCENARIOGEN_LLM_PROFILE": "anthropic"}
    cfg = load_llm_config(make_doc(), make_pricing(), environ=env)
    assert cfg.provenance()["profile"] == "anthropic"
    assert cfg.model_for("propose") == "claude-sonnet-5-5"

    cfg = load_llm_config(make_doc(), make_pricing(), profile="mixed", environ=env)
    assert cfg.provenance()["profile"] == "mixed"
    assert cfg.model_for("discover") == "deepseek-flash"

    doc = make_doc()
    doc["active_profile"] = "${OTHER_PROFILE_VAR}"
    cfg = load_llm_config(doc, make_pricing(), environ={"OTHER_PROFILE_VAR": "mixed"})
    assert cfg.model_for("calibrate") == "deepseek-v4-pro"


@pytest.mark.criterion("C2")
def test_c2_unknown_profile_raises(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 2: an unknown profile name, from the argument or from the environment, raises
    # ConfigError; the same document with a known name loads.
    assert load_llm_config(make_doc(), make_pricing(), profile="anthropic", environ={}).model_for(
        "verify") == "claude-haiku-5-5"
    with pytest.raises(ConfigError):
        load_llm_config(make_doc(), make_pricing(), profile="nope", environ={})
    with pytest.raises(ConfigError):
        load_llm_config(make_doc(), make_pricing(), environ={"SCENARIOGEN_LLM_PROFILE": "nope"})


@pytest.mark.criterion("C2")
@given(env_profile=st.one_of(st.none(), st.sampled_from(PROFILES)),
       explicit=st.one_of(st.none(), st.sampled_from(PROFILES)))
def test_c2_property_precedence(make_doc: Factory, make_pricing: Factory, env_profile: str | None,
                                explicit: str | None) -> None:
    # oracle: Requirement 2: chosen = explicit if given, else the variable if set, else "openai"; the
    # propose model is then read off the dict's profile.
    environ = {} if env_profile is None else {"SCENARIOGEN_LLM_PROFILE": env_profile}
    chosen = explicit if explicit is not None else (env_profile if env_profile is not None else "openai")
    cfg = load_llm_config(make_doc(), make_pricing(), profile=explicit, environ=environ)
    assert cfg.provenance()["profile"] == chosen
    assert cfg.model_for("propose") == make_doc()["profiles"][chosen]["propose"]


# ----------------------------------------------------------------------------------------- C3


def _set(path: tuple[Any, ...], value: Any, *, in_pricing: bool = False) -> Callable[..., None]:
    def mutate(doc: dict[str, Any], pricing: dict[str, Any]) -> None:
        target: Any = pricing if in_pricing else doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def _delete(path: tuple[Any, ...], *, in_pricing: bool = False) -> Callable[..., None]:
    def mutate(doc: dict[str, Any], pricing: dict[str, Any]) -> None:
        target: Any = pricing if in_pricing else doc
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


_PROVIDER = {"api_key_env": "MISTRAL_API_KEY", "timeout_s": 60.0}

# Each mutation turns the valid conftest config into one that Requirement 1, 3 or 5 forbids.
MUTATIONS: dict[str, Callable[..., None]] = {
    "unknown_top_level_key": _set(("default_model",), "gpt-5-mini"),
    "missing_top_level_key": _delete(("budgets",)),
    "unknown_provider_key": _set(("providers", "openai", "organization"), "org-1"),
    "unknown_model_key": _set(("models", "gpt-5-mini", "top_p"), 0.9),
    "unknown_profile_stage": _set(("profiles", "openai", "brainstorm"), "gpt-5-mini"),
    "unknown_stage_params_stage": _set(("stage_params", "brainstorm"), {"max_tokens": 100}),
    "unknown_stage_params_key": _set(("stage_params", "discover", "top_p"), 0.9),
    "unknown_retry_key": _set(("retry", "jitter"), True),
    "unknown_budgets_key": _set(("budgets", "max_wall_s"), 60),
    "unknown_pricing_row_key": _set(("gpt-5", "batch_input"), 0.0006, in_pricing=True),
    "unknown_provider_name": _set(("providers", "mistral"), _PROVIDER),
    "profile_model_not_in_models": _set(("profiles", "anthropic", "narrate"), "claude-opus-9"),
    "profile_missing_stage": _delete(("profiles", "mixed", "attribution")),
    "model_without_pricing_row_inactive_profile": _delete(("claude-sonnet-5-5",), in_pricing=True),
    "model_without_pricing_row_active_profile": _delete(("gpt-5-mini",), in_pricing=True),
    "temperature_on_temperature_false_model": _set(("stage_params", "propose", "temperature"), 0.3),
    "effort_on_openai_model": _set(("models", "gpt-5-mini", "effort"), "low"),
    "anthropic_effort_none": _set(("models", "claude-sonnet-5-5", "effort"), "none"),
    "anthropic_effort_unknown": _set(("models", "claude-sonnet-5-5", "effort"), "extreme"),
    "deepseek_effort_medium": _set(("models", "deepseek-v4-pro", "effort"), "medium"),
    "deepseek_effort_xhigh": _set(("models", "deepseek-v4-pro", "effort"), "xhigh"),
    "thinking_on_openai_model": _set(("models", "gpt-5", "thinking"), "adaptive"),
    "thinking_on_deepseek_model": _set(("models", "deepseek-v4-pro", "thinking"), "adaptive"),
    "anthropic_thinking_unknown": _set(("models", "claude-sonnet-5-5", "thinking"), "enabled"),
    "disabled_with_max": _set(("models", "claude-haiku-5-5", "effort"), "max"),
    "timeout_zero": _set(("providers", "anthropic", "timeout_s"), 0.0),
    "timeout_negative": _set(("providers", "openai", "timeout_s"), -1.0),
    "timeout_nan": _set(("providers", "deepseek", "timeout_s"), math.nan),
    "timeout_inf": _set(("providers", "openai", "timeout_s"), math.inf),
    "rate_negative": _set(("gpt-5", "input"), -0.001, in_pricing=True),
    "rate_nan": _set(("deepseek-v4-pro", "output"), math.nan, in_pricing=True),
    "tier_rate_negative": _set(("claude-haiku-5-5", "tiers", 0, "input"), -0.0005, in_pricing=True),
    "peak_rate_nan": _set(("deepseek-flash", "peak", "output"), math.nan, in_pricing=True),
    "tiers_and_peak": _set(("claude-haiku-5-5", "peak"),
                           {"weekdays_utc": ["01:00-04:00"], "input": 0.0002, "output": 0.001}, in_pricing=True),
    "peak_window_reversed": _set(("deepseek-flash", "peak", "weekdays_utc"), ["04:00-01:00"], in_pricing=True),
    "peak_window_empty": _set(("deepseek-flash", "peak", "weekdays_utc"), ["06:00-06:00"], in_pricing=True),
    "peak_window_no_dash": _set(("deepseek-flash", "peak", "weekdays_utc"), ["01:00"], in_pricing=True),
    "peak_window_bad_hour": _set(("deepseek-flash", "peak", "weekdays_utc"), ["25:00-26:00"], in_pricing=True),
    "peak_window_bad_minute": _set(("deepseek-flash", "peak", "weekdays_utc"), ["01:60-04:00"], in_pricing=True),
    "peak_window_not_a_time": _set(("deepseek-flash", "peak", "weekdays_utc"), ["one-four"], in_pricing=True),
}


def _between_tools_with_xhigh(doc: dict[str, Any], pricing: dict[str, Any]) -> None:
    doc["models"]["claude-sonnet-5-5"]["thinking"] = "between_tools"
    doc["models"]["claude-sonnet-5-5"]["effort"] = "xhigh"


MUTATIONS["between_tools_with_xhigh"] = _between_tools_with_xhigh


@pytest.mark.criterion("C3")
def test_c3_missing_pricing_row_names_the_model(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 5: a model any profile uses (claude-sonnet-5-5, used by the inactive anthropic and
    # mixed profiles) without a pricing row raises ConfigError naming the model.
    pricing = make_pricing()
    del pricing["claude-sonnet-5-5"]
    with pytest.raises(ConfigError, match="claude-sonnet-5-5"):
        load_llm_config(make_doc(), pricing, profile="openai", environ={})


@pytest.mark.criterion("C3")
def test_c3_unknown_keys_and_rules(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirements 1 and 3 by hand: an extra top-level key, an openai effort and between_tools with
    # effort max each raise ConfigError; between_tools with effort medium is allowed.
    doc = make_doc()
    doc["provider"] = "openai"
    with pytest.raises(ConfigError):
        load_llm_config(doc, make_pricing(), environ={})
    doc = make_doc()
    doc["models"]["gpt-5"]["effort"] = "high"
    with pytest.raises(ConfigError):
        load_llm_config(doc, make_pricing(), environ={})
    doc = make_doc()
    doc["models"]["claude-sonnet-5-5"].update(thinking="between_tools", effort="max")
    with pytest.raises(ConfigError):
        load_llm_config(doc, make_pricing(), environ={})
    doc = make_doc()
    doc["models"]["claude-sonnet-5-5"].update(thinking="between_tools", effort="medium")
    cfg = load_llm_config(doc, make_pricing(), profile="anthropic", environ={})
    assert cfg.thinking_for("propose") == "between_tools"
    assert cfg.effort_for("propose") == "medium"


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_c3_metamorphic_one_mutation_raises(make_doc: Factory, make_pricing: Factory, name: str) -> None:
    # oracle: Requirements 1, 3 and 5: the unchanged valid config loads (and answers a lookup from its dict),
    # and the same config with exactly this one forbidden change raises ConfigError.
    base = load_llm_config(make_doc(), make_pricing(), environ={})
    assert base.model_for("propose") == "gpt-5"
    doc, pricing = make_doc(), make_pricing()
    MUTATIONS[name](doc, pricing)
    with pytest.raises(ConfigError):
        load_llm_config(doc, pricing, environ={})


@pytest.mark.criterion("C3")
@given(effort=st.sampled_from(["none", "low", "medium", "high", "xhigh", "max", "minimal", "auto"]),
       thinking=st.sampled_from([None, "adaptive", "between_tools", "disabled", "enabled"]))
def test_c3_property_anthropic_effort_thinking(make_doc: Factory, make_pricing: Factory, effort: str,
                                               thinking: str | None) -> None:
    # oracle: Requirement 3 by hand: anthropic effort in {low, medium, high, xhigh, max}; thinking in
    # {adaptive, between_tools, disabled}; between_tools/disabled need effort in {low, medium, high}.
    doc = make_doc()
    spec = doc["models"]["claude-sonnet-5-5"]
    spec["effort"] = effort
    spec.pop("thinking")
    if thinking is not None:
        spec["thinking"] = thinking
    ok = effort in {"low", "medium", "high", "xhigh", "max"}
    ok = ok and thinking in {None, "adaptive", "between_tools", "disabled"}
    if thinking in {"between_tools", "disabled"}:
        ok = ok and effort in {"low", "medium", "high"}
    if ok:
        cfg = load_llm_config(doc, make_pricing(), profile="anthropic", environ={})
        assert cfg.effort_for("propose") == effort
        assert cfg.thinking_for("propose") == thinking
    else:
        with pytest.raises(ConfigError):
            load_llm_config(doc, make_pricing(), profile="anthropic", environ={})


@pytest.mark.criterion("C3")
@given(effort=st.sampled_from(["none", "low", "medium", "high", "xhigh", "max", "minimal"]))
def test_c3_property_deepseek_effort(make_doc: Factory, make_pricing: Factory, effort: str) -> None:
    # oracle: Requirement 3 by hand: deepseek effort in {none, low, high, max}, anything else raises.
    doc = make_doc()
    doc["models"]["deepseek-v4-pro"]["effort"] = effort
    if effort in {"none", "low", "high", "max"}:
        cfg = load_llm_config(doc, make_pricing(), profile="mixed", environ={})
        assert cfg.effort_for("calibrate") == effort
    else:
        with pytest.raises(ConfigError):
            load_llm_config(doc, make_pricing(), profile="mixed", environ={})


# ----------------------------------------------------------------------------------------- C4


@pytest.mark.criterion("C4")
def test_c4_max_tokens_capped_by_model(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 4 by hand from conftest: stage max_tokens discover 9000, evidence 16000, propose 24000,
    # calibrate none, narrate 40000, verify none, attribution 6000; caps gpt-5/gpt-5-mini 128000, sonnet 64000,
    # haiku 32000, deepseek-v4-pro 32000, deepseek-flash 8000.
    expected = {
        "openai": {"discover": 9000, "evidence": 16000, "propose": 24000, "calibrate": None, "narrate": 40000,
                   "verify": None, "attribution": 6000},
        "anthropic": {"discover": 9000, "evidence": 16000, "propose": 24000, "calibrate": None, "narrate": 32000,
                      "verify": None, "attribution": 6000},
        "mixed": {"discover": 8000, "evidence": 16000, "propose": 24000, "calibrate": None, "narrate": 40000,
                  "verify": None, "attribution": 6000},
    }
    for prof, by_stage in expected.items():
        cfg = load_llm_config(make_doc(), make_pricing(), profile=prof, environ={})
        assert {s: cfg.max_tokens_for(s) for s in STAGES} == by_stage, prof


@pytest.mark.criterion("C4")
@given(stage_caps=st.lists(st.one_of(st.none(), st.integers(min_value=1, max_value=300000)),
                           min_size=len(STAGES), max_size=len(STAGES)),
       model_caps=st.lists(st.integers(min_value=1, max_value=300000), min_size=len(MODELS), max_size=len(MODELS)),
       prof=st.sampled_from(PROFILES))
def test_c4_property_min_of_stage_and_model(make_doc: Factory, make_pricing: Factory, stage_caps: list[int | None],
                                            model_caps: list[int], prof: str) -> None:
    # oracle: Requirement 4: max_tokens_for(stage) = min(stage max_tokens, model max_output_tokens), or None.
    doc = make_doc()
    for model, cap in zip(MODELS, model_caps, strict=True):
        doc["models"][model]["max_output_tokens"] = cap
    for stage, cap in zip(STAGES, stage_caps, strict=True):
        doc["stage_params"][stage].pop("max_tokens", None)
        if cap is not None:
            doc["stage_params"][stage]["max_tokens"] = cap
    cfg = load_llm_config(doc, make_pricing(), profile=prof, environ={})
    for stage, cap in zip(STAGES, stage_caps, strict=True):
        model_cap = model_caps[MODELS.index(doc["profiles"][prof][stage])]
        assert cfg.max_tokens_for(stage) == (None if cap is None else min(cap, model_cap))


# ----------------------------------------------------------------------------------------- C8


@pytest.mark.criterion("C8")
def test_c8_single_provider_profile(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 9, written out by hand from the conftest openai profile.
    cfg = load_llm_config(make_doc(), make_pricing(), profile="openai", environ={})
    assert cfg.providers_in_use() == ["openai"]
    mini = {"model": "gpt-5-mini", "provider": "openai"}
    assert cfg.provenance() == {
        "profile": "openai",
        "stages": {"discover": mini, "evidence": mini, "propose": {"model": "gpt-5", "provider": "openai"},
                   "calibrate": mini, "narrate": mini, "verify": mini, "attribution": mini},
        "models": {"gpt-5": {"provider": "openai", "effort": None, "thinking": None},
                   "gpt-5-mini": {"provider": "openai", "effort": None, "thinking": None}},
    }


@pytest.mark.criterion("C8")
def test_c8_mixed_profile(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 9, written out by hand from the conftest mixed profile.
    cfg = load_llm_config(make_doc(), make_pricing(), profile="mixed", environ={})
    assert cfg.providers_in_use() == ["anthropic", "deepseek", "openai"]
    assert cfg.provenance() == {
        "profile": "mixed",
        "stages": {
            "discover": {"model": "deepseek-flash", "provider": "deepseek"},
            "evidence": {"model": "claude-haiku-5-5", "provider": "anthropic"},
            "propose": {"model": "gpt-5", "provider": "openai"},
            "calibrate": {"model": "deepseek-v4-pro", "provider": "deepseek"},
            "narrate": {"model": "claude-sonnet-5-5", "provider": "anthropic"},
            "verify": {"model": "gpt-5-mini", "provider": "openai"},
            "attribution": {"model": "deepseek-flash", "provider": "deepseek"},
        },
        "models": {
            "deepseek-flash": {"provider": "deepseek", "effort": None, "thinking": None},
            "claude-haiku-5-5": {"provider": "anthropic", "effort": "low", "thinking": "disabled"},
            "gpt-5": {"provider": "openai", "effort": None, "thinking": None},
            "deepseek-v4-pro": {"provider": "deepseek", "effort": "high", "thinking": None},
            "claude-sonnet-5-5": {"provider": "anthropic", "effort": "high", "thinking": "adaptive"},
            "gpt-5-mini": {"provider": "openai", "effort": None, "thinking": None},
        },
    }


@pytest.mark.criterion("C8")
@given(gen=st.fixed_dictionaries({s: st.sampled_from(MODELS) for s in STAGES}))
def test_c8_property_generated_profile(make_doc: Factory, make_pricing: Factory, gen: dict[str, str]) -> None:
    # oracle: Requirement 9 applied by hand to a randomly drawn profile: providers_in_use is the sorted set of
    # the drawn models' providers; provenance lists every stage and exactly the drawn models.
    doc = make_doc()
    for params in doc["stage_params"].values():
        params.pop("temperature", None)  # gpt-5 (temperature: false) may land on any stage
    doc["profiles"]["gen"] = gen
    models = doc["models"]
    cfg = load_llm_config(doc, make_pricing(), profile="gen", environ={})
    assert cfg.providers_in_use() == sorted({models[m]["provider"] for m in gen.values()})
    assert cfg.provenance() == {
        "profile": "gen",
        "stages": {s: {"model": m, "provider": models[m]["provider"]} for s, m in gen.items()},
        "models": {m: {"provider": models[m]["provider"], "effort": models[m].get("effort"),
                       "thinking": models[m].get("thinking")} for m in set(gen.values())},
    }
