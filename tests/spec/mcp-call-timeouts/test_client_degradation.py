"""mcp-call-timeouts C4: a timeout in a real stdio MCP server degrades the tool, not the server.

``McpClient`` is built with its existing constructor (``McpClient(specs, *, timeout_s, call_timeout_s)``),
which per Requirement 4 behaves as the policy with ``default_s=call_timeout_s``, no overrides and
``degrade_after=1``. The server is the committed ``fixture_mcp_server.py`` (spec Notes): ``slow`` sleeps
``SLOW_SECONDS`` (8 s) in a worker thread, ``fast`` returns ``FAST_RESULT`` at once.

Wall-clock bounds (spec Notes): c = 1.5 s, T = 30 s; (a) c <= elapsed < c + 4 s, the 4 s margin covering
scheduling and stdio round-trips on a loaded machine while staying far below the 8 s the tool would take;
(c) elapsed < 1 s, since a degraded tool must fail without waiting (c is 1.5 s, so a wait would show).
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from src.scenariogen.llm.mcp.client import McpClient, ServerSpec

CALL_TIMEOUT_S = 1.5  # c
OVERALL_TIMEOUT_S = 30.0  # T
A_MARGIN_S = 4.0  # (a) upper bound c + 4 s, well under SLOW_SECONDS
C_BOUND_S = 1.0  # (c) "without waiting"

SLOW_SECONDS = 8.0  # fixture_mcp_server.SLOW_SECONDS
FAST_EXPECTED = {"tool": "fast", "ok": True, "value": 42}  # fixture_mcp_server.FAST_RESULT, parsed by hand


def _timed(fn: Any) -> tuple[float, Any, BaseException | None]:
    start = time.perf_counter()
    try:
        out, err = fn(), None
    except Exception as exc:  # the test asserts on the exception type below
        out, err = None, exc
    return time.perf_counter() - start, out, err


def _fast_call(client: McpClient) -> dict[str, Any]:
    """A `fast` call; a TimeoutError here (server wrongly degraded) is turned into an assertion failure."""
    try:
        text = client.call_tool("fx", "fast", {})
    except TimeoutError as exc:
        pytest.fail(f"fast call on the same server failed after another tool timed out: {exc!r}")
    return dict(json.loads(text))


@pytest.mark.criterion("C4")
def test_c4_slow_timeout_degrades_slow_only(fixture_specs: dict[str, ServerSpec]):
    # oracle: Requirements 2 and 4 with wall-clock bounds from the spec Notes: (a) the slow call raises
    # TimeoutError after >= c and < c + 4 s (the tool takes 8 s); (b) fast on the same server then returns
    # its fixed JSON (FAST_EXPECTED, parsed by hand from the fixture); (c) a second slow call raises
    # TimeoutError in < 1 s.
    assert SLOW_SECONDS > CALL_TIMEOUT_S + A_MARGIN_S
    with McpClient(fixture_specs, timeout_s=OVERALL_TIMEOUT_S, call_timeout_s=CALL_TIMEOUT_S) as client:
        elapsed_a, _, err_a = _timed(lambda: client.call_tool("fx", "slow", {}))
        assert isinstance(err_a, TimeoutError), repr(err_a)
        assert elapsed_a >= CALL_TIMEOUT_S, elapsed_a
        assert elapsed_a < CALL_TIMEOUT_S + A_MARGIN_S, elapsed_a

        assert _fast_call(client) == FAST_EXPECTED

        elapsed_c, _, err_c = _timed(lambda: client.call_tool("fx", "slow", {}))
        assert isinstance(err_c, TimeoutError), repr(err_c)
        assert elapsed_c < C_BOUND_S, elapsed_c


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_c4_metamorphic_fast_unchanged_by_slow_timeout(fixture_specs: dict[str, ServerSpec]):
    # oracle: Requirement 2 metamorphic relation: a timeout of `slow` must not change what `fast` on the same
    # server answers, so fast's result before and after the timeout is identical (and equals the fixture's
    # fixed JSON); the timed-out tool stays degraded and fails fast (< 1 s) on every later call.
    with McpClient(fixture_specs, timeout_s=OVERALL_TIMEOUT_S, call_timeout_s=CALL_TIMEOUT_S) as client:
        before = _fast_call(client)
        _, _, err = _timed(lambda: client.call_tool("fx", "slow", {}))
        assert isinstance(err, TimeoutError), repr(err)
        after = [_fast_call(client) for _ in range(3)]
        assert before == FAST_EXPECTED
        assert all(a == before for a in after), after
        for _ in range(2):
            elapsed, _, err_again = _timed(lambda: client.call_tool("fx", "slow", {}))
            assert isinstance(err_again, TimeoutError), repr(err_again)
            assert elapsed < C_BOUND_S, elapsed
