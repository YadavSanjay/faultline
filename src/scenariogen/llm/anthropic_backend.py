"""Native Anthropic chat backend behind the `ChatBackend` Protocol (scenariogen LLM flow).

Translates the flow's OpenAI-shaped requests into Anthropic Messages API calls and the final
message back into a `RawCompletion`, so the gateway's retry, cost, budget, finish-reason and
served-model handling apply unchanged. Requests always stream (long outputs stay clear of HTTP
timeouts), carry the model's thinking / effort settings from the registry, and by default ask
Anthropic to cache the prompt prefix — OpenAI and DeepSeek cache automatically, Anthropic only on
request. An assistant turn the backend returned travels back verbatim in `_native`, which keeps
thinking blocks and their signatures intact across tool turns.

Spec: docs/specs/llm-anthropic-backend.md. Design: docs/design/llm_multi_provider_design.md §5.2.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from .gateway import (
    FatalChatError,
    OpenAIChatBackend,
    RawCompletion,
    ToolCall,
    openai_client_kwargs,
)

logger = logging.getLogger("scenariogen.llm.anthropic")

_PROVIDER = "anthropic"
_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "refusal",
}
_TOOL_CHOICES = {"auto": {"type": "auto"}, "none": {"type": "none"}}


def _tool_definition(tool: Mapping[str, Any]) -> dict[str, Any]:
    fn = tool.get("function", {})
    return {"name": fn.get("name"), "description": fn.get("description"), "input_schema": fn.get("parameters")}


def _parse_input(arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _assistant_content(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The content blocks for an assistant turn: its own Anthropic blocks, else a rebuild."""
    native = message.get("_native")
    if isinstance(native, Mapping) and native.get("provider") == _PROVIDER:
        return list(native.get("content", []))
    blocks: list[dict[str, Any]] = []
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        fn = call.get("function", {})
        blocks.append({"type": "tool_use", "id": call.get("id"), "name": fn.get("name"),
                       "input": _parse_input(fn.get("arguments", ""))})
    return blocks


