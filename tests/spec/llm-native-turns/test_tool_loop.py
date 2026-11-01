"""C2 and C4: `run_tool_loop` over the real gateway and `OpenAIChatBackend` with a fake SDK client.

C2: the assistant turn is passed back as returned, including `reasoning_content` (Requirement 2).
C4: tools stay on the request after the budget is used up, with `tool_choice="none"` (Requirement 4).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.mcp.loop import run_tool_loop

_TEXT = st.text(alphabet="abcxyz 0123456789{}\":,", max_size=16)
_PARAMS = {"type": "object", "properties": {"q": {"type": "string"}}}


def _run_two_turns(kit: SimpleNamespace, content: str, calls: list[tuple[str, str, str]], reasoning: Any
                   ) -> list[dict[str, Any]]:
    """One tool-call response then a final answer; returns the messages of the second request."""
    fake = kit.FakeSDKClient([
        kit.make_response(content=content, finish_reason="tool_calls", tool_calls=calls,
                          reasoning_content=reasoning),
        kit.make_response(content="final answer"),
    ])
    result = run_tool_loop(gateway=kit.openai_gateway(fake), registry=kit.make_registry(2), system="sys",
                           user="usr", model=kit.MODEL, stage="spec", max_tool_calls=8)
    assert result.content == "final answer"
    assert len(fake.calls) == 2
    messages: list[dict[str, Any]] = fake.calls[1]["messages"]
    return messages


# ----------------------------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_reasoning_content_is_passed_back(kit: SimpleNamespace) -> None:
    # oracle: Requirement 2 — the dict below is written out by hand from the scripted response.
    calls = [("call_1", "arag__tool0", '{"q": "taiwan"}')]
    messages = _run_two_turns(kit, "", calls, "think step by step")
    assert messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "arag__tool0", "arguments": '{"q": "taiwan"}'}}
        ],
        "reasoning_content": "think step by step",
    }
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": kit.TOOL_RESULT}


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_reasoning_adds_exactly_one_key(kit: SimpleNamespace) -> None:
    # oracle: Requirement 2 — with reasoning_content the request is today's request plus that one key;
    # with it absent or None the request is identical to today's (hand-built dict).
    calls = [("call_a", "arag__tool0", "{}"), ("call_b", "arag__tool1", '{"q": "x"}')]
    today = {
        "role": "assistant",
        "content": "looking it up",
        "tool_calls": [
            {"id": "call_a", "type": "function", "function": {"name": "arag__tool0", "arguments": "{}"}},
            {"id": "call_b", "type": "function", "function": {"name": "arag__tool1", "arguments": '{"q": "x"}'}},
        ],
    }
    absent = _run_two_turns(kit, "looking it up", calls, kit.ABSENT)
    none = _run_two_turns(kit, "looking it up", calls, None)
    with_reasoning = _run_two_turns(kit, "looking it up", calls, "chain")
    assert with_reasoning[2] == {**today, "reasoning_content": "chain"}
    assert absent[2] == today
    assert none[2] == today
    assert with_reasoning[:2] == absent[:2]
    assert with_reasoning[3:] == absent[3:]


@pytest.mark.criterion("C2")
@given(
    content=_TEXT,
    reasoning_kind=st.sampled_from(["absent", "none", "text"]),
    reasoning_text=_TEXT,
    calls=st.lists(st.tuples(st.sampled_from(["arag__tool0", "arag__tool1"]), _TEXT), min_size=1, max_size=3),
)
def test_assistant_turn_property(kit: SimpleNamespace, content: str, reasoning_kind: str, reasoning_text: str,
                                 calls: list[tuple[str, str]]) -> None:
    # oracle: Requirement 2 — the expected assistant dict is built here by hand from the scripted fields.
    reasoning: Any = {"absent": kit.ABSENT, "none": None, "text": reasoning_text}[reasoning_kind]
    scripted = [(f"call_{i}", name, args) for i, (name, args) in enumerate(calls)]
    expected: dict[str, Any] = {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in scripted
        ],
    }
    if reasoning_kind == "text":
        expected["reasoning_content"] = reasoning_text
    messages = _run_two_turns(kit, content, scripted, reasoning)
    assert messages[2] == expected
    assert ("reasoning_content" in messages[2]) == (reasoning_kind == "text")


# ----------------------------------------------------------------------------------------------- C4
def _hand_built_tools(n_tools: int) -> list[dict[str, Any]]:
    """The tool list the registry offers for `make_registry(n_tools)`, written out from its listings."""
    return [
        {"type": "function", "function": {"name": f"arag__tool{i}", "description": f"fixed tool {i}",
                                          "parameters": _PARAMS}}
        for i in range(n_tools)
    ]


def _run_greedy_model(kit: SimpleNamespace, n_tools: int, max_tool_calls: int) -> list[dict[str, Any]]:
    """A model that asks for one tool on every turn; returns every request the SDK received."""

    def responder(index: int, kwargs: dict[str, Any]) -> Any:
        return kit.make_response(content="", finish_reason="tool_calls",
                                 tool_calls=[(f"call_{index}", "arag__tool0", '{"q": "more"}')])

    fake = kit.FakeSDKClient(responder=responder)
    run_tool_loop(gateway=kit.openai_gateway(fake), registry=kit.make_registry(n_tools), system="sys",
                  user="usr", model=kit.MODEL, stage="spec", max_tool_calls=max_tool_calls)
    requests: list[dict[str, Any]] = fake.calls
    return requests


@pytest.mark.criterion("C4")
def test_tools_stay_on_last_turn_with_tool_choice_none(kit: SimpleNamespace) -> None:
    # oracle: Requirement 4 — with max_tool_calls=2 the first two requests carry the tools and no
    # tool_choice; the third (after the budget is used up) carries the same tools and tool_choice="none".
    requests = _run_greedy_model(kit, n_tools=3, max_tool_calls=2)
    tools = _hand_built_tools(3)
    assert len(requests) >= 3
    for req in requests[:2]:
        assert req["tools"] == tools
        assert "tool_choice" not in req
    assert requests[2].get("tools") == tools
    assert requests[2].get("tool_choice") == "none"


@pytest.mark.criterion("C4")
@given(n_tools=st.integers(min_value=0, max_value=3), max_tool_calls=st.integers(min_value=1, max_value=4))
def test_tool_offer_property(kit: SimpleNamespace, n_tools: int, max_tool_calls: int) -> None:
    # oracle: Requirement 4 — before the budget: tools, no tool_choice; the request after it: the same
    # tools with tool_choice="none"; no tools offered: neither key on any request.
    requests = _run_greedy_model(kit, n_tools=n_tools, max_tool_calls=max_tool_calls)
    assert len(requests) >= max_tool_calls + 1
    if n_tools == 0:
        for req in requests:
            assert "tools" not in req
            assert "tool_choice" not in req
        return
    tools = _hand_built_tools(n_tools)
    for req in requests[:max_tool_calls]:
        assert req["tools"] == tools
        assert "tool_choice" not in req
    after = requests[max_tool_calls]
    assert after.get("tools") == tools
    assert after.get("tool_choice") == "none"
