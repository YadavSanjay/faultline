"""C3: requests Claude would reject are refused before any client call (spec Requirement 5)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend
from src.scenariogen.llm.gateway import FatalChatError

_MSG = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
_TOOLS = [{"type": "function", "function": {"name": "arag__search", "description": "d",
                                            "parameters": {"type": "object"}}}]


def _refused(kit: SimpleNamespace, **kwargs: Any) -> None:
    """`create` raises FatalChatError and the fake client records no call."""
    fake = kit.FakeAnthropicClient([kit.make_message([kit.text_block("ok")])])
    backend = AnthropicChatBackend(client=fake, models=kit.MODELS)
    with pytest.raises(FatalChatError):
        backend.create(**{"model": kit.SONNET, "messages": _MSG, "tools": _TOOLS, **kwargs})
    assert fake.calls == []


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("temperature", [0.0, 0.7, 1.0])
def test_temperature_is_refused(kit: SimpleNamespace, temperature: float) -> None:
    # oracle: Requirement 5 — any temperature that is not None (0.0 included) raises before calling.
    _refused(kit, temperature=temperature)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("tool_choice", ["required", "any", {"type": "function", "function": {"name": "arag__search"}},
                                         {"type": "tool", "name": "arag__search"}])
def test_forced_tool_choice_is_refused(kit: SimpleNamespace, tool_choice: Any) -> None:
    # oracle: Requirement 5 — a tool_choice other than "auto", "none" or None raises before calling.
    _refused(kit, tool_choice=tool_choice)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("tools", [None, []])
@pytest.mark.parametrize("tool_choice", ["required", "any", {"type": "tool", "name": "arag__search"}])
def test_forced_tool_choice_is_refused_without_tools(kit: SimpleNamespace, tools: list[dict[str, Any]] | None,
                                                     tool_choice: Any) -> None:
    # oracle: Requirement 5 — a forced tool_choice is refused "whether or not tools are given", so it
    # raises before calling even with tools None or empty (where a valid tool_choice is just dropped).
    _refused(kit, tool_choice=tool_choice, tools=tools)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("model", ["claude-opus-9", "gpt-5", "claude-sonnet-5-5-2026-01-01", ""])
def test_unknown_model_is_refused(kit: SimpleNamespace, model: str) -> None:
    # oracle: Requirement 5 — a model that is not a key of `models` raises before calling.
    _refused(kit, model=model)


@pytest.mark.criterion("C3")
@given(
    temperature=st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False, min_value=-2, max_value=2)),
    tool_choice=st.one_of(st.sampled_from(["auto", "none", None, "required", "any", "AUTO"]),
                          st.text(alphabet="abcnoeqrui", min_size=1, max_size=8),
                          st.dictionaries(st.sampled_from(["type", "name"]), st.text(max_size=4), max_size=2)),
    model=st.one_of(st.sampled_from(["claude-sonnet-5-5", "claude-haiku-4-5"]),
                    st.text(alphabet="claude-0123456789", max_size=20)),
    tools=st.sampled_from([_TOOLS, None, []]),
)
def test_refusal_property(kit: SimpleNamespace, temperature: float | None, tool_choice: Any, model: str,
                          tools: list[dict[str, Any]] | None) -> None:
    # oracle: Requirement 5 — refused (FatalChatError, no client call) exactly when temperature is not
    # None, or tool_choice is not one of "auto"/"none"/None, or model is not in `models`, whether or
    # not tools are given; otherwise exactly one stream call is made.
    refused = (temperature is not None or tool_choice not in ("auto", "none", None)
               or model not in ("claude-sonnet-5-5", "claude-haiku-4-5"))
    fake = kit.FakeAnthropicClient([kit.make_message([kit.text_block("ok")], model=model)])
    backend = AnthropicChatBackend(client=fake, models=kit.MODELS)
    kwargs: dict[str, Any] = {"model": model, "messages": _MSG, "tools": tools, "temperature": temperature,
                              "tool_choice": tool_choice, "max_completion_tokens": 50}
    if refused:
        with pytest.raises(FatalChatError):
            backend.create(**kwargs)
        assert fake.calls == []
    else:
        result = backend.create(**kwargs)
        assert len(fake.calls) == 1
        assert result.content == "ok"
