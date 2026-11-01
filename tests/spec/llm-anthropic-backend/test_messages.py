"""C2: OpenAI-shaped messages become Anthropic messages (spec Requirement 4)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend


def _sent_messages(kit: SimpleNamespace, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calls `create` (Haiku, no system message) once; returns the `messages` the client received."""
    fake = kit.FakeAnthropicClient([kit.make_message([kit.text_block("ok")], model=kit.HAIKU)])
    AnthropicChatBackend(client=fake, models=kit.MODELS).create(model=kit.HAIKU, messages=messages,
                                                                max_completion_tokens=100)
    assert len(fake.calls) == 1
    sent: list[dict[str, Any]] = fake.calls[0]["messages"]
    return sent


def _call(id_: str, name: str, arguments: str) -> dict[str, Any]:
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.mark.criterion("C2")
def test_user_messages_keep_their_content(kit: SimpleNamespace) -> None:
    # oracle: Requirement 4 — a user message keeps its content; a plain assistant reply becomes one
    # text block.
    sent = _sent_messages(kit, [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ])
    assert sent == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
        {"role": "user", "content": "second"},
    ]


@pytest.mark.criterion("C2")
def test_one_tool_result(kit: SimpleNamespace) -> None:
    # oracle: Requirement 4 — assistant without text: only a tool_use block; the tool message becomes a
    # user message holding one tool_result block.
    sent = _sent_messages(kit, [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [_call("c1", "arag__search", '{"q": "taiwan"}')]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"hits": 3}'},
    ])
    assert sent == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "c1", "name": "arag__search", "input": {"q": "taiwan"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": '{"hits": 3}'}]},
    ]


@pytest.mark.criterion("C2")
def test_three_tool_results_are_merged(kit: SimpleNamespace) -> None:
    # oracle: Requirement 4 — text block first, then one tool_use per call; three consecutive tool
    # messages become one user message with their blocks in order. A later user message stays separate.
    sent = _sent_messages(kit, [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "checking", "tool_calls": [
            _call("a", "t0", "{}"), _call("b", "t1", '{"n": 2}'), _call("c", "t2", '{"x": [1, 2]}')]},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
        {"role": "tool", "tool_call_id": "c", "content": "rc"},
        {"role": "user", "content": "go on"},
    ])
    assert sent == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "a", "name": "t0", "input": {}},
            {"type": "tool_use", "id": "b", "name": "t1", "input": {"n": 2}},
            {"type": "tool_use", "id": "c", "name": "t2", "input": {"x": [1, 2]}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "ra"},
            {"type": "tool_result", "tool_use_id": "b", "content": "rb"},
            {"type": "tool_result", "tool_use_id": "c", "content": "rc"},
        ]},
        {"role": "user", "content": "go on"},
    ]


_NATIVE_BLOCKS = [
    {"type": "thinking", "thinking": "", "signature": "EqQBCkgIBxABGAIiQL" + "x" * 1200},
    {"type": "text", "text": "Looking."},
    {"type": "tool_use", "id": "toolu_01", "name": "arag__search", "input": {"q": "taiwan"}},
]


@pytest.mark.criterion("C2")
def test_anthropic_native_is_sent_unchanged(kit: SimpleNamespace) -> None:
    # oracle: Requirement 4 — `_native` with provider anthropic: the assistant message is exactly
    # {"role": "assistant", "content": <its list>}; content, tool_calls and reasoning_content are ignored.
    sent = _sent_messages(kit, [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "Looking.", "reasoning_content": "r",
         "tool_calls": [_call("toolu_01", "arag__search", '{"q": "taiwan"}')],
         "_native": {"provider": "anthropic", "content": _NATIVE_BLOCKS}},
        {"role": "tool", "tool_call_id": "toolu_01", "content": "R"},
    ])
    assert sent == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "EqQBCkgIBxABGAIiQL" + "x" * 1200},
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "toolu_01", "name": "arag__search", "input": {"q": "taiwan"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": "R"}]},
    ]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("arguments", ["not json", "[1, 2]", '"text"', "3", "", "null", '{"a": '])
def test_non_object_arguments_give_empty_input(kit: SimpleNamespace, arguments: str) -> None:
    # oracle: Requirement 4 — arguments that are not a JSON object give input {}; empty content gives no
    # text block.
    sent = _sent_messages(kit, [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [_call("c9", "t", arguments)]},
        {"role": "tool", "tool_call_id": "c9", "content": "R"},
    ])
    assert sent[1] == {"role": "assistant", "content": [{"type": "tool_use", "id": "c9", "name": "t", "input": {}}]}


@pytest.mark.criterion("C2")
def test_other_provider_native_is_ignored(kit: SimpleNamespace) -> None:
    # oracle: Requirement 4 — a `_native` from another provider is not used: the turn is rebuilt from
    # content + tool_calls, and reasoning_content is dropped.
    sent = _sent_messages(kit, [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "hm", "reasoning_content": "deep thoughts",
         "tool_calls": [_call("c1", "t", '{"k": "v"}')],
         "_native": {"provider": "deepseek", "content": [{"type": "thinking", "thinking": "x", "signature": "s"}]}},
        {"role": "tool", "tool_call_id": "c1", "content": "R"},
    ])
    assert sent[1] == {"role": "assistant", "content": [
        {"type": "text", "text": "hm"},
        {"type": "tool_use", "id": "c1", "name": "t", "input": {"k": "v"}},
    ]}


