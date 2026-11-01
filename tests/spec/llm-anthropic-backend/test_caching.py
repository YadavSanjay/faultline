"""C7: prompt caching — the top-level cache_control and its pricing through the gateway (spec Requirement 9)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend

_EPHEMERAL = {"type": "ephemeral"}
_TOOLS = [{"type": "function", "function": {"name": "arag__search", "description": "search",
                                            "parameters": {"type": "object"}}}]
_MSG = [{"role": "system", "content": "long system prompt"}, {"role": "user", "content": "q"}]
# The request Requirement 3 gives for Sonnet over _MSG/_TOOLS, tool_choice "auto", max 2000, minus cache_control.
_SONNET_REQUEST: dict[str, Any] = {
    "model": "claude-sonnet-5-5",
    "max_tokens": 2000,
    "system": "long system prompt",
    "messages": [{"role": "user", "content": "q"}],
    "tools": [{"name": "arag__search", "description": "search", "input_schema": {"type": "object"}}],
    "tool_choice": {"type": "auto"},
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "high"},
}


def _request(kit: SimpleNamespace, caching: Any, **kwargs: Any) -> dict[str, Any]:
    """One `create` over a fake client; `caching` is "default" (keyword left out) or the bool to pass."""
    fake = kit.FakeAnthropicClient([kit.make_message([kit.text_block("ok")], model=kwargs["model"])])
    if caching == "default":
        backend = AnthropicChatBackend(client=fake, models=kit.MODELS)
    else:
        backend = AnthropicChatBackend(client=fake, models=kit.MODELS, prompt_caching=caching)
    backend.create(**kwargs)
    assert len(fake.calls) == 1
    request: dict[str, Any] = fake.calls[0]
    return request


def _sonnet(kit: SimpleNamespace, caching: Any) -> dict[str, Any]:
    return _request(kit, caching, model=kit.SONNET, messages=_MSG, tools=_TOOLS, tool_choice="auto",
                    max_completion_tokens=2000)


@pytest.mark.criterion("C7")
def test_caching_is_on_by_default(kit: SimpleNamespace) -> None:
    # oracle: Requirements 3 and 9 — without the keyword, the request carries cache_control ephemeral.
    assert _sonnet(kit, "default") == {**_SONNET_REQUEST, "cache_control": {"type": "ephemeral"}}


@pytest.mark.criterion("C7")
def test_caching_explicitly_on(kit: SimpleNamespace) -> None:
    # oracle: Requirements 3 and 9 — prompt_caching=True gives the same request as the default.
    assert _sonnet(kit, True) == {**_SONNET_REQUEST, "cache_control": {"type": "ephemeral"}}


@pytest.mark.criterion("C7")
def test_caching_off_leaves_cache_control_out(kit: SimpleNamespace) -> None:
    # oracle: Requirement 3 — with prompt_caching=False the key is left out; everything else is unchanged.
    request = _sonnet(kit, False)
    assert "cache_control" not in request
    assert request == _SONNET_REQUEST


@pytest.mark.criterion("C7")
def test_every_tool_loop_turn_carries_cache_control(kit: SimpleNamespace) -> None:
    # oracle: Requirement 9 — "every request carries" cache_control: both turns of a tool round trip.
    fake = kit.FakeAnthropicClient([
        kit.make_message([kit.tool_use_block("toolu_1", "arag__search", {"q": "x"})], stop_reason="tool_use"),
        kit.make_message([kit.text_block("done")]),
    ])
    backend = AnthropicChatBackend(client=fake, models=kit.MODELS)
    first = backend.create(model=kit.SONNET, messages=_MSG, tools=_TOOLS, tool_choice="auto")
    assert first.assistant_message is not None
    backend.create(model=kit.SONNET, tools=_TOOLS, tool_choice="auto", messages=[
        *_MSG, first.assistant_message, {"role": "tool", "tool_call_id": "toolu_1", "content": "r"}])
    assert [call["cache_control"] for call in fake.calls] == [{"type": "ephemeral"}, {"type": "ephemeral"}]


def _gateway_cost(kit: SimpleNamespace, *, input_tokens: int, cache_creation: int, cache_read: int,
                  output_tokens: int) -> Any:
    """One gateway call (Notes pricing) whose final message reports the given usage."""
    fake = kit.FakeAnthropicClient([kit.make_message([kit.text_block("ok")], input_tokens=input_tokens,
                                                     output_tokens=output_tokens, cache_read=cache_read,
                                                     cache_creation=cache_creation)])
    gw = kit.gateway(AnthropicChatBackend(client=fake, models=kit.MODELS))
    return gw.complete(messages=_MSG, model=kit.SONNET, stage="spec")


@pytest.mark.criterion("C7")
def test_cache_write_turn_cost(kit: SimpleNamespace) -> None:
    # oracle: Requirement 9 with the Notes pricing and live counts (4, 16494 written, 0 read, 4 out):
    # (4 x 0.002 + 16494 x 0.0025 + 4 x 0.01) / 1000 = (0.008 + 41.235 + 0.04) / 1000 = 0.041283 USD.
    result = _gateway_cost(kit, input_tokens=4, cache_creation=16494, cache_read=0, output_tokens=4)
    assert (result.prompt_tokens, result.cached_prompt_tokens, result.cache_write_tokens) == (16498, 0, 16494)
    # rel 1e-9: only float rounding in a four-term sum separates the gateway's figure from the hand one.
    assert result.cost_usd == pytest.approx(0.041283, rel=1e-9)


@pytest.mark.criterion("C7")
def test_cache_read_turn_cost(kit: SimpleNamespace) -> None:
    # oracle: Requirement 9 with the Notes pricing and live counts (4, 0 written, 16494 read, 4 out):
    # (4 x 0.002 + 16494 x 0.0002 + 4 x 0.01) / 1000 = (0.008 + 3.2988 + 0.04) / 1000 = 0.0033468 USD.
    result = _gateway_cost(kit, input_tokens=4, cache_creation=0, cache_read=16494, output_tokens=4)
    assert (result.prompt_tokens, result.cached_prompt_tokens, result.cache_write_tokens) == (16498, 16494, 0)
    # rel 1e-9: only float rounding in a four-term sum separates the gateway's figure from the hand one.
    assert result.cost_usd == pytest.approx(0.0033468, rel=1e-9)


# ------------------------------------------------------------------------------------------ property
@pytest.mark.criterion("C7")
@given(
    input_tokens=st.integers(0, 200_000),
    cache_creation=st.integers(0, 200_000),
    cache_read=st.integers(0, 200_000),
    output_tokens=st.integers(1, 64_000),
)
def test_gateway_cost_property(kit: SimpleNamespace, input_tokens: int, cache_creation: int, cache_read: int,
                               output_tokens: int) -> None:
    # oracle: Requirement 9 — uncached input at "input", writes at "cache_write", reads at "cached_input",
    # output at "output" (Notes pricing, USD per 1,000 tokens); the formula is written out here.
    expected = (input_tokens * 0.002 + cache_creation * 0.0025 + cache_read * 0.0002 + output_tokens * 0.01) / 1000
    result = _gateway_cost(kit, input_tokens=input_tokens, cache_creation=cache_creation, cache_read=cache_read,
                           output_tokens=output_tokens)
    assert (result.cached_prompt_tokens, result.cache_write_tokens) == (cache_read, cache_creation)
    # rel 1e-9: float rounding in the sums only; output_tokens >= 1 keeps the expected cost above zero.
    assert result.cost_usd == pytest.approx(expected, rel=1e-9)


_TEXT = st.text(alphabet="abc xyz.", min_size=1, max_size=10)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@given(
    model=st.sampled_from(["claude-sonnet-5-5", "claude-haiku-4-5"]),
    turns=st.lists(st.tuples(st.sampled_from(["system", "user"]), _TEXT), min_size=1, max_size=4),
    with_tools=st.booleans(),
    tool_choice=st.sampled_from(["auto", "none", None]),
    max_completion_tokens=st.one_of(st.none(), st.integers(1, 100_000)),
)
def test_caching_on_and_off_differ_only_by_cache_control(kit: SimpleNamespace, model: str,
                                                         turns: list[tuple[str, str]], with_tools: bool,
                                                         tool_choice: str | None,
                                                         max_completion_tokens: int | None) -> None:
    # oracle: Requirements 3 and 9 — turning prompt_caching off removes exactly the cache_control key;
    # the default and True both send {"type": "ephemeral"}.
    kwargs: dict[str, Any] = {"model": model, "messages": [{"role": r, "content": t} for r, t in turns],
                              "tools": _TOOLS if with_tools else None, "tool_choice": tool_choice,
                              "max_completion_tokens": max_completion_tokens}
    default = _request(kit, "default", **kwargs)
    on = _request(kit, True, **kwargs)
    off = _request(kit, False, **kwargs)
    assert default == on
    assert on["cache_control"] == _EPHEMERAL
    assert "cache_control" not in off
    assert {k: v for k, v in on.items() if k != "cache_control"} == off