def _translate_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Splits OpenAI-shaped messages into the Anthropic `system` text and `messages` list."""
    system: list[str] = []
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system.append(str(message.get("content", "")))
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": message.get("tool_call_id"),
                     "content": message.get("content")}
            if out and out[-1].get("_tool_results"):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block], "_tool_results": True})
        elif role == "assistant":
            out.append({"role": "assistant", "content": _assistant_content(message)})
        else:
            out.append({"role": role, "content": message.get("content")})
    for message in out:
        message.pop("_tool_results", None)
    return ("\n\n".join(system) if system else None), out


def _usage(usage: Any) -> tuple[int, int, int, int]:
    """(prompt, cached, written, completion) tokens; prompt includes the cached and written ones."""
    def count(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    cached, written = count("cache_read_input_tokens"), count("cache_creation_input_tokens")
    return count("input_tokens") + cached + written, cached, written, count("output_tokens")


class AnthropicChatBackend:
    """`anthropic`-backed chat backend that translates OpenAI-shaped requests to the Messages API."""

    def __init__(
        self,
        client: Any = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 600.0,
        models: Mapping[str, Mapping[str, Any]] | None = None,
        prompt_caching: bool = True,
    ) -> None:
        """Builds the backend.

        Args:
            client: An Anthropic SDK client; when None, one is built with the SDK's retries off.
            api_key: The API key, or None to leave it to the SDK's environment lookup.
            base_url: The API base URL, or None for the SDK default.
            timeout_s: The per-request timeout in seconds.
            models: Per model name, ``{"max_output_tokens": int, "effort": str | None,
                "thinking": str | None}``.
            prompt_caching: When true, every request carries the top-level
                ``cache_control: {"type": "ephemeral"}`` so Anthropic caches the prompt prefix.
        """
        if client is None:
            import importlib

            anthropic = importlib.import_module("anthropic")
            client = anthropic.Anthropic(**openai_client_kwargs(
                api_key=api_key, base_url=base_url, timeout_s=timeout_s))
        self._client: Any = client
        self._models = {name: dict(spec) for name, spec in (models or {}).items()}
        self._prompt_caching = prompt_caching

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_completion_tokens: int | None = None,
        temperature: float | None = None,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> RawCompletion:
        """Streams one Messages API call and returns its final message as a `RawCompletion`.

        Args:
            model: The requested model; must be in ``models``.
            messages: OpenAI-shaped chat messages.
            max_completion_tokens: The output cap, or None for the model's ``max_output_tokens``.
            temperature: Must be None (Claude models reject sampling parameters).
            response_format: ``{"type": "json_schema", ...}``, ``{"type": "json_object"}`` or None.
            tools: OpenAI function-tool definitions, or None.
            tool_choice: ``"auto"``, ``"none"`` or None.

        Returns:
            The normalized completion.

        Raises:
            FatalChatError: A refused request or a non-retryable client error.
            TransientChatError: A retryable client error.
        """
        request = self._request(model, messages, max_completion_tokens, temperature, response_format,
                                tools, tool_choice)
        try:
            with self._client.messages.stream(**request) as stream:
                final = stream.get_final_message()
        except Exception as exc:
            # Classified like the OpenAI SDK's errors (same class names and statuses).
            raise OpenAIChatBackend._classify(exc) from exc
        return self._completion(final)

    def _request(
        self,
        model: str,
        messages: list[dict[str, Any]],
        max_completion_tokens: int | None,
        temperature: float | None,
        response_format: dict[str, Any] | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        spec = self._models.get(model)
        if spec is None:
            raise FatalChatError(f"anthropic: model {model!r} is not configured")
        if temperature is not None:
            raise FatalChatError(f"anthropic: {model} rejects sampling parameters (temperature={temperature})")
        if tool_choice is not None and (not isinstance(tool_choice, str) or tool_choice not in _TOOL_CHOICES):
            raise FatalChatError(f"anthropic: forced tool_choice {tool_choice!r} is rejected by {model}")
        cap = int(spec["max_output_tokens"])
        system, translated = _translate_messages(messages)
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": cap if max_completion_tokens is None else min(max_completion_tokens, cap),
            "messages": translated,
        }
        if system is not None:
            request["system"] = system
        if tools:
            request["tools"] = [_tool_definition(t) for t in tools]
            if tool_choice is not None:
                request["tool_choice"] = dict(_TOOL_CHOICES[tool_choice])
        if spec.get("thinking"):
            request["thinking"] = {"type": spec["thinking"]}
        output_config: dict[str, Any] = {}
        if spec.get("effort"):
            output_config["effort"] = spec["effort"]
        if response_format and response_format.get("type") == "json_schema":
            output_config["format"] = {"type": "json_schema",
                                       "schema": response_format["json_schema"]["schema"]}
        if output_config:
            request["output_config"] = output_config
        if self._prompt_caching:
            request["cache_control"] = {"type": "ephemeral"}
        return request

    @staticmethod
    def _completion(final: Any) -> RawCompletion:
        blocks = list(getattr(final, "content", None) or [])
        text = "".join(str(b.text) for b in blocks if b.type == "text")
        tool_calls = tuple(
            ToolCall(id=str(b.id), name=str(b.name), arguments=json.dumps(b.input))
            for b in blocks if b.type == "tool_use"
        )
        stop_reason = str(getattr(final, "stop_reason", "") or "")
        if stop_reason == "refusal":
            details = getattr(final, "stop_details", None)
            logger.warning("anthropic %s refused (category=%s)", getattr(final, "model", ""),
                           getattr(details, "category", None))
        prompt, cached, written, completion = _usage(getattr(final, "usage", None))
        assistant = None
        if tool_calls:
            assistant = {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.name, "arguments": tc.arguments}} for tc in tool_calls],
                "_native": {"provider": _PROVIDER, "content": [b.model_dump(exclude_none=True) for b in blocks]},
            }
        return RawCompletion(
            content=text,
            prompt_tokens=prompt,
            completion_tokens=completion,
            finish_reason=_STOP_REASONS.get(stop_reason, stop_reason),
            tool_calls=tool_calls,
            assistant_message=assistant,
            cached_prompt_tokens=cached,
            cache_write_tokens=written,
            served_model=str(getattr(final, "model", "") or ""),
        )
