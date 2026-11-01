"""LLM cost tracking (P0).

Mirrors ARag's `cost_tracker.py`: a `MODEL_PRICING` table (USD per **1,000** tokens) and a
thread-safe `CostTracker` that turns token usage into dollars and accumulates running totals.
Cost is a first-class output of the scenariogen LLM flow (the narrative carries an invocation
cost page) and a budget bound (a `max_total_cost_usd` circuit breaker), so every LLM call routes
its usage through here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger("scenariogen.llm.cost")

# USD per 1,000 tokens. gpt-5 family (mirrors ARag's MODEL_PRICING convention). The governed rate
# card is configs/scenariogen/llm/pricing.json; this table is the default for tests and tools.
MODEL_PRICING: dict[str, dict[str, Any]] = {
    "gpt-5": {"input": 0.00125, "output": 0.01, "cached_input": 0.000125},
    "gpt-5-mini": {"input": 0.00025, "output": 0.002, "cached_input": 0.000025},
    "gpt-5-nano": {"input": 0.00005, "output": 0.0004, "cached_input": 0.000005},
}
_RATE_KEYS = ("input", "output", "cached_input", "cache_write")


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """USD cost for one call: prompt/1000*input_rate + completion/1000*output_rate.

    Raises:
        ValueError: ``model`` has no rate-card row. A $0 cost would hide the call from the
            budget breaker (design D9), so a missing row fails loudly.
    """
    rate = _rates_for(model, pricing or MODEL_PRICING)
    return (prompt_tokens / 1000.0) * float(rate["input"]) + (completion_tokens / 1000.0) * float(rate["output"])


def _rates_for(model: str, table: dict[str, Any]) -> dict[str, Any]:
    rate = table.get(model)
    if rate is None:
        raise ValueError(f"no pricing row for model {model!r}; add one to configs/scenariogen/llm/pricing.json")
    return dict(rate)


def _overlay(base: dict[str, Any], block: dict[str, Any]) -> dict[str, Any]:
    """Base rates with a tier or peak block's rates on top.

    A block that sets `input` but not `cached_input` / `cache_write` prices those at the block's
    `input`, not the base cache rate.
    """
    given = {k: block[k] for k in _RATE_KEYS if block.get(k) is not None}
    out = {**base, **given}
    if "input" in given:
        for key in ("cached_input", "cache_write"):
            if key not in given:
                out.pop(key, None)
    return out


def _in_peak(at: datetime, windows: list[str]) -> bool:
    """True when `at` (UTC) falls Monday-Friday inside a `HH:MM-HH:MM` window (end exclusive)."""
    utc = at.astimezone(UTC)
    if utc.weekday() >= 5:
        return False
    minute = utc.hour * 60 + utc.minute
    for window in windows:
        start, end = window.split("-")
        lo = int(start[:2]) * 60 + int(start[3:])
        hi = int(end[:2]) * 60 + int(end[3:])
        if lo <= minute < hi:
            return True
    return False


def cost_usd(
    rates: dict[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int,
    cache_write_tokens: int,
    at: datetime,
) -> float:
    """USD cost of one call from a pricing row, with cache, tier and peak rates.

    Args:
        rates: A pricing row (USD per 1,000 tokens), optionally with ``tiers`` or ``peak``.
        prompt_tokens: All prompt tokens, including cached and cache-written ones.
        completion_tokens: Output tokens.
        cached_prompt_tokens: Prompt tokens read from the cache.
        cache_write_tokens: Prompt tokens written to the cache.
        at: Timezone-aware start time of the call.

    Returns:
        The cost in USD.

    Raises:
        ValueError: ``at`` is naive, or cached plus written tokens exceed the prompt tokens.
    """
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError(f"cost_usd needs a timezone-aware start time, got naive {at!r}")
    if cached_prompt_tokens + cache_write_tokens > prompt_tokens:
        raise ValueError(
            f"cached ({cached_prompt_tokens}) + written ({cache_write_tokens}) tokens exceed prompt ({prompt_tokens})")
    rate = {k: rates[k] for k in _RATE_KEYS if rates.get(k) is not None}
    tiers = [t for t in rates.get("tiers") or [] if prompt_tokens > t["above_prompt_tokens"]]
    if tiers:
        rate = _overlay(rate, max(tiers, key=lambda t: t["above_prompt_tokens"]))
    peak = rates.get("peak")
    if peak and _in_peak(at, peak["weekdays_utc"]):
        rate = _overlay(rate, peak)
    input_rate = float(rate["input"])
    cached_rate = float(rate.get("cached_input", input_rate))
    write_rate = float(rate.get("cache_write", input_rate))
    uncached = prompt_tokens - cached_prompt_tokens - cache_write_tokens
    return (uncached * input_rate + cached_prompt_tokens * cached_rate + cache_write_tokens * write_rate
            + completion_tokens * float(rate["output"])) / 1000.0


@dataclass(frozen=True)
class UsageRecord:
    """One LLM call's token usage + its dollar cost."""

    model: str
    stage: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    cached_prompt_tokens: int = 0
    cache_write_tokens: int = 0


class CostTracker:
    """Thread-safe accumulator of LLM usage → dollars, with an optional JSONL audit log."""

    def __init__(
        self,
        pricing: dict[str, Any] | None = None,
        *,
        log_path: Path | None = None,
    ):
        self._pricing = pricing or MODEL_PRICING
        self._log_path = log_path
        self._lock = Lock()
        self._records: list[UsageRecord] = []
        self.total_cost_usd = 0.0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.per_model: dict[str, dict[str, float]] = {}

    def record_usage(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        stage: str = "",
        cached_prompt_tokens: int = 0,
        cache_write_tokens: int = 0,
        at: datetime | None = None,
    ) -> UsageRecord:
        """Prices one call with `cost_usd` and adds it to the running totals.

        Args:
            model: The requested model (its rate-card row).
            prompt_tokens: All prompt tokens, including cached and cache-written ones.
            completion_tokens: Output tokens.
            stage: The flow stage, for per-stage rollups.
            cached_prompt_tokens: Prompt tokens read from the cache.
            cache_write_tokens: Prompt tokens written to the cache.
            at: The call's start time (timezone-aware); now (UTC) when None.

        Raises:
            ValueError: ``model`` has no rate-card row.
        """
        cost = cost_usd(
            _rates_for(model, self._pricing), prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens, cache_write_tokens=cache_write_tokens,
            at=at or datetime.now(UTC),
        )
        record = UsageRecord(
            model=model,
            stage=stage,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=cost,
            cached_prompt_tokens=cached_prompt_tokens,
            cache_write_tokens=cache_write_tokens,
        )
        with self._lock:
            self._records.append(record)
            self.total_cost_usd += cost
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            bucket = self.per_model.setdefault(
                model, {"calls": 0.0, "prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0}
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += prompt_tokens
            bucket["completion_tokens"] += completion_tokens
            bucket["cost_usd"] += cost
        if self._log_path is not None:
            self._append_log(record)
        return record

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_calls": len(self._records),
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
                "total_cost_usd": round(self.total_cost_usd, 6),
                "per_model": {m: dict(v) for m, v in self.per_model.items()},
            }

    def _append_log(self, record: UsageRecord) -> None:
        line = json.dumps(
            {
                "model": record.model,
                "stage": record.stage,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "cached_prompt_tokens": record.cached_prompt_tokens,
                "cache_write_tokens": record.cache_write_tokens,
                "cost_usd": round(record.estimated_cost_usd, 6),
            },
            separators=(",", ":"),
        )
        assert self._log_path is not None
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
