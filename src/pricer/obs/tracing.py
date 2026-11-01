"""Lightweight tracing — moved to `src/common/obs/tracing.py`.

Kept as a re-export shim so existing `pricer.obs.tracing` imports keep working; remove once
all call sites import from `common.obs` directly.
"""

from __future__ import annotations

from src.common.obs.tracing import drain_spans, observe_duration, record_span

__all__ = ["drain_spans", "observe_duration", "record_span"]
