"""Hand-built inputs and hand oracles for the signal-health spec tests.

Everything is synthetic (docs/specs/signal-health.md, Notes). ``AS_OF`` is a fixed run date (never the
current date). ``status_dict`` writes out a Requirement 1 status dict by hand so the roll-up tests
(C2) never call ``source_status``; ``mixed_statuses`` is a hand-built list with every status present
and two sources per status (so the by-name tie-break is visible), and ``mixed_rollup`` is Requirement 2
applied to it by hand.

Helpers are exposed through the session-scoped ``h`` fixture (session scope so hypothesis tests can
use it).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

AS_OF = date(2026, 10, 18)


def status_dict(source: str, status: str) -> dict[str, Any]:
    """A plausible hand-written Requirement 1 dict for ``source`` with the given ``status``."""
    if status == "ok":
        return {"source": source, "latest": "2026-10-10", "staleness_days": 8, "stale": False,
                "covered": 4, "expected": 4, "coverage": 1.0, "holds": {}, "status": "ok"}
    if status == "degraded":
        return {"source": source, "latest": "2026-10-10", "staleness_days": 8, "stale": False,
                "covered": 3, "expected": 4, "coverage": 0.75, "holds": {}, "status": "degraded"}
    if status == "down":
        return {"source": source, "latest": None, "staleness_days": None, "stale": False,
                "covered": 0, "expected": 10, "coverage": 0.0, "holds": {"timeout": 10}, "status": "down"}
    if status == "unused":
        return {"source": source, "latest": None, "staleness_days": None, "stale": False,
                "covered": 0, "expected": 0, "coverage": None, "holds": {}, "status": "unused"}
    raise AssertionError(f"unknown status {status!r}")


def mixed_statuses() -> list[dict[str, Any]]:
    """Input order deliberately not sorted by status or by name."""
    return [
        status_dict("options", "degraded"),
        status_dict("throughput", "unused"),
        status_dict("news", "ok"),
        status_dict("macrorag", "down"),
        status_dict("crowding", "ok"),
        status_dict("gpr", "degraded"),
        status_dict("xbrlrag", "down"),
        status_dict("aaa_extra", "unused"),
    ]


def mixed_rollup() -> dict[str, Any]:
    """Requirement 2 by hand on ``mixed_statuses``: worst is down; 2 of each status; down
    (macrorag, xbrlrag), degraded (gpr, options), ok (crowding, news), unused (aaa_extra, throughput)."""
    return {
        "overall": "down",
        "counts": {"ok": 2, "degraded": 2, "down": 2, "unused": 2},
        "sources": [
            status_dict("macrorag", "down"),
            status_dict("xbrlrag", "down"),
            status_dict("gpr", "degraded"),
            status_dict("options", "degraded"),
            status_dict("crowding", "ok"),
            status_dict("news", "ok"),
            status_dict("aaa_extra", "unused"),
            status_dict("throughput", "unused"),
        ],
    }


@pytest.fixture(scope="session")
def h() -> SimpleNamespace:
    return SimpleNamespace(
        as_of=AS_OF,
        status_dict=status_dict,
        mixed_statuses=mixed_statuses,
        mixed_rollup=mixed_rollup,
    )
