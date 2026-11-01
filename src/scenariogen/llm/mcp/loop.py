"""Bounded agentic tool-calling loop (P1).

Drives an OpenAI function-calling conversation against the MCP tool registry: the model decides
which tools to call, the loop dispatches them to ARag/MacroRAG and feeds the results back, until
the model answers with text or the per-phase tool-call budget is hit. The budget is the hard
terminator — once exhausted, the next turn is sent with `tool_choice="none"` so it must produce a
final answer.

This is the "agentic within a phase" half of the hybrid design: the deterministic skeleton picks
the phase, the model drives the tool calls inside it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..gateway import InstrumentedChatClient, LLMCallResult, ToolCall
from .registry import McpToolRegistry

logger = logging.getLogger("scenariogen.llm.mcp.loop")


@dataclass
class ToolLoopResult:
    """The outcome of one bounded tool loop."""

    content: str
    n_tool_calls: int
    budget_exhausted: bool
    calls: list[LLMCallResult] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)


def _parse_arguments(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("tool arguments were not valid JSON: %.120s", raw)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _assistant_message(result: LLMCallResult) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": result.content or None,
        "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in result.tool_calls
        ],
    }


def _tool_message(call: ToolCall, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.id, "content": content}


def run_tool_loop(
    *,
    gateway: InstrumentedChatClient,
    registry: McpToolRegistry,
    system: str,
    user: str,
    model: str,
    stage: str,
    allow: set[str] | None = None,
    max_tool_calls: int = 24,
    max_completion_tokens: int | None = None,
    temperature: float | None = None,
) -> ToolLoopResult:
    tools = registry.openai_tools(allow)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    calls: list[LLMCallResult] = []
    tool_calls_made = 0
    # Safety bound on iterations: every iteration either finishes or makes ≥1 tool call, and tool
    # calls are budget-capped, so this can't be reached in normal operation — it's a backstop.
    max_iterations = max_tool_calls + 2
    for _ in range(max_iterations):
        # Once the budget is used up the tools stay on the request (the history holds tool calls,
        # which some providers require definitions for) but tool_choice="none" forbids new calls.
        tool_choice = "none" if (tools and tool_calls_made >= max_tool_calls) else None
        result = gateway.complete(
            messages=messages,
            model=model,
            stage=stage,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            tools=tools or None,
            tool_choice=tool_choice,
        )
        calls.append(result)
        if not result.tool_calls:
            return ToolLoopResult(
                content=result.content,
                n_tool_calls=tool_calls_made,
                budget_exhausted=tool_calls_made >= max_tool_calls,
                calls=calls,
                messages=messages,
            )
        # The provider's own turn when the backend supplies it (keeps reasoning), else a rebuild.
        messages.append(result.assistant_message or _assistant_message(result))
        for call in result.tool_calls:
            if tool_calls_made >= max_tool_calls:
                messages.append(_tool_message(call, json.dumps({"error": "tool-call budget exhausted"})))
                continue
            tool_calls_made += 1
            if allow is not None and call.name not in allow:  # only the phase's offered tools may run
                logger.warning("tool %s is not allowed in stage %s; refused", call.name, stage)
                messages.append(_tool_message(call, json.dumps({"error": f"tool {call.name} is not available"})))
                continue
            arguments = _parse_arguments(call.arguments)
            try:
                output = registry.dispatch(call.name, arguments)
            except Exception as exc:
                logger.warning("tool %s failed: %s", call.name, exc)
                output = json.dumps({"error": str(exc)})
            messages.append(_tool_message(call, output))

    # Backstop: return whatever the last turn produced.
    last = calls[-1] if calls else None
    return ToolLoopResult(
        content=last.content if last else "",
        n_tool_calls=tool_calls_made,
        budget_exhausted=True,
        calls=calls,
        messages=messages,
    )
