"""Run-level ARag call budget + breaker (harness-audit F5).

The transport's ``timeout_s`` bounds a *single* ``narrative_search`` (10 min default). Across a
whole run — N companies x (narrative + facts) calls x scenarios x retries — there was no
aggregate ceiling, and the shared CPU-inference service (RAGINFER) could be pounded by a runaway
loop.

``AragBudget`` is a run-scoped breaker: it caps the total number of ARag calls and their
cumulative wall-clock. ``check()`` (called before each request) raises ``AragBudgetError`` once a
ceiling is crossed; ``charge()`` (after each request) records the call. ``AragBudgetError`` is a
plain ``RuntimeError`` — deliberately **not** an ``AragTransportError`` — so the provider's
per-scenario fail-loud handler does not swallow it and it propagates to hard-stop the run. A
``None`` ceiling means unlimited (backward-compatible default).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class AragBudgetError(RuntimeError):
    """A run-level ARag ceiling (call count or wall-clock) was exceeded — hard-stop the run."""


@dataclass
class AragBudget:
    max_calls: int | None = None
    max_wall_clock_s: float | None = None
    _calls: int = field(default=0, init=False)
    _elapsed_s: float = field(default=0.0, init=False)

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def elapsed_s(self) -> float:
        return self._elapsed_s

    def check(self) -> None:
        """Raise if a ceiling has already been reached (called *before* each ARag request)."""
        if self.max_calls is not None and self._calls >= self.max_calls:
            raise AragBudgetError(
                f"ARag call budget exhausted: {self._calls} call(s) >= max {self.max_calls}")
        if self.max_wall_clock_s is not None and self._elapsed_s >= self.max_wall_clock_s:
            raise AragBudgetError(
                f"ARag wall-clock budget exhausted: {self._elapsed_s:.1f}s "
                f">= max {self.max_wall_clock_s:.1f}s")

    def charge(self, latency_s: float) -> None:
        """Record one completed ARag call (success or failure — both consume the service)."""
        self._calls += 1
        self._elapsed_s += max(0.0, latency_s)
