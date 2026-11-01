"""Structured logging — moved to `src/common/obs/log.py`.

Kept as a re-export shim so existing `pricer.obs.log` imports keep working; remove once all
call sites import from `common.obs` directly.
"""

from __future__ import annotations

from src.common.obs.log import (
    JsonFormatter,
    bind,
    configure_logging,
    current_context,
    log_context,
)

__all__ = ["JsonFormatter", "bind", "configure_logging", "current_context", "log_context"]