# ------------------------------------------------------------------------------------------ property
_TEXT = st.text(alphabet="abc xyz", max_size=8)
_ID = st.text(alphabet="abcdef0123456789", min_size=1, max_size=6)
_JSON_OBJ = st.dictionaries(st.text(alphabet="pqr", min_size=1, max_size=3),
                            st.one_of(st.integers(-9, 9), st.text(alphabet="uv", max_size=3)), max_size=3)
_ARGS = st.one_of(_JSON_OBJ.map(lambda d: ("obj", d)),
                  st.sampled_from(["oops", "[1]", "7", "", "null"]).map(lambda s: ("bad", s)))
_NATIVE = st.lists(
    st.one_of(
        st.builds(lambda t, s: {"type": "thinking", "thinking": t, "signature": s}, _TEXT, _ID),
        st.builds(lambda t: {"type": "text", "text": t}, _TEXT),
        st.builds(lambda i, n, d: {"type": "tool_use", "id": i, "name": n, "input": d}, _ID, _ID, _JSON_OBJ),
    ),
    min_size=1, max_size=4,
)
_TURN = st.one_of(
    st.tuples(st.just("user"), _TEXT),
    st.tuples(st.just("assistant"), st.one_of(st.none(), _TEXT),
              st.lists(st.tuples(_ID, _ID, _ARGS), max_size=3),
              st.one_of(st.none(), st.tuples(st.sampled_from(["anthropic", "deepseek", "openai"]), _NATIVE))),
)


@pytest.mark.criterion("C2")
@given(turns=st.lists(_TURN, min_size=1, max_size=6))
def test_message_translation_property(kit: SimpleNamespace, turns: list[tuple[Any, ...]]) -> None:
    # oracle: Requirement 4 — input and expected output are built side by side here from the rules: user
    # unchanged; anthropic `_native` sent as is; other assistants rebuilt (text block if non-empty, then
    # tool_use blocks, non-object arguments -> {}); each run of tool messages -> one user message.
    messages: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    for turn in turns:
        if turn[0] == "user":
            messages.append({"role": "user", "content": turn[1]})
            expected.append({"role": "user", "content": turn[1]})
            continue
        _, content, calls, native = turn
        msg: dict[str, Any] = {"role": "assistant", "content": content, "reasoning_content": "dropped"}
        raw_calls = []
        rebuilt: list[dict[str, Any]] = [{"type": "text", "text": content}] if content else []
        for id_, name, (kind, value) in calls:
            arguments = json.dumps(value) if kind == "obj" else value
            raw_calls.append(_call(id_, name, arguments))
            rebuilt.append({"type": "tool_use", "id": id_, "name": name, "input": value if kind == "obj" else {}})
        if raw_calls:
            msg["tool_calls"] = raw_calls
        if native is not None:
            msg["_native"] = {"provider": native[0], "content": native[1]}
        if not rebuilt and not (native is not None and native[0] == "anthropic"):
            continue  # an assistant turn with nothing to send is outside the spec's cases
        messages.append(msg)
        if native is not None and native[0] == "anthropic":
            expected.append({"role": "assistant", "content": native[1]})
        else:
            expected.append({"role": "assistant", "content": rebuilt})
        if calls:
            for id_, _name, _args in calls:
                messages.append({"role": "tool", "tool_call_id": id_, "content": f"result-{id_}"})
            expected.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": id_, "content": f"result-{id_}"} for id_, _n, _a in calls]})
    if not messages:
        messages = [{"role": "user", "content": "q"}]
        expected = [{"role": "user", "content": "q"}]
    assert _sent_messages(kit, messages) == expected


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_native_overrides_rebuild(kit: SimpleNamespace) -> None:
    # oracle: Requirement 4 — adding an anthropic `_native` to an assistant turn replaces only that
    # message's content with the native list; adding a non-anthropic `_native` changes nothing.
    turn: dict[str, Any] = {"role": "assistant", "content": "x", "tool_calls": [_call("c1", "t", '{"a": 1}')]}
    tail = [{"role": "tool", "tool_call_id": "c1", "content": "R"}, {"role": "user", "content": "next"}]
    head = [{"role": "user", "content": "q"}]
    plain = _sent_messages(kit, [*head, turn, *tail])
    other = _sent_messages(kit, [*head, {**turn, "_native": {"provider": "openai", "content": _NATIVE_BLOCKS}},
                                 *tail])
    native = _sent_messages(kit, [*head, {**turn, "_native": {"provider": "anthropic", "content": _NATIVE_BLOCKS}},
                                  *tail])
    assert other == plain
    assert native[:1] == plain[:1]
    assert native[2:] == plain[2:]
    assert native[1] == {"role": "assistant", "content": _NATIVE_BLOCKS}
    assert plain[1] != native[1]
