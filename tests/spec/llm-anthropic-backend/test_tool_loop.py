"""C6: `run_tool_loop` over `InstrumentedChatClient` over `AnthropicChatBackend` (spec Requirements 4, 6, 8)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend
from src.scenariogen.llm.gateway import FatalChatError
from src.scenariogen.llm.mcp.loop import run_tool_loop


def _loop(kit: SimpleNamespace, first: list[Any], final_text: str) -> tuple[Any, Any]:
    """A tool_use turn with `first` blocks, then a text answer; returns (loop result, fake client)."""
    fake = kit.FakeAnthropicClient([
        kit.make_message(first, stop_reason="tool_use", input_tokens=1000, output_tokens=200),
        kit.make_message([kit.text_block(final_text)], stop_reason="end_turn", input_tokens=1500, output_tokens=80),
    ])
    gw = kit.gateway(AnthropicChatBackend(client=fake, models=kit.MODELS))
    result = run_tool_loop(gateway=gw, registry=kit.make_registry(3), system="sys", user="usr", model=kit.SONNET,
                           stage="spec", max_tool_calls=8)
    return result, fake


@pytest.mark.criterion("C6")
def test_thinking_and_tool_use_are_passed_back(kit: SimpleNamespace) -> None:
    # oracle: Requirements 4, 6, 8 — the second request ends with exactly the returned blocks as the
    # assistant message, then one user message with the matching tool_result (the fake tool's output).
    result, fake = _loop(kit, [kit.thinking_block("plan the lookup", "sig-1"),
                               kit.tool_use_block("toolu_1", "arag__tool0", {"q": "taiwan"})], "final answer")
    assert result.content == "final answer"
    assert len(fake.calls) == 2
    second = fake.calls[1]
    assert second["system"] == "sys"
    assert second["messages"] == [
        {"role": "user", "content": "usr"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "plan the lookup", "signature": "sig-1"},
            {"type": "tool_use", "id": "toolu_1", "name": "arag__tool0", "input": {"q": "taiwan"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": kit.TOOL_RESULT}]},
    ]


@pytest.mark.criterion("C6")
def test_refusal_is_fatal_after_one_call(kit: SimpleNamespace) -> None:
    # oracle: Requirements 6 and 8 (D6) — refusal -> finish_reason "refusal" -> FatalChatError, no retry.
    fake = kit.FakeAnthropicClient([
        kit.make_message([kit.text_block("I can't help with that.")], stop_reason="refusal", category="cyber"),
        kit.make_message([kit.text_block("ok")]),
    ])
    gw = kit.gateway(AnthropicChatBackend(client=fake, models=kit.MODELS))
    with pytest.raises(FatalChatError):
        gw.complete(messages=[{"role": "user", "content": "q"}], model=kit.SONNET, stage="spec")
    assert len(fake.calls) == 1


_ID = st.text(alphabet="abcdef0123456789", min_size=1, max_size=6)
_INPUT = st.dictionaries(st.sampled_from(["q", "k", "as_of"]), st.text(alphabet="xyz", max_size=4), max_size=2)


@pytest.mark.criterion("C6")
@given(
    thinking=st.text(alphabet="abc ", max_size=10),
    signature=st.text(alphabet="ABCdef0123=", min_size=1, max_size=40),
    uses=st.lists(st.tuples(_ID, st.integers(0, 2), _INPUT), min_size=1, max_size=3, unique_by=lambda u: u[0]),
    final_text=st.text(alphabet="abc .", min_size=1, max_size=10),
)
def test_round_trip_property(kit: SimpleNamespace, thinking: str, signature: str,
                             uses: list[tuple[str, int, dict[str, Any]]], final_text: str) -> None:
    # oracle: Requirements 4, 6, 8 — for any thinking block plus 1-3 tool uses, the second request's
    # messages are [user, assistant(<those block dicts>), user(<one tool_result per use, in order>)],
    # and the loop answers with the second turn's text.
    blocks = [kit.thinking_block(thinking, signature)]
    expected_blocks: list[dict[str, Any]] = [{"type": "thinking", "thinking": thinking, "signature": signature}]
    for id_, tool, input_ in uses:
        blocks.append(kit.tool_use_block(id_, f"arag__tool{tool}", input_))
        expected_blocks.append({"type": "tool_use", "id": id_, "name": f"arag__tool{tool}", "input": input_})
    result, fake = _loop(kit, blocks, final_text)
    assert result.content == final_text
    assert result.n_tool_calls == len(uses)
    assert len(fake.calls) == 2
    assert fake.calls[1]["messages"] == [
        {"role": "user", "content": "usr"},
        {"role": "assistant", "content": expected_blocks},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": id_, "content": kit.TOOL_RESULT}
                                     for id_, _t, _i in uses]},
    ]


@pytest.mark.criterion("C6")
@given(max_attempts=st.integers(1, 5), category=st.sampled_from(["cyber", "bio", "other"]))
def test_refusal_property(kit: SimpleNamespace, max_attempts: int, category: str) -> None:
    # oracle: Requirement 8 (D6) — whatever the attempt limit, a refusal raises FatalChatError after
    # exactly one call.
    fake = kit.FakeAnthropicClient([
        kit.make_message([kit.text_block("no")], stop_reason="refusal", category=category),
        kit.make_message([kit.text_block("ok")]),
    ])
    gw = kit.gateway(AnthropicChatBackend(client=fake, models=kit.MODELS), max_attempts=max_attempts)
    with pytest.raises(FatalChatError):
        gw.complete(messages=[{"role": "user", "content": "q"}], model=kit.SONNET, stage="spec")
    assert len(fake.calls) == 1
