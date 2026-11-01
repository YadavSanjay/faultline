"""Raw call-trace capture for the dual-RAG flow (P3).

Captures every external call a scenario run makes — MacroRAG REST, ARag REST, and OpenAI LLM —
with its request, response, prompts, cost, latency and outcome, into a per-run trace so the UI
Trace Explorer (P4/P5) can drill into every service/model interaction. Models MacroRAG's
`llm/usage.py` `CallRecord`: a contextvar collector (no-op outside a run), a capture toggle +
character truncation for the large bodies, and one JSONL line per call.
"""

from __future__ import annotations

from .capture import (
    CallRecord,
    TraceCollector,
    bind_collector,
    configure_capture,
    current_collector,
    new_collector,
    record_llm_call,
    record_rest_call,
    trace_run,
    unbind_collector,
)
from .store import write_trace

__all__ = [
    "CallRecord",
    "TraceCollector",
    "bind_collector",
    "configure_capture",
    "current_collector",
    "new_collector",
    "record_llm_call",
    "record_rest_call",
    "trace_run",
    "unbind_collector",
    "write_trace",
]
