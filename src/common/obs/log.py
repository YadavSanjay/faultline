"""Structured (JSON) logging with contextual fields.

A contextvar carries run-scoped fields (run_id, snapshot_id, valuation_date, trade_id,
asset_class, source, …) that are attached to *every* log record, so logs are queryable as
data rather than grepped as text. `configure_logging(json_logs=...)` installs a JSON
formatter for machines or the familiar human format for an interactive CLI.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_CTX: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_ctx", default=None)


def _ctx() -> dict[str, Any]:
    return _CTX.get() or {}

_STD = {  # LogRecord attributes we don't echo as "extra"
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "context",
    "taskName",
}


def bind(**fields: Any) -> None:
    """Merge fields into the ambient log context (until cleared / context exits)."""
    _CTX.set({**_ctx(), **fields})


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    token = _CTX.set({**_ctx(), **fields})
    try:
        yield
    finally:
        _CTX.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_ctx())


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.context = _ctx()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "context", {}) or {})
        for k, v in record.__dict__.items():        # explicit logger.info(..., extra={...})
            if k not in _STD and not k.startswith("_"):
                payload.setdefault(k, v)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(json_logs: bool = False, level: int = logging.INFO,
                      logfile: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))

    fmt: logging.Formatter = (
        JsonFormatter() if json_logs
        else logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    ctx_filter = _ContextFilter()
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(ctx_filter)
        root.addHandler(h)
    root.setLevel(level)
