"""Run-scoped curve/vol memoization (P1).

The same QuantLib curve is otherwise bootstrapped once *per trade* (every pricer
calls `build_ois_curve(...)`), so a 30-trade SOFR book pays for ~30 identical
bootstraps. This cache builds each distinct curve **once per run** and hands the
shared `YieldTermStructureHandle` back to every trade that needs it — the dominant
win for large books and for scenario fan-out (reprice the book under N market states).

Scope & safety:
- The cache is held in a `contextvars.ContextVar`, so it is **process-local** and
  bounded to the `with curve_cache():` block the runner (or a worker) opens. It is
  *not* a global singleton that leaks across runs.
- Keys are by curve **content** (the frozen `ParOISCurveData`/`TreasuryCurveData`
  plus build options), so two identical curves collapse to one even when the loader
  hands back fresh-but-equal data objects.
- A cached handle is built at the curve's own reference date; within one run the
  global `ql.Settings.evaluationDate` is fixed, so reuse is sound. Parallel workers
  each open their own cache (one valuation date per process).
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast


@dataclass
class CurveCache:
    """Memoizes built curves for the lifetime of one run (one process)."""

    store: dict[Any, Any] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def get_or_build[T](self, key: Any, build: Callable[[], T]) -> T:
        if key in self.store:
            self.hits += 1
            return cast(T, self.store[key])
        self.misses += 1
        value = build()
        self.store[key] = value
        return value

    def stats(self) -> str:
        total = self.hits + self.misses
        return (
            f"{len(self.store)} distinct curve(s) built; "
            f"{self.hits}/{total} build calls served from cache"
        )


_active: contextvars.ContextVar[CurveCache | None] = contextvars.ContextVar(
    "curve_cache", default=None
)


@contextmanager
def curve_cache(cache: CurveCache | None = None) -> Iterator[CurveCache]:
    """Activate a curve cache for the enclosed block (e.g. one run / one worker)."""
    cache = cache if cache is not None else CurveCache()
    token = _active.set(cache)
    try:
        yield cache
    finally:
        _active.reset(token)


def cached[T](key: Any, build: Callable[[], T]) -> T:
    """Return the cached value for `key`, or `build()` it. No-op if no cache is active.

    With no active cache (the default for direct unit tests / library use) this just
    calls `build()`, so behaviour is unchanged outside a `curve_cache()` block.
    """
    active = _active.get()
    if active is None:
        return build()
    return active.get_or_build(key, build)
