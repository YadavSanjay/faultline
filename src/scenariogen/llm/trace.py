"""Per-run telemetry rollup via a contextvar (P0).

Mirrors ARag's `TraceContext`: a `contextvars`-scoped accumulator so the LLM gateway and the
MCP tool loop can record cost / tokens / tool-calls without threading a context object through
every signature. When no run is active (`current_run()` returns ``None``) recording is a no-op,
so the gateway degrades cleanly outside a flow (e.g. in unit tests calling it directly).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_CURRENT: ContextVar[RunTrace | None] = ContextVar("scenariogen_llm_run", default=None)


@dataclass
class StageRollup:
    n_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class RunTrace:
    """Accumulated cost/tokens/tool-calls for one LLM scenario-generation run."""

    run_id: str
    total_cost_usd: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    n_llm_calls: int = 0
    n_tool_calls: int = 0
    retries: int = 0
    per_stage: dict[str, StageRollup] = field(default_factory=dict)
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)

    def record_llm_call(
        self,
        *,
        stage: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        retries: int = 0,
    ) -> None:
        self.n_llm_calls += 1
        self.total_cost_usd += cost_usd
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.retries += retries
        rollup = self.per_stage.setdefault(stage, StageRollup())
        rollup.n_calls += 1
        rollup.prompt_tokens += prompt_tokens
        rollup.completion_tokens += completion_tokens
        rollup.cost_usd += cost_usd

    def record_tool_call(self, name: str) -> None:
        self.n_tool_calls += 1
        self.tool_calls_by_name[name] = self.tool_calls_by_name.get(name, 0) + 1

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            "n_llm_calls": self.n_llm_calls,
            "n_tool_calls": self.n_tool_calls,
            "retries": self.retries,
            "per_stage": {
                stage: {
                    "n_calls": r.n_calls,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "cost_usd": round(r.cost_usd, 6),
                }
                for stage, r in self.per_stage.items()
            },
            "tool_calls_by_name": dict(self.tool_calls_by_name),
        }


def current_run() -> RunTrace | None:
    """The active run trace, or ``None`` outside a `run_trace` block."""
    return _CURRENT.get()


@contextmanager
def run_trace(run_id: str) -> Iterator[RunTrace]:
    """Bind a fresh `RunTrace` for the duration of one scenario-generation run."""
    trace = RunTrace(run_id=run_id)
    token = _CURRENT.set(trace)
    try:
        yield trace
    finally:
        _CURRENT.reset(token)
