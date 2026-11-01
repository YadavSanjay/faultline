"""Call capture: CallRecord + a contextvar TraceCollector (P3).

Mirrors MacroRAG's `usage.py`: the three call-sites (LLM gateway, MacroRAG transport, ARag
transport) call ``record_llm_call`` / ``record_rest_call``, which fold a ``CallRecord`` into the
active collector. Outside a run (``current_collector() is None``) recording is a no-op, so the
call-sites stay clean and unit tests that hit them directly don't need a run.

Prompt/response bodies are large, so capture is a **toggle** (default on) with a per-field
**character cap** (default 20k, as MacroRAG). Token/cost/latency accounting is always recorded;
only the big request/response/prompt strings are gated.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from src.scenariogen.layer_cost import response_cost

logger = logging.getLogger("scenariogen.trace")

_CURRENT: ContextVar[TraceCollector | None] = ContextVar("scenariogen_trace", default=None)

# Module-level capture policy (mutable via configure_capture).
_CAPTURE_ENABLED = True
_MAX_CHARS = 20_000


def configure_capture(*, enabled: bool | None = None, max_chars: int | None = None) -> None:
    """Toggle body capture / set the per-field character cap (process-wide)."""
    global _CAPTURE_ENABLED, _MAX_CHARS
    if enabled is not None:
        _CAPTURE_ENABLED = bool(enabled)
    if max_chars is not None:
        _MAX_CHARS = max(0, int(max_chars))


def _cap(obj: Any) -> str:
    """Serialize + truncate a body for capture. Empty string when capture is off / obj is None."""
    if not _CAPTURE_ENABLED or obj is None:
        return ""
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str, ensure_ascii=False)
    if len(s) > _MAX_CHARS:
        return s[:_MAX_CHARS] + f"...[+{len(s) - _MAX_CHARS} chars truncated]"
    return s


def _prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\n---\n{user}".encode()).hexdigest()[:12]


@dataclass
class CallRecord:
    """One captured external call (LLM or RAG) — cost/latency + (toggle) request/response/prompt."""

    seq: int
    service: str                 # "llm" | "macrorag" | "arag"
    purpose: str                 # LLM stage, or REST path (e.g. "/query", "/api/v1/narrative/search")
    ok: bool
    latency_ms: float
    scenario: str = ""           # the scenario being generated when this call was made
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    prompt_hash: str = ""
    system: str = ""             # LLM system prompt (captured)
    user: str = ""               # LLM user prompt (captured)
    request: str = ""            # REST request body (captured)
    response: str = ""           # REST response / LLM completion (captured)
    error: str = ""
    # Whether cost_usd is a cost the service or provider reported (a REST response's usage.cost_usd, an
    # LLM call's token cost); False means it is unknown, not zero (cost-per-layer).
    cost_reported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq, "service": self.service, "purpose": self.purpose, "ok": self.ok,
            "latency_ms": round(self.latency_ms, 1), "scenario": self.scenario, "model": self.model,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6), "prompt_hash": self.prompt_hash,
            "system": self.system, "user": self.user, "request": self.request,
            "response": self.response, "error": self.error, "cost_reported": self.cost_reported,
        }


@dataclass
class TraceCollector:
    """Ordered per-run accumulator of CallRecords + a rollup summary."""

    run_id: str
    records: list[CallRecord] = field(default_factory=list)
    current_scenario: str = ""
    _seq: int = 0

    def set_scenario(self, scenario: str) -> None:
        """Tag subsequent calls with the scenario currently being generated."""
        self.current_scenario = scenario

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def add(self, record: CallRecord) -> None:
        self.records.append(record)

    def summary(self) -> dict[str, Any]:
        by_service: dict[str, dict[str, Any]] = {}
        total_cost = 0.0
        total_latency = 0.0
        for r in self.records:
            b = by_service.setdefault(
                r.service, {"n_calls": 0, "n_errors": 0, "cost_usd": 0.0,
                            "latency_ms": 0.0, "total_tokens": 0})
            b["n_calls"] += 1
            b["n_errors"] += 0 if r.ok else 1
            b["cost_usd"] += r.cost_usd
            b["latency_ms"] += r.latency_ms
            b["total_tokens"] += r.prompt_tokens + r.completion_tokens
            total_cost += r.cost_usd
            total_latency += r.latency_ms
        for b in by_service.values():
            b["cost_usd"] = round(b["cost_usd"], 6)
            b["latency_ms"] = round(b["latency_ms"], 1)
        return {
            "run_id": self.run_id, "n_calls": len(self.records),
            "total_cost_usd": round(total_cost, 6), "total_latency_ms": round(total_latency, 1),
            "by_service": by_service,
        }


def current_collector() -> TraceCollector | None:
    return _CURRENT.get()


def new_collector(run_id: str) -> TraceCollector:
    return TraceCollector(run_id=run_id)


def bind_collector(collector: TraceCollector) -> Any:
    """Bind a collector as current; returns the reset token for ``unbind_collector``."""
    return _CURRENT.set(collector)


def unbind_collector(token: Any) -> None:
    _CURRENT.reset(token)


@contextmanager
def trace_run(run_id: str) -> Iterator[TraceCollector]:
    """Bind a fresh collector for the duration of one run (convenience for tests / simple flows)."""
    collector = new_collector(run_id)
    token = bind_collector(collector)
    try:
        yield collector
    finally:
        unbind_collector(token)


def record_rest_call(*, service: str, purpose: str, ok: bool, latency_ms: float,
                     request: Any = None, response: Any = None, error: str = "") -> None:
    """Record a MacroRAG / ARag REST call into the active collector (no-op if none)."""
    col = current_collector()
    if col is None:
        return
    try:
        cost = response_cost(response)
    except ValueError as exc:                    # a broken usage block: record the call without a cost
        logger.warning("  %s %s: ignoring reported cost (%s)", service, purpose, exc)
        cost = None
    col.add(CallRecord(
        seq=col.next_seq(), service=service, purpose=purpose, ok=ok, latency_ms=latency_ms,
        scenario=col.current_scenario, request=_cap(request), response=_cap(response), error=error,
        cost_usd=cost or 0.0, cost_reported=cost is not None))


def record_llm_call(*, purpose: str, model: str, ok: bool, latency_ms: float,
                    messages: list[dict[str, Any]] | None = None, content: str = "",
                    prompt_tokens: int = 0, completion_tokens: int = 0, cost_usd: float = 0.0,
                    error: str = "") -> None:
    """Record an OpenAI LLM call into the active collector (no-op if none)."""
    col = current_collector()
    if col is None:
        return
    system_parts = [str(m.get("content", "")) for m in (messages or []) if m.get("role") == "system"]
    user_parts = [str(m.get("content", "")) for m in (messages or []) if m.get("role") == "user"]
    system, user = "\n".join(system_parts), "\n".join(user_parts)
    col.add(CallRecord(
        seq=col.next_seq(), service="llm", purpose=purpose, ok=ok, latency_ms=latency_ms,
        scenario=col.current_scenario, model=model, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd, prompt_hash=_prompt_hash(system, user),
        system=_cap(system), user=_cap(user), response=_cap(content), error=error, cost_reported=True))
