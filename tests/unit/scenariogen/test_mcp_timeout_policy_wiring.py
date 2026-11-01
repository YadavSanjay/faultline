"""Unit tests for the implementer-defined parts of mcp-call-timeouts.

The locked spec tests cover the policy rules and the client's default (per-tool, degrade after one)
behaviour. These cover the wiring: an explicit policy's per-tool timeout and degrade threshold inside
the real client (against a stdio fixture server), and the committed mcp.json loading as a valid
policy with the synthesize override.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from src.scenariogen.llm.config import load_mcp_settings
from src.scenariogen.llm.mcp.client import McpClient, ServerSpec
from src.scenariogen.llm.mcp.timeouts import CallTimeoutPolicy

_SPECS = {"fx": ServerSpec("fx", sys.executable, [str(Path(__file__).with_name("mcp_fixture_server.py"))],
                           tool_prefix="fx")}
_SYNTH = "macro__macro_synthesize_shock_vector"


def test_per_tool_override_lets_a_slow_tool_finish() -> None:
    # Default 1s would time out a 2s call; the fx__slow override of 10s lets it finish.
    policy = CallTimeoutPolicy(default_s=1.0, per_tool_s={"fx__slow": 10.0}, degrade_after=1)
    with McpClient(_SPECS, timeout_s=30.0, policy=policy) as client:
        assert json.loads(client.call_tool("fx", "slow", {"seconds": 2.0}))["ok"] is True
        assert policy.is_degraded("fx__slow") is False


def test_degrade_after_two_retries_once_before_skipping() -> None:
    policy = CallTimeoutPolicy(default_s=0.5, per_tool_s={}, degrade_after=2)
    with McpClient(_SPECS, timeout_s=30.0, policy=policy) as client:
        for _ in range(2):  # both calls wait out the 0.5s timeout: the first doesn't degrade the tool
            start = time.perf_counter()
            with pytest.raises(TimeoutError):
                client.call_tool("fx", "slow", {"seconds": 5.0})
            assert time.perf_counter() - start >= 0.5
        start = time.perf_counter()
        with pytest.raises(TimeoutError, match="previously timed out"):
            client.call_tool("fx", "slow", {"seconds": 5.0})
        assert time.perf_counter() - start < 0.5  # degraded now: skipped without waiting
        assert json.loads(client.call_tool("fx", "fast", {}))["ok"] is True


def test_committed_mcp_json_is_a_valid_policy_with_a_synthesize_override() -> None:
    settings = load_mcp_settings()
    policy = CallTimeoutPolicy.from_settings(settings)
    assert policy.timeout_for(_SYNTH) > policy.default_s
    assert policy.timeout_for(_SYNTH) <= float(settings["timeout_s"])
