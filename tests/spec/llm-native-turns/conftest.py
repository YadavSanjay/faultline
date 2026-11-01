"""Offline fakes for the llm-native-turns spec tests.

Fakes sit only at the I/O boundaries the spec allows (docs/specs/llm-native-turns.md, Notes):

- ``FakeSDKClient``: stands in for the OpenAI SDK client handed to ``OpenAIChatBackend(client=...)``.
  ``chat.completions.create(**kwargs)`` records a deep copy of each call's kwargs and returns (or
  raises) the next scripted item; when the script runs out it keeps using the last item.
- ``FakeChatBackend``: a ``ChatBackend`` returning scripted ``RawCompletion``s (existing fields only).
- ``FakeToolSource``: an MCP tool source with fixed ``ToolListing``s and a fixed JSON result.

Everything else (``InstrumentedChatClient``, ``OpenAIChatBackend``, ``McpToolRegistry``,
``run_tool_loop``, ``CostTracker``) is the real project code.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import InstrumentedChatClient, OpenAIChatBackend, RawCompletion
from src.scenariogen.llm.mcp.client import ServerSpec, ToolListing
from src.scenariogen.llm.mcp.registry import McpToolRegistry

MODEL = "gpt-5"
# MODEL_PRICING["gpt-5"], USD per 1,000 tokens (spec Notes; OpenAI's published gpt-5 rate).
GPT5_INPUT_PER_1K = 0.00125
GPT5_OUTPUT_PER_1K = 0.01

ABSENT = object()


def make_response(
    *,
    model: Any = MODEL,
    content: str | None = "done",
    finish_reason: str = "stop",
    tool_calls: Sequence[tuple[str, str, str]] = (),
    reasoning_content: Any = ABSENT,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cached_tokens: Any = ABSENT,
) -> SimpleNamespace:
    """An OpenAI-SDK-shaped chat completion response.

    ``tool_calls`` items are ``(id, name, arguments)``. ``reasoning_content`` and ``cached_tokens``
    are left off the message / usage entirely unless given, and ``model=ABSENT`` leaves
    the response without a ``model`` attribute.
    """
    message = SimpleNamespace(
        content=content,
        tool_calls=[
            SimpleNamespace(id=i, type="function", function=SimpleNamespace(name=n, arguments=a))
            for i, n, a in tool_calls
        ] or None,
    )
    if reasoning_content is not ABSENT:
        message.reasoning_content = reasoning_content
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    if cached_tokens is not ABSENT:
        usage.prompt_tokens_details = SimpleNamespace(cached_tokens=cached_tokens)
    resp = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, message=message)],
        usage=usage,
    )
    if model is not ABSENT:
        resp.model = model
    return resp


class RateLimitError(Exception):
    """Shaped like ``openai.RateLimitError``: the class name, ``status_code`` and response headers."""

    def __init__(self, headers: dict[str, str]):
        super().__init__("rate limited")
        self.status_code = 429
        self.response = SimpleNamespace(status_code=429, headers=headers)


class _Completions:
    def __init__(self, owner: FakeSDKClient):
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        return self._owner.handle(kwargs)


class FakeSDKClient:
    """Fake OpenAI SDK client: ``chat.completions.create(**kwargs)`` over a script."""

    def __init__(self, script: Sequence[Any] | None = None,
                 responder: Callable[[int, dict[str, Any]], Any] | None = None):
        self._script = list(script or [])
        self._responder = responder
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=_Completions(self))

    def handle(self, kwargs: dict[str, Any]) -> Any:
        index = len(self.calls)
        self.calls.append(copy.deepcopy(kwargs))
        if self._responder is not None:
            item = self._responder(index, kwargs)
        else:
            item = self._script[min(index, len(self._script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


class FakeChatBackend:
    """A ``ChatBackend`` returning scripted ``RawCompletion``s; counts calls."""

    def __init__(self, script: Sequence[RawCompletion]):
        self._script = list(script)
        self.n_calls = 0

    def create(self, **kwargs: Any) -> RawCompletion:
        item = self._script[min(self.n_calls, len(self._script) - 1)]
        self.n_calls += 1
        return item


def raw(finish_reason: str, content: str = "ok") -> RawCompletion:
    return RawCompletion(content=content, prompt_tokens=10, completion_tokens=5, finish_reason=finish_reason)


TOOL_RESULT = json.dumps({"result": "fixed"})


class FakeToolSource:
    """MCP tool source with fixed listings and a fixed JSON result."""

    def __init__(self, listings: dict[str, list[ToolListing]]):
        self._listings = listings
        self.dispatched: list[tuple[str, str, dict[str, Any]]] = []

    def list_tools(self, server: str) -> list[ToolListing]:
        return list(self._listings.get(server, []))

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        self.dispatched.append((server, tool, arguments))
        return TOOL_RESULT


def make_registry(n_tools: int) -> McpToolRegistry:
    """A registry over one fake server ``arag`` advertising ``n_tools`` tools (``tool0``, ...)."""
    listings = [
        ToolListing(name=f"tool{i}", description=f"fixed tool {i}",
                    input_schema={"type": "object", "properties": {"q": {"type": "string"}}})
        for i in range(n_tools)
    ]
    specs = {"arag": ServerSpec(name="arag", command="py", args=[], tool_prefix="arag")}
    return McpToolRegistry(FakeToolSource({"arag": listings}), specs)


def openai_gateway(fake: FakeSDKClient, *, max_attempts: int = 4, min_wait_s: float = 0.0,
                   max_wait_s: float = 0.0) -> InstrumentedChatClient:
    """The real gateway over the real ``OpenAIChatBackend`` over a fake SDK client."""
    return InstrumentedChatClient(OpenAIChatBackend(client=fake), CostTracker(), max_attempts=max_attempts,
                                  min_wait_s=min_wait_s, max_wait_s=max_wait_s)


def fake_backend_gateway(backend: FakeChatBackend, *, max_attempts: int = 4) -> InstrumentedChatClient:
    return InstrumentedChatClient(backend, CostTracker(), max_attempts=max_attempts, min_wait_s=0.0, max_wait_s=0.0)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The fakes and builders above, for tests (a hyphenated spec dir can't be imported from)."""
    return SimpleNamespace(
        MODEL=MODEL, ABSENT=ABSENT, GPT5_INPUT_PER_1K=GPT5_INPUT_PER_1K, GPT5_OUTPUT_PER_1K=GPT5_OUTPUT_PER_1K,
        make_response=make_response, RateLimitError=RateLimitError, FakeSDKClient=FakeSDKClient,
        FakeChatBackend=FakeChatBackend, raw=raw, TOOL_RESULT=TOOL_RESULT, make_registry=make_registry,
        openai_gateway=openai_gateway, fake_backend_gateway=fake_backend_gateway,
    )
