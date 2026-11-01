"""C3, C6 and C8: `OpenAIChatBackend` (and the gateway over it) with a fake OpenAI SDK client.

C3: private (`_`-prefixed) message keys never reach the SDK and the caller's input is untouched.
C6: the served model must be the requested model or a dated snapshot of it.
C8: cached prompt tokens are reported; prompt tokens and cost are unchanged.
"""

from __future__ import annotations

import copy
import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.gateway import FatalChatError, OpenAIChatBackend

GPT5_CACHED_INPUT_PER_1K = 0.000125   # configs/scenariogen/llm/pricing.json, gpt-5 cached_input


def _strip_private(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def _hand_cost(prompt_tokens: int, completion_tokens: int, kit: SimpleNamespace, cached_tokens: int = 0) -> float:
    """MODEL_PRICING["gpt-5"] applied by hand: tokens / 1000 * USD-per-1K rate."""
    return ((prompt_tokens - cached_tokens) / 1000 * kit.GPT5_INPUT_PER_1K
            + cached_tokens / 1000 * GPT5_CACHED_INPUT_PER_1K
            + completion_tokens / 1000 * kit.GPT5_OUTPUT_PER_1K)


# ----------------------------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_private_keys_are_removed(kit: SimpleNamespace) -> None:
    # oracle: Requirement 3 — the expected list below is the input with `_native` and `_meta` removed.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
        {"role": "assistant", "content": None, "_native": {"blocks": [{"type": "thinking"}]},
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
         "reasoning_content": "r"},
        {"role": "tool", "tool_call_id": "c1", "content": "{}", "_meta": "x"},
    ]
    before = copy.deepcopy(messages)
    fake = kit.FakeSDKClient([kit.make_response()])
    OpenAIChatBackend(client=fake).create(model=kit.MODEL, messages=messages)
    assert fake.calls[0]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
         "reasoning_content": "r"},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
    ]
    assert messages == before


_KEYS = st.one_of(
    st.sampled_from(["role", "content", "name", "tool_call_id", "reasoning_content", "_native", "_meta", "_"]),
    st.text(alphabet="_abc", min_size=1, max_size=4),
)
_VALUES = st.one_of(st.none(), st.integers(-5, 5), st.text(max_size=5),
                    st.dictionaries(st.text(alphabet="_ab", max_size=3), st.integers(0, 3), max_size=2))


@pytest.mark.criterion("C3")
@given(messages=st.lists(st.dictionaries(_KEYS, _VALUES, max_size=5), max_size=5))
def test_private_keys_property(kit: SimpleNamespace, messages: list[dict[str, Any]]) -> None:
    # oracle: Requirement 3 — the SDK sees the input with every top-level `_` key removed, in order,
    # and the caller's list and dicts are unchanged.
    before = copy.deepcopy(messages)
    fake = kit.FakeSDKClient([kit.make_response()])
    OpenAIChatBackend(client=fake).create(model=kit.MODEL, messages=messages)
    assert fake.calls[0]["messages"] == _strip_private(before)
    assert messages == before


# ----------------------------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_prefix_sharing_model_is_fatal_after_one_call(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 — gpt-5-mini-2025-08-07 is not gpt-5 nor gpt-5 + "-YYYY-MM-DD".
    fake = kit.FakeSDKClient([kit.make_response(model="gpt-5-mini-2025-08-07")])
    with pytest.raises(FatalChatError):
        kit.openai_gateway(fake).complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
    assert len(fake.calls) == 1


@pytest.mark.criterion("C6")
def test_dated_snapshot_is_accepted_and_priced_as_requested(kit: SimpleNamespace) -> None:
    # oracle: Requirement 6 and MODEL_PRICING["gpt-5"] — 1000 prompt + 200 completion tokens cost
    # 1.0 * 0.00125 + 0.2 * 0.01 = 0.00325 USD; a different model then fails after one call.
    gw = kit.openai_gateway(kit.FakeSDKClient([
        kit.make_response(model="gpt-5-2025-08-07", prompt_tokens=1000, completion_tokens=200)]))
    result = gw.complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
    assert result.model == "gpt-5"
    # tolerance: the same formula, so only float rounding separates the values.
    assert result.cost_usd == pytest.approx(0.00325, rel=1e-12)
    other = kit.FakeSDKClient([kit.make_response(model="gpt-4o")])
    with pytest.raises(FatalChatError):
        kit.openai_gateway(other).complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
    assert len(other.calls) == 1


_ACCEPTED = ["exact", "dated", "empty", "none", "missing"]
_REJECTED = ["mini_dated", "mini", "other", "no_day", "compact", "suffix", "dated_trailer"]


