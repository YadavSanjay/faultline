"""C7: a 429's `retry-after` header sets the wait before the next attempt, capped at `max_wait_s`.

Timing budget (spec Notes: total sleep about 3 s): 1.0 s + 0.3 s + 0.6 s = 1.9 s of intended sleep.
`time.sleep` never returns early by more than the OS timer resolution (~1-16 ms on Windows), so every
lower bound below leaves at least 50 ms of margin under the intended wait; upper bounds leave at least
0.2 s over it for scheduling and test overhead.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from src.scenariogen.llm.gateway import LLMCallResult

_MSG = [{"role": "user", "content": "q"}]
_MIN_WAIT_S = 0.01


def _timed_call(kit: SimpleNamespace, headers: dict[str, str], max_wait_s: float) -> tuple[LLMCallResult, float]:
    """A 429 carrying `headers`, then a normal response; returns the result and the wall-clock seconds."""
    fake = kit.FakeSDKClient([kit.RateLimitError(headers), kit.make_response(content="after 429")])
    gw = kit.openai_gateway(fake, min_wait_s=_MIN_WAIT_S, max_wait_s=max_wait_s)
    start = time.perf_counter()
    result: Any = gw.complete(messages=_MSG, model=kit.MODEL, stage="s")
    elapsed = time.perf_counter() - start
    assert len(fake.calls) == 2
    return result, elapsed


@pytest.mark.criterion("C7")
def test_retry_after_is_honoured(kit: SimpleNamespace) -> None:
    # oracle: Requirement 7 / C7 — retry-after "1" with max_wait_s=5: wait 1 s (not the 0.01 s backoff).
    # Margin: the bound is 0.9 s, 100 ms under the 1 s wait.
    result, elapsed = _timed_call(kit, {"retry-after": "1"}, max_wait_s=5.0)
    assert result.attempts == 2
    assert result.content == "after 429"
    assert elapsed >= 0.9


@pytest.mark.criterion("C7")
def test_retry_after_is_capped_at_max_wait(kit: SimpleNamespace) -> None:
    # oracle: Requirement 7 / C7 — retry-after "30" capped at max_wait_s=0.3: the wait is 0.3 s, so the
    # call takes under 2 s (spec bound) and at least 0.25 s (50 ms under the capped wait; the 0.01 s
    # backoff alone would finish far sooner).
    result, elapsed = _timed_call(kit, {"retry-after": "30"}, max_wait_s=0.3)
    assert result.attempts == 2
    assert elapsed < 2.0
    assert elapsed >= 0.25


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_unusable_header_falls_back_to_backoff(kit: SimpleNamespace) -> None:
    # oracle: Requirement 7 / C7 — a decimal header "0.6" is honoured (>= 0.55 s, 50 ms margin), while
    # a missing, non-numeric or empty header falls back to the 0.01 s backoff (< 0.5 s, 0.49 s margin).
    honoured, honoured_s = _timed_call(kit, {"retry-after": "0.6"}, max_wait_s=5.0)
    assert honoured.attempts == 2
    assert honoured_s >= 0.55
    for headers in ({}, {"retry-after": "soon"}, {"retry-after": ""}):
        result, elapsed = _timed_call(kit, headers, max_wait_s=5.0)
        assert result.attempts == 2
        assert elapsed < 0.5
