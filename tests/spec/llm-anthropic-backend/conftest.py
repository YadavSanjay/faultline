"""Offline fakes for the llm-anthropic-backend spec tests.

Fakes sit only at the I/O boundaries the spec allows (docs/specs/llm-anthropic-backend.md, Notes):

- ``FakeAnthropicClient``: stands in for the ``anthropic`` SDK client handed to
  ``AnthropicChatBackend(client=...)``. ``messages.stream(**kwargs)`` records a deep copy of the
  kwargs and returns a context manager whose ``get_final_message()`` gives the next scripted message
  (or raises the next scripted exception from ``stream`` itself). It has no ``messages.create``.
- ``FakeToolSource``: an MCP tool source with fixed ``ToolListing``s and a fixed JSON result (C6).

Everything else (``AnthropicChatBackend``, ``InstrumentedChatClient``, ``McpToolRegistry``,
``run_tool_loop``, ``CostTracker``) is the real project code.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import InstrumentedChatClient
from src.scenariogen.llm.mcp.client import ServerSpec, ToolListing
from src.scenariogen.llm.mcp.registry import McpToolRegistry

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5"
# The backend's model table (spec Requirement 1): Sonnet with effort and thinking, Haiku with neither.
MODELS: dict[str, dict[str, Any]] = {
    SONNET: {"max_output_tokens": 64000, "effort": "high", "thinking": "adaptive"},
    HAIKU: {"max_output_tokens": 32000, "effort": None, "thinking": None},
}
# Spec Notes: pricing for gateway tests, USD per 1,000 tokens.
PRICING = {SONNET: {"input": 0.002, "output": 0.01, "cached_input": 0.0002, "cache_write": 0.0025}}

ABSENT = object()


class Block:
    """An SDK-shaped content block: attributes plus ``model_dump(exclude_none=...)``."""

    def __init__(self, **fields: Any):
        self._fields = dict(fields)
        for key, value in fields.items():
            setattr(self, key, value)

    def model_dump(self, exclude_none: bool = False) -> dict[str, Any]:
        out = copy.deepcopy(self._fields)
        if exclude_none:
            out = {k: v for k, v in out.items() if v is not None}
        return out


def text_block(text: str) -> Block:
    """A text block; ``citations=None`` as the SDK has it (dropped by exclude_none=True)."""
    return Block(type="text", text=text, citations=None)


def tool_use_block(id_: str, name: str, input_: dict[str, Any]) -> Block:
    return Block(type="tool_use", id=id_, name=name, input=input_)


def thinking_block(thinking: str, signature: str) -> Block:
    return Block(type="thinking", thinking=thinking, signature=signature)


def make_message(
    content: Sequence[Block],
    *,
    stop_reason: str = "end_turn",
    model: str = SONNET,
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: Any = ABSENT,
    cache_creation: Any = ABSENT,
    category: str | None = None,
) -> SimpleNamespace:
    """A Messages-API-shaped final message. Cache fields are left off ``usage`` unless given."""
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    if cache_read is not ABSENT:
        usage.cache_read_input_tokens = cache_read
    if cache_creation is not ABSENT:
        usage.cache_creation_input_tokens = cache_creation
    msg = SimpleNamespace(content=list(content), stop_reason=stop_reason, usage=usage, model=model,
                          stop_details=None)
    if category is not None:
        msg.stop_details = SimpleNamespace(category=category)
    return msg


class _Stream:
    """The context manager ``messages.stream`` returns."""

    def __init__(self, owner: FakeAnthropicClient, message: Any):
        self._owner = owner
        self._message = message

    def __enter__(self) -> _Stream:
        self._owner.entered += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._owner.exited += 1

    def get_final_message(self) -> Any:
        return self._message


class _Messages:
    """``client.messages``: only ``stream`` (no ``create``)."""

    def __init__(self, owner: FakeAnthropicClient):
        self._owner = owner

    def stream(self, **kwargs: Any) -> _Stream:
        return self._owner.handle(kwargs)


class FakeAnthropicClient:
    """Fake ``anthropic.Anthropic``: ``messages.stream(**kwargs)`` over a script.

    When the script runs out the last item is reused. An exception item is raised from ``stream``.
    """

    def __init__(self, script: Sequence[Any]):
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.entered = 0
        self.exited = 0
        self.messages = _Messages(self)

    def handle(self, kwargs: dict[str, Any]) -> _Stream:
        index = len(self.calls)
        self.calls.append(copy.deepcopy(kwargs))
        item = self._script[min(index, len(self._script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return _Stream(self, item)


class RateLimitError(Exception):
    """Shaped like ``anthropic.RateLimitError``: the class name, ``status_code`` and response headers."""

    def __init__(self, headers: dict[str, str]):
        super().__init__("rate limited")
        self.status_code = 429
        self.response = SimpleNamespace(status_code=429, headers=headers)


class StatusError(Exception):
    """An SDK-like error whose class name is not in any transient list: only its status can classify it."""

    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        super().__init__(f"status {status_code}")
        self.status_code = status_code
        self.response = SimpleNamespace(status_code=status_code, headers=dict(headers or {}))


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
    """A registry over one fake server ``arag`` advertising ``n_tools`` tools (``arag__tool0``, ...)."""
    listings = [
        ToolListing(name=f"tool{i}", description=f"fixed tool {i}",
                    input_schema={"type": "object", "properties": {"q": {"type": "string"}}})
        for i in range(n_tools)
    ]
    specs = {"arag": ServerSpec(name="arag", command="py", args=[], tool_prefix="arag")}
    return McpToolRegistry(FakeToolSource({"arag": listings}), specs)


def gateway(backend: Any, *, max_attempts: int = 4) -> InstrumentedChatClient:
    """The real gateway over ``backend``, priced with the spec's Notes pricing, no waits."""
    return InstrumentedChatClient(backend, CostTracker(dict(PRICING)), max_attempts=max_attempts,
                                  min_wait_s=0.0, max_wait_s=0.0)


@pytest.fixture(scope="session")
def kit() -> SimpleNamespace:
    """The fakes and builders above, for tests (a hyphenated spec dir can't be imported from)."""
    return SimpleNamespace(
        SONNET=SONNET, HAIKU=HAIKU, MODELS=MODELS, PRICING=PRICING, ABSENT=ABSENT, Block=Block,
        text_block=text_block, tool_use_block=tool_use_block, thinking_block=thinking_block,
        make_message=make_message, FakeAnthropicClient=FakeAnthropicClient, RateLimitError=RateLimitError,
        StatusError=StatusError, TOOL_RESULT=TOOL_RESULT, make_registry=make_registry, gateway=gateway,
    )