def _served_name(kind: str, day: dt.date, kit: SimpleNamespace) -> Any:
    iso = day.isoformat()
    return {
        "exact": "gpt-5",
        "dated": f"gpt-5-{iso}",
        "empty": "",
        "none": None,
        "missing": kit.ABSENT,
        "mini_dated": f"gpt-5-mini-{iso}",
        "mini": "gpt-5-mini",
        "other": "gpt-4o",
        "no_day": f"gpt-5-{iso[:7]}",
        "compact": f"gpt-5-{iso.replace('-', '')}",
        "suffix": "gpt-5x",
        "dated_trailer": f"gpt-5-{iso}-preview",
    }[kind]


@pytest.mark.criterion("C6")
@given(
    kind=st.sampled_from(_ACCEPTED + _REJECTED),
    day=st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2099, 12, 31)),
    prompt_tokens=st.integers(0, 20_000),
    completion_tokens=st.integers(0, 20_000),
)
def test_served_model_property(kit: SimpleNamespace, kind: str, day: dt.date, prompt_tokens: int,
                               completion_tokens: int) -> None:
    # oracle: Requirement 6 — accepted: requested name, name + "-YYYY-MM-DD", empty or missing; anything
    # else is fatal after one call. Cost is MODEL_PRICING["gpt-5"] applied by hand.
    fake = kit.FakeSDKClient([kit.make_response(model=_served_name(kind, day, kit), prompt_tokens=prompt_tokens,
                                                completion_tokens=completion_tokens)])
    gw = kit.openai_gateway(fake)
    if kind in _REJECTED:
        with pytest.raises(FatalChatError):
            gw.complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
    else:
        result = gw.complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
        assert result.model == "gpt-5"
        # tolerance: the same formula, so only float rounding separates the values.
        assert result.cost_usd == pytest.approx(_hand_cost(prompt_tokens, completion_tokens, kit), rel=1e-12, abs=1e-15)
    assert len(fake.calls) == 1


# ----------------------------------------------------------------------------------------------- C8
@pytest.mark.criterion("C8")
def test_cached_tokens_reported(kit: SimpleNamespace) -> None:
    # oracle: Requirement 8 and MODEL_PRICING["gpt-5"] — 2000 prompt (1500 cached) + 300 completion:
    # 0.5 * 0.00125 + 1.5 * 0.000125 + 0.3 * 0.01 = 0.0038125 USD (cached rate since llm-provider-profiles).
    fake = kit.FakeSDKClient([kit.make_response(prompt_tokens=2000, completion_tokens=300, cached_tokens=1500)])
    result = kit.openai_gateway(fake).complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
    assert getattr(result, "cached_prompt_tokens", None) == 1500
    assert result.prompt_tokens == 2000
    # tolerance: the same formula, so only float rounding separates the values.
    assert result.cost_usd == pytest.approx(0.0038125, rel=1e-12)


@pytest.mark.criterion("C8")
def test_cached_tokens_default_to_zero(kit: SimpleNamespace) -> None:
    # oracle: Requirement 8 — no prompt_tokens_details means cached_prompt_tokens == 0.
    fake = kit.FakeSDKClient([kit.make_response(prompt_tokens=400, completion_tokens=100)])
    result = kit.openai_gateway(fake).complete(messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
    assert getattr(result, "cached_prompt_tokens", None) == 0
    assert result.prompt_tokens == 400


@pytest.mark.criterion("C8")
@given(
    prompt_tokens=st.integers(0, 50_000),
    completion_tokens=st.integers(0, 50_000),
    cached_fraction=st.integers(0, 100),
    has_details=st.booleans(),
)
def test_cached_tokens_property(kit: SimpleNamespace, prompt_tokens: int, completion_tokens: int,
                                cached_fraction: int, has_details: bool) -> None:
    # oracle: Requirement 8 and MODEL_PRICING["gpt-5"] — cached tokens are reported (0 when absent),
    # prompt_tokens includes them, and cost is the cached-rate formula applied by hand.
    cached = prompt_tokens * cached_fraction // 100
    response = (kit.make_response(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                  cached_tokens=cached) if has_details else
                kit.make_response(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens))
    result = kit.openai_gateway(kit.FakeSDKClient([response])).complete(
        messages=[{"role": "user", "content": "q"}], model="gpt-5", stage="s")
    assert getattr(result, "cached_prompt_tokens", None) == (cached if has_details else 0)
    assert result.prompt_tokens == prompt_tokens
    assert result.completion_tokens == completion_tokens
    # tolerance: the same formula, so only float rounding separates the values.
    expected = _hand_cost(prompt_tokens, completion_tokens, kit, cached if has_details else 0)
    assert result.cost_usd == pytest.approx(expected, rel=1e-12, abs=1e-15)
