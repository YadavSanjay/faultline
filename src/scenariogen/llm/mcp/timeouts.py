"""Per-tool MCP call timeouts and per-tool degradation (spec `mcp-call-timeouts`).

One slow tool (MacroRAG's shock synthesis can take minutes) used to share a single call timeout with
every cheap lookup, and one timeout disabled its whole server for the rest of the run. The policy
gives each namespaced tool (``<server prefix>__<tool>``, as in ``phase_allowlists``) its own call
timeout and counts timeouts per tool, so only the tool that keeps timing out is skipped.
"""

from __future__ import annotations

import math
from typing import Any

DEFAULT_TIMEOUT_S = 600.0       # the client's overall wait for one call (and for connect/close)
DEFAULT_CALL_TIMEOUT_S = 90.0   # a tool's call timeout when it has no override
DEFAULT_DEGRADE_AFTER = 1       # timeouts after which a tool is skipped for the rest of the run


def _positive_seconds(value: Any, key: str) -> float:
    """Returns ``value`` as seconds, or raises ValueError naming ``key`` unless finite and > 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"mcp.json {key} must be a number of seconds, got {value!r}")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"mcp.json {key} must be a finite number of seconds > 0, got {value!r}")
    return seconds


class CallTimeoutPolicy:
    """Per-tool call timeouts plus a per-tool timeout counter for one run.

    Each namespaced MCP tool gets its own call timeout (a ``tool_timeouts_s`` entry in
    ``configs/scenariogen/llm/mcp.json``, else ``call_timeout_s``), and timeouts are counted per
    tool, so one slow tool is skipped after ``degrade_after_timeouts`` timeouts without disabling
    the other tools on its server.
    """

    def __init__(self, *, default_s: float, per_tool_s: dict[str, float], degrade_after: int) -> None:
        """Builds a policy.

        Args:
            default_s: Call timeout (seconds) for a tool with no override.
            per_tool_s: Call timeout (seconds) per namespaced tool name.
            degrade_after: Timeouts after which a tool is skipped for the rest of the run.
        """
        self.default_s = default_s
        self._per_tool_s = dict(per_tool_s)
        self._degrade_after = degrade_after
        self._timeouts: dict[str, int] = {}

    @classmethod
    def from_settings(cls, mcp_settings: dict[str, Any]) -> CallTimeoutPolicy:
        """Builds a policy from the ``mcp.json`` settings dict, validating every timeout key.

        Args:
            mcp_settings: The loaded ``mcp.json``; missing keys take the module defaults.

        Returns:
            The validated policy.

        Raises:
            ValueError: A timeout is not a finite number > 0, a call timeout exceeds ``timeout_s``,
                or ``degrade_after_timeouts`` is not an integer >= 1. The message names the key.
        """
        overall = _positive_seconds(mcp_settings.get("timeout_s", DEFAULT_TIMEOUT_S), "timeout_s")
        default = _positive_seconds(mcp_settings.get("call_timeout_s", DEFAULT_CALL_TIMEOUT_S), "call_timeout_s")
        if default > overall:
            raise ValueError(f"mcp.json call_timeout_s ({default:g}) exceeds timeout_s ({overall:g})")
        raw_per_tool = mcp_settings.get("tool_timeouts_s", {})
        if not isinstance(raw_per_tool, dict):
            raise ValueError(f"mcp.json tool_timeouts_s must be a map of tool -> seconds, got {raw_per_tool!r}")
        per_tool: dict[str, float] = {}
        for tool, value in raw_per_tool.items():
            seconds = _positive_seconds(value, f"tool_timeouts_s[{tool}]")
            if seconds > overall:
                raise ValueError(f"mcp.json tool_timeouts_s[{tool}] ({seconds:g}) exceeds timeout_s ({overall:g})")
            per_tool[str(tool)] = seconds
        degrade_after = mcp_settings.get("degrade_after_timeouts", DEFAULT_DEGRADE_AFTER)
        if isinstance(degrade_after, bool) or not isinstance(degrade_after, int) or degrade_after < 1:
            raise ValueError(f"mcp.json degrade_after_timeouts must be an integer >= 1, got {degrade_after!r}")
        return cls(default_s=default, per_tool_s=per_tool, degrade_after=degrade_after)

    def timeout_for(self, tool: str) -> float:
        """Returns the call timeout (seconds) for a namespaced tool name."""
        return self._per_tool_s.get(tool, self.default_s)

    def record_timeout(self, tool: str) -> None:
        """Counts one timeout against a namespaced tool name."""
        self._timeouts[tool] = self._timeouts.get(tool, 0) + 1

    def is_degraded(self, tool: str) -> bool:
        """Returns whether the tool has timed out ``degrade_after`` times in this run."""
        return self._timeouts.get(tool, 0) >= self._degrade_after
