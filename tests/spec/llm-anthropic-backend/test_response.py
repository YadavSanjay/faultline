"""C4: the streamed final message becomes a `RawCompletion` (spec Requirement 6)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend
from src.scenariogen.llm.gateway import RawCompletion, ToolCall

_MSG = [{"role": "user", "content": "q"}]


def _translate(kit: SimpleNamespace, message: Any) -> RawCompletion:
    """Calls `create` (Sonnet) over a fake client returning `message`."""
    fake = kit.FakeAnthropicClient([message])
    result: RawCompletion = AnthropicChatBackend(client=fake, models=kit.MODELS).create(model=kit.SONNET, messages=_MSG)
    assert len(fake.calls) == 1
    return result


@pytest.mark.criterion("C4")
def test_text_only(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 — every field written by hand: end_turn -> stop, no cache fields -> 0,
    # no tool calls -> assistant_message None.
    msg = kit.make_message([kit.text_block("Hello there")], stop_reason="end_turn", input_tokens=12, output_tokens=7)
    assert _translate(kit, msg) == RawCompletion(
        content="Hello there", prompt_tokens=12, completion_tokens=7, finish_reason="stop", tool_calls=(),
        assistant_message=None, cached_prompt_tokens=0, cache_write_tokens=0, served_model="claude-sonnet-5-5",
    )


@pytest.mark.criterion("C4")
def test_text_with_two_tool_uses(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 — one ToolCall per tool_use with json.dumps(input); the assistant message
    # has the OpenAI tool_calls and the blocks' model_dump(exclude_none=True) (text's citations=None dropped).
    msg = kit.make_message([
        kit.text_block("Let me check."),
        kit.tool_use_block("toolu_1", "arag__search", {"q": "taiwan"}),
        kit.tool_use_block("toolu_2", "macro__view", {}),
    ], stop_reason="tool_use", input_tokens=500, output_tokens=60)
    assert _translate(kit, msg) == RawCompletion(
        content="Let me check.",
        prompt_tokens=500,
        completion_tokens=60,
        finish_reason="tool_calls",
        tool_calls=(ToolCall("toolu_1", "arag__search", '{"q": "taiwan"}'), ToolCall("toolu_2", "macro__view", "{}")),
        assistant_message={
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [
                {"id": "toolu_1", "type": "function", "function": {"name": "arag__search",
                                                                   "arguments": '{"q": "taiwan"}'}},
                {"id": "toolu_2", "type": "function", "function": {"name": "macro__view", "arguments": "{}"}},
            ],
            "_native": {"provider": "anthropic", "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_1", "name": "arag__search", "input": {"q": "taiwan"}},
                {"type": "tool_use", "id": "toolu_2", "name": "macro__view", "input": {}},
            ]},
        },
        cached_prompt_tokens=0,
        cache_write_tokens=0,
        served_model="claude-sonnet-5-5",
    )


@pytest.mark.criterion("C4")
def test_text_blocks_are_joined_without_separator(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 — "its text blocks joined in order, with no separator":
    # "Part one." + "Part two." + " Done." = "Part one.Part two. Done.", skipping the tool_use between.
    msg = kit.make_message([
        kit.text_block("Part one."),
        kit.text_block("Part two."),
        kit.tool_use_block("toolu_3", "arag__search", {"q": "x"}),
        kit.text_block(" Done."),
    ], stop_reason="tool_use", input_tokens=20, output_tokens=15)
    assert _translate(kit, msg) == RawCompletion(
        content="Part one.Part two. Done.",
        prompt_tokens=20,
        completion_tokens=15,
        finish_reason="tool_calls",
        tool_calls=(ToolCall("toolu_3", "arag__search", '{"q": "x"}'),),
        assistant_message={
            "role": "assistant",
            "content": "Part one.Part two. Done.",
            "tool_calls": [{"id": "toolu_3", "type": "function",
                            "function": {"name": "arag__search", "arguments": '{"q": "x"}'}}],
            "_native": {"provider": "anthropic", "content": [
                {"type": "text", "text": "Part one."},
                {"type": "text", "text": "Part two."},
                {"type": "tool_use", "id": "toolu_3", "name": "arag__search", "input": {"q": "x"}},
                {"type": "text", "text": " Done."},
            ]},
        },
        served_model="claude-sonnet-5-5",
    )


@pytest.mark.criterion("C4")
def test_text_only_two_blocks(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 — "ab" + "cd" joined with no separator is "abcd"; no tool calls -> None.
    msg = kit.make_message([kit.text_block("ab"), kit.text_block("cd")], input_tokens=5, output_tokens=4)
    assert _translate(kit, msg) == RawCompletion(content="abcd", prompt_tokens=5, completion_tokens=4,
                                                 finish_reason="stop", served_model="claude-sonnet-5-5")


@pytest.mark.criterion("C4")
def test_thinking_with_tool_use(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 — no text blocks: content "" and assistant content None; the thinking block
    # (empty thinking text, as in the live Haiku evidence) is kept in _native with its signature.
    sig = "S" * 1200
    msg = kit.make_message([
        kit.thinking_block("", sig),
        kit.tool_use_block("toolu_9", "arag__search", {"q": "chips", "k": 5}),
    ], stop_reason="tool_use", input_tokens=40, output_tokens=30)
    assert _translate(kit, msg) == RawCompletion(
        content="",
        prompt_tokens=40,
        completion_tokens=30,
        finish_reason="tool_calls",
        tool_calls=(ToolCall("toolu_9", "arag__search", '{"q": "chips", "k": 5}'),),
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "toolu_9", "type": "function",
                            "function": {"name": "arag__search", "arguments": '{"q": "chips", "k": 5}'}}],
            "_native": {"provider": "anthropic", "content": [
                {"type": "thinking", "thinking": "", "signature": sig},
                {"type": "tool_use", "id": "toolu_9", "name": "arag__search", "input": {"q": "chips", "k": 5}},
            ]},
        },
        served_model="claude-sonnet-5-5",
    )


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("stop_reason", "finish_reason"), [
    ("end_turn", "stop"),
    ("stop_sequence", "stop"),
    ("max_tokens", "length"),
    ("tool_use", "tool_calls"),
    ("refusal", "refusal"),
    ("pause_turn", "pause_turn"),
    ("model_context_window_exceeded", "model_context_window_exceeded"),
])
def test_stop_reason_mapping(kit: SimpleNamespace, stop_reason: str, finish_reason: str) -> None:
    # oracle: Requirement 6 — the written mapping; unmapped values pass through unchanged.
    msg = kit.make_message([kit.text_block("t")], stop_reason=stop_reason, input_tokens=3, output_tokens=2,
                           category="cyber" if stop_reason == "refusal" else None)
    assert _translate(kit, msg) == RawCompletion(content="t", prompt_tokens=3, completion_tokens=2,
                                                 finish_reason=finish_reason, served_model="claude-sonnet-5-5")


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(("cache_read", "cache_creation", "prompt", "cached", "written"), [
    (300, 50, 100 + 300 + 50, 300, 50),
    (0, 0, 100, 0, 0),
    (None, None, 100, 0, 0),
    ("absent", "absent", 100, 0, 0),
    (1200, "absent", 100 + 1200, 1200, 0),
    (None, 75, 100 + 75, 0, 75),
])
def test_usage_with_and_without_cache_fields(kit: SimpleNamespace, cache_read: Any, cache_creation: Any,
                                             prompt: int, cached: int, written: int) -> None:
    # oracle: Requirement 6 — prompt = input + cache_read + cache_creation (missing/None -> 0), with
    # input_tokens=100 and output_tokens=20; the expected sums are written out in the parameters.
    msg = kit.make_message([kit.text_block("t")], input_tokens=100, output_tokens=20,
                           cache_read=kit.ABSENT if cache_read == "absent" else cache_read,
                           cache_creation=kit.ABSENT if cache_creation == "absent" else cache_creation)
    assert _translate(kit, msg) == RawCompletion(content="t", prompt_tokens=prompt, completion_tokens=20,
                                                 finish_reason="stop", cached_prompt_tokens=cached,
                                                 cache_write_tokens=written, served_model="claude-sonnet-5-5")


# ------------------------------------------------------------------------------------------ property
_STOP = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length", "tool_use": "tool_calls",
         "refusal": "refusal"}
_ID = st.text(alphabet="abcdef0123456789_", min_size=1, max_size=8)
_INPUT = st.dictionaries(st.text(alphabet="pqrs", min_size=1, max_size=3),
                         st.one_of(st.integers(-99, 99), st.text(alphabet="xyz", max_size=3), st.booleans()),
                         max_size=3)
_CACHE = st.one_of(st.just("absent"), st.none(), st.integers(0, 50_000))


def _n(value: Any) -> int:
    return 0 if value in ("absent", None) else int(value)


@pytest.mark.criterion("C4")
@given(
    texts=st.lists(st.text(alphabet="abc .", min_size=1, max_size=10), max_size=3),
    thinking=st.one_of(st.none(), st.tuples(st.text(alphabet="th ", max_size=6), _ID)),
    uses=st.lists(st.tuples(_ID, _ID, _INPUT), max_size=3),
    stop_reason=st.sampled_from([*_STOP, "pause_turn", "something_new"]),
    input_tokens=st.integers(0, 100_000),
    output_tokens=st.integers(0, 64_000),
    cache_read=_CACHE,
    cache_creation=_CACHE,
)
def test_response_property(kit: SimpleNamespace, texts: list[str], thinking: tuple[str, str] | None,
                           uses: list[tuple[str, str, dict[str, Any]]], stop_reason: str, input_tokens: int,
                           output_tokens: int, cache_read: Any, cache_creation: Any) -> None:
    # oracle: Requirement 6 — the expected RawCompletion is built here field by field from the rules
    # (0-3 text blocks, joined with no separator; assistant content None when there are none).
    blocks: list[Any] = []
    native: list[dict[str, Any]] = []
    if thinking is not None:
        blocks.append(kit.thinking_block(*thinking))
        native.append({"type": "thinking", "thinking": thinking[0], "signature": thinking[1]})
    for text in texts:
        blocks.append(kit.text_block(text))
        native.append({"type": "text", "text": text})
    joined = "".join(texts)
    for id_, name, input_ in uses:
        blocks.append(kit.tool_use_block(id_, name, input_))
        native.append({"type": "tool_use", "id": id_, "name": name, "input": input_})
    msg = kit.make_message(blocks, stop_reason=stop_reason, input_tokens=input_tokens, output_tokens=output_tokens,
                           cache_read=kit.ABSENT if cache_read == "absent" else cache_read,
                           cache_creation=kit.ABSENT if cache_creation == "absent" else cache_creation,
                           category="other" if stop_reason == "refusal" else None)
    calls = tuple(ToolCall(id_, name, json.dumps(input_)) for id_, name, input_ in uses)
    assistant = None
    if calls:
        assistant = {
            "role": "assistant",
            "content": joined if texts else None,
            "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                           for c in calls],
            "_native": {"provider": "anthropic", "content": native},
        }
    expected = RawCompletion(
        content=joined,
        prompt_tokens=input_tokens + _n(cache_read) + _n(cache_creation),
        completion_tokens=output_tokens,
        finish_reason=_STOP.get(stop_reason, stop_reason),
        tool_calls=calls,
        assistant_message=assistant,
        cached_prompt_tokens=_n(cache_read),
        cache_write_tokens=_n(cache_creation),
        served_model="claude-sonnet-5-5",
    )
    assert _translate(kit, msg) == expected


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_cache_fields_move_tokens_into_prompt(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 — adding cache_read=r and cache_creation=w raises prompt_tokens by exactly
    # r + w, sets cached/written to r and w, and changes no other field.
    base = _translate(kit, kit.make_message([kit.text_block("t")], input_tokens=900, output_tokens=40))
    cached = _translate(kit, kit.make_message([kit.text_block("t")], input_tokens=900, output_tokens=40,
                                              cache_read=4000, cache_creation=250))
    assert cached.prompt_tokens == base.prompt_tokens + 4250
    assert (cached.cached_prompt_tokens, cached.cache_write_tokens) == (4000, 250)
    assert (base.cached_prompt_tokens, base.cache_write_tokens) == (0, 0)
    assert (cached.content, cached.completion_tokens, cached.finish_reason, cached.served_model) == \
        (base.content, base.completion_tokens, base.finish_reason, base.served_model)
    assert base.prompt_tokens == 900
