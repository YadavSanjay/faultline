"""Lightweight tracing: time a unit of work into a metric + structured log.

`observe_duration(histogram, **labels)` records wall-time into a Prometheus histogram
(a "span per trade / curve build / download"). If OpenTelemetry is installed and a tracer
configured, it also opens a real span — but OTel is optional, so with no extra dependency
the same call still produces latency metrics and structured timing logs.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .log import current_context

logger = logging.getLogger("obs.trace")

# In-process span collector (ENG-4). `observe_duration` appends each timed span to a buffer
# keyed by the run_id bound in the log context, so a run can persist real per-stage / per-
# trade durations regardless of the log level (the debug-log line is dropped at INFO). The
# runner drains and writes the buffer at run end; it is bounded so a runaway can't grow it.
_SPANS: dict[str, list[dict[str, Any]]] = {}
_SPAN_CAP = 10_000


def record_span(span: str, duration_ms: float, **labels: str) -> None:
    run_id = current_context().get("run_id")
    if not run_id:
        return
    buf = _SPANS.setdefault(str(run_id), [])
    if len(buf) < _SPAN_CAP:
        buf.append({"span": span, "duration_ms": round(float(duration_ms), 3), **labels})


def drain_spans(run_id: str) -> list[dict[str, Any]]:
    """Pop and return the collected spans for a run (called once at run end)."""
    return _SPANS.pop(str(run_id), [])

# Optional: real OpenTelemetry spans when the SDK is present + configured. OTel being
# absent is the normal, supported case, so this degrades to metrics-only timing.
_TRACER: Any = None
try:
    from opentelemetry import trace as _otel_trace

    _TRACER = _otel_trace.get_tracer("obs")
except Exception:
    _TRACER = None


@contextmanager
def observe_duration(histogram: Any | None = None, *, span: str = "",
                     **labels: str) -> Iterator[None]:
    """Time the block: observe into `histogram` (with `labels`), log, and OTel-span it."""
    otel_cm = _TRACER.start_as_current_span(span or "span") if _TRACER is not None else None
    if otel_cm is not None:
        otel_cm.__enter__()
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        if histogram is not None:
            (histogram.labels(**labels) if labels else histogram).observe(dt)
        if span:
            record_span(span, dt * 1e3, **labels)
            logger.debug("span %s done", span, extra={"span": span, "duration_ms": dt * 1e3, **labels})
        if otel_cm is not None:
            otel_cm.__exit__(None, None, None)
