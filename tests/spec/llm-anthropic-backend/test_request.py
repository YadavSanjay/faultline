"""C1: the request `AnthropicChatBackend.create` streams (spec Requirements 2 and 3)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend

_SCHEMA = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"],
           "additionalProperties": False}
_JSON_SCHEMA_RF = {"type": "json_schema", "json_schema": {"name": "out", "schema": _SCHEMA, "strict": True}}
_EPHEMERAL = {"type": "ephemeral"}  # Requirement 3: cache_control is on by default (Requirement 9)
_PARAMS = {"type": "object", "properties": {"q": {"type": "string"}}}
_OPENAI_TOOLS = [
    {"type": "function", "function": {"name": "arag__search", "description": "search", "parameters": _PARAMS}},
    {"type": "function", "function": {"name": "macro__view", "description": "view", "parameters": {"type": "object"}}},
]
_ANTHROPIC_TOOLS = [
    {"name": "arag__search", "description": "search", "input_schema": _PARAMS},
    {"name": "macro__view", "description": "view", "input_schema": {"type": "object"}},
]


def _stream(kit: SimpleNamespace, model: str, **kwargs: Any) -> dict[str, Any]:
    """Calls `create` once over a fake client; returns the kwargs `messages.stream` received."""
    fake = kit.FakeAnthropicClient([kit.make_message([kit.text_block("ok")], model=model)])
    AnthropicChatBackend(client=fake, models=kit.MODELS).create(model=model, **kwargs)
    assert len(fake.calls) == 1
    assert fake.entered == 1
    assert fake.exited == 1
    request: dict[str, Any] = fake.calls[0]
    return request


@pytest.mark.criterion("C1")
def test_system_messages_are_joined(kit: SimpleNamespace) -> None:
    # oracle: Requirement 3 — system contents joined with "\n\n" in order; the rest in order; Haiku has
    # no thinking/effort, so max_tokens is its 32000 cap and only the default cache_control is added.
    request = _stream(kit, kit.HAIKU, messages=[
        {"role": "system", "content": "You are a risk analyst."},
        {"role": "user", "content": "q1"},
        {"role": "system", "content": "Answer in JSON."},
        {"role": "user", "content": "q2"},
    ])
    assert request == {
        "model": "claude-haiku-4-5",
        "max_tokens": 32000,
        "system": "You are a risk analyst.\n\nAnswer in JSON.",
        "messages": [{"role": "user", "content": "q1"}, {"role": "user", "content": "q2"}],
        "cache_control": {"type": "ephemeral"},
    }


@pytest.mark.criterion("C1")
def test_no_system_key_without_system_messages(kit: SimpleNamespace) -> None:
    # oracle: Requirement 3 — "The key is left out when there are none."
    request = _stream(kit, kit.HAIKU, messages=[{"role": "user", "content": "q"}], max_completion_tokens=500)
    assert request == {"model": "claude-haiku-4-5", "max_tokens": 500, "cache_control": _EPHEMERAL,
                       "messages": [{"role": "user", "content": "q"}]}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("tool_choice", "expected_choice"), [
    ("auto", {"type": "auto"}),
    ("none", {"type": "none"}),
    (None, None),
])
def test_tools_and_tool_choice(kit: SimpleNamespace, tool_choice: str | None, expected_choice: Any) -> None:
    # oracle: Requirement 3 — function tools become {name, description, input_schema}; "auto"/"none"
    # map to {"type": ...}; None leaves tool_choice out.
    request = _stream(kit, kit.HAIKU, messages=[{"role": "user", "content": "q"}], max_completion_tokens=1000,
                      tools=_OPENAI_TOOLS, tool_choice=tool_choice)
    expected: dict[str, Any] = {"model": "claude-haiku-4-5", "max_tokens": 1000,
                                "messages": [{"role": "user", "content": "q"}], "tools": _ANTHROPIC_TOOLS,
                                "cache_control": _EPHEMERAL}
    if expected_choice is not None:
        expected["tool_choice"] = expected_choice
    assert request == expected


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("tools", [None, []])
def test_no_tools_means_no_tool_choice(kit: SimpleNamespace, tools: list[dict[str, Any]] | None) -> None:
    # oracle: Requirement 3 — tools left out when empty or None, and tool_choice is sent only with tools.
    request = _stream(kit, kit.HAIKU, messages=[{"role": "user", "content": "q"}], max_completion_tokens=10,
                      tools=tools, tool_choice="none")
    assert request == {"model": "claude-haiku-4-5", "max_tokens": 10, "cache_control": _EPHEMERAL,
                       "messages": [{"role": "user", "content": "q"}]}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize(("requested", "expected"), [(None, 64000), (100_000, 64000), (64000, 64000), (2048, 2048)])
def test_max_tokens_cap_and_default(kit: SimpleNamespace, requested: int | None, expected: int) -> None:
    # oracle: Requirement 3 — max_tokens = min(max_completion_tokens, 64000) for Sonnet, 64000 when None.
    # Sonnet carries thinking "adaptive" and effort "high".
    request = _stream(kit, kit.SONNET, messages=[{"role": "user", "content": "q"}], max_completion_tokens=requested)
    assert request == {
        "model": "claude-sonnet-5-5",
        "max_tokens": expected,
        "messages": [{"role": "user", "content": "q"}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "cache_control": _EPHEMERAL,
    }


@pytest.mark.criterion("C1")
def test_json_schema_with_effort(kit: SimpleNamespace) -> None:
    # oracle: Requirement 3 (D7) — output_config carries both the effort and the json_schema format.
    request = _stream(kit, kit.SONNET, messages=[{"role": "user", "content": "q"}], max_completion_tokens=4000,
                      response_format=_JSON_SCHEMA_RF)
    assert request == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": "q"}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": _SCHEMA}},
        "cache_control": _EPHEMERAL,
    }


@pytest.mark.criterion("C1")
def test_json_schema_without_effort(kit: SimpleNamespace) -> None:
    # oracle: Requirement 3 (D7) — Haiku has no effort, so output_config holds only the format.
    request = _stream(kit, kit.HAIKU, messages=[{"role": "user", "content": "q"}], max_completion_tokens=4000,
                      response_format=_JSON_SCHEMA_RF)
    assert request == {
        "model": "claude-haiku-4-5",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": "q"}],
        "output_config": {"format": {"type": "json_schema", "schema": _SCHEMA}},
        "cache_control": _EPHEMERAL,
    }


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-sonnet-5-5"])
def test_json_object_sends_nothing(kit: SimpleNamespace, model: str) -> None:
    # oracle: Requirement 3 — response_format={"type": "json_object"} adds no key; Haiku then has no
    # output_config at all, Sonnet only its effort.
    request = _stream(kit, model, messages=[{"role": "user", "content": "q"}], max_completion_tokens=300,
                      response_format={"type": "json_object"})
    if model == "claude-haiku-4-5":
        assert request == {"model": model, "max_tokens": 300, "messages": [{"role": "user", "content": "q"}],
                           "cache_control": _EPHEMERAL}
    else:
        assert request == {"model": model, "max_tokens": 300, "messages": [{"role": "user", "content": "q"}],
                           "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"},
                           "cache_control": _EPHEMERAL}


# ------------------------------------------------------------------------------------------ property
_TEXT = st.text(alphabet="abc xyz.\n", min_size=1, max_size=12)


@pytest.mark.criterion("C1")
@given(
    model=st.sampled_from(["claude-sonnet-5-5", "claude-haiku-4-5"]),
    turns=st.lists(st.tuples(st.sampled_from(["system", "user"]), _TEXT), min_size=1, max_size=6),
    max_completion_tokens=st.one_of(st.none(), st.integers(1, 200_000)),
    n_tools=st.integers(0, 2),
    tool_choice=st.sampled_from(["auto", "none", None]),
    response_format=st.sampled_from(["none", "json_object", "json_schema"]),
)
def test_request_property(kit: SimpleNamespace, model: str, turns: list[tuple[str, str]],
                          max_completion_tokens: int | None, n_tools: int, tool_choice: str | None,
                          response_format: str) -> None:
    # oracle: Requirement 3 — the expected request is built here key by key from the written rules.
    messages = [{"role": role, "content": text} for role, text in turns]
    cfg = kit.MODELS[model]
    rf: dict[str, Any] | None = {"none": None, "json_object": {"type": "json_object"},
                                 "json_schema": _JSON_SCHEMA_RF}[response_format]
    cap = cfg["max_output_tokens"]
    expected: dict[str, Any] = {
        "model": model,
        "max_tokens": cap if max_completion_tokens is None else min(max_completion_tokens, cap),
        "messages": [m for m in messages if m["role"] != "system"],
    }
    systems = [m["content"] for m in messages if m["role"] == "system"]
    if systems:
        expected["system"] = "\n\n".join(systems)
    if n_tools:
        expected["tools"] = _ANTHROPIC_TOOLS[:n_tools]
        if tool_choice is not None:
            expected["tool_choice"] = {"type": tool_choice}
    if cfg["thinking"] is not None:
        expected["thinking"] = {"type": cfg["thinking"]}
    output_config: dict[str, Any] = {}
    if cfg["effort"] is not None:
        output_config["effort"] = cfg["effort"]
    if response_format == "json_schema":
        output_config["format"] = {"type": "json_schema", "schema": _SCHEMA}
    expected["cache_control"] = _EPHEMERAL
    if output_config:
        expected["output_config"] = output_config
    request = _stream(kit, model, messages=messages, max_completion_tokens=max_completion_tokens,
                      response_format=rf, tools=_OPENAI_TOOLS[:n_tools] or None, tool_choice=tool_choice)
    assert request == expected


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_json_object_is_the_same_request_as_none(kit: SimpleNamespace) -> None:
    # oracle: Requirement 3 — json_object "sends nothing", so the request equals the one with no
    # response_format; json_schema differs from it only in output_config["format"].
    base: dict[str, Any] = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}],
                            "max_completion_tokens": 777, "tools": _OPENAI_TOOLS, "tool_choice": "auto"}
    plain = _stream(kit, kit.SONNET, **base)
    json_object = _stream(kit, kit.SONNET, response_format={"type": "json_object"}, **base)
    json_schema = _stream(kit, kit.SONNET, response_format=_JSON_SCHEMA_RF, **base)
    assert json_object == plain
    assert {k: v for k, v in json_schema.items() if k != "output_config"} == \
        {k: v for k, v in plain.items() if k != "output_config"}
    assert json_schema["output_config"] == {**plain["output_config"],
                                            "format": {"type": "json_schema", "schema": _SCHEMA}}
