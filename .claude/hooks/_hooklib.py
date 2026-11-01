"""Shared helpers for the faultline project hooks (registered in `.claude/settings.json`).

The hook launcher is the bare `python` on PATH (3.10, no project packages), so this module and
the hook scripts use only the standard library and stay 3.10-compatible. Real work runs in the
py312 env, found by `env_python()`.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# Most-preferred first. Override with FAULTLINE_HOOK_PYTHON on another machine.
_PY312_CANDIDATES = (
    os.environ.get("FAULTLINE_HOOK_PYTHON"),
    os.path.join(os.path.dirname(sys.executable), "envs", "py312", "python.exe"),   # `python` is the base env
    os.path.expanduser(r"~\Anaconda3\envs\py312\python.exe"),
    os.path.expanduser(r"~\miniconda3\envs\py312\python.exe"),
)


def read_event() -> dict[str, Any]:
    """Parses the hook event JSON from stdin; returns {} on any failure."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return {}


def env_python() -> str | None:
    """Returns the py312 interpreter path, or None when it can't be found (hooks then fail open)."""
    for candidate in _PY312_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def project_dir() -> str:
    """Returns the repository root (Claude Code sets CLAUDE_PROJECT_DIR for hooks)."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
