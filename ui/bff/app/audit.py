"""Append-only audit trail for BFF writes.

Every mutation records one JSONL line (who, what, when, why, outcome) under
``data/pricer/audit/audit.jsonl``. Append-only and human-readable so a control reviewer can
reconstruct exactly which operator approved which scenario (and why), independent of the
domain store that the write itself landed in.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .settings import data_dir


def _audit_path() -> Path:
    return data_dir() / "pricer" / "audit" / "audit.jsonl"


def record_action(action: str, target: str, actor: str, *, reason: str = "",
                  details: dict[str, Any] | None = None, outcome: str = "ok") -> dict[str, Any]:
    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "target": target,
        "actor": actor,
        "reason": reason,
        "outcome": outcome,
        "details": details or {},
    }
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def recent(limit: int = 100) -> list[dict[str, Any]]:
    """The most recent audit entries, newest first."""
    path = _audit_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in reversed(lines[-limit:]):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
