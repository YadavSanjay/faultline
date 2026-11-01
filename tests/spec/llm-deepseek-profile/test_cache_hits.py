"""llm-deepseek-profile C3-C4: DeepSeek cache hits are counted and priced at ``cached_input``.

C3 drives ``OpenAIChatBackend(client=<fake>)`` with SDK-shaped usage objects. C4 prices a call through
``InstrumentedChatClient`` over the DeepSeek backend built from the committed config (fake SDK), with
``CostTracker(config.pricing)`` and a fixed clock. Rates are the spec Notes' committed rows, USD per
1,000 tokens; every expected cost is worked by hand next to it as
``(misses*input + hits*cached_input + completion*output) / 1000``. 2026-10-19 is a Monday,
2026-10-24 a Saturday; peak windows are 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.cost import CostTracker
from src.scenariogen.llm.gateway import InstrumentedChatClient, OpenAIChatBackend
from src.scenariogen.llm.profiles import LLMConfig, build_provider_backend

MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
REL = 1e-12  # tolerance: a handful of float multiplications and additions; only rounding error remains

FLASH_OFF = {"input": 0.00015, "cached_input": 0.000003, "output": 0.0006}
FLASH_PEAK = {"input": 0.0003, "cached_input": 0.000006, "output": 0.0012}
PRO_OFF = {"input": 0.00066, "cached_input": 0.000022, "output": 0.00198}
PRO_PEAK = {"input": 0.00132, "cached_input": 0.000044, "output": 0.00396}

MONDAY_PEAK = datetime(2026, 10, 19, 2, 0, tzinfo=UTC)
MONDAY_OFF = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _deepseek_usage(prompt: int, hits: int, completion: int) -> dict[str, Any]:
    """DeepSeek-shaped usage: hits + misses == prompt_tokens, no prompt_tokens_details (spec C3)."""
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "prompt_cache_hit_tokens": hits, "prompt_cache_miss_tokens": prompt - hits}


def _backend_create(sdk: SimpleNamespace, usage: dict[str, Any]) -> Any:
    client = sdk.FakeOpenAIClient(sdk.Recorder(usage))
    return OpenAIChatBackend(client=client).create(model="deepseek-flash", messages=MESSAGES)


# ----------------------------------------------------------------------------------------- C3


@pytest.mark.criterion("C3")
def test_c3_prompt_cache_hit_tokens_are_counted(sdk: SimpleNamespace) -> None:
    # oracle: Requirement 3 + live evidence (Notes): usage P=10000, hits H=4000, misses 6000, no
    # prompt_tokens_details -> cached_prompt_tokens == 4000, prompt_tokens == 10000 (unchanged).
    raw = _backend_create(sdk, _deepseek_usage(10000, 4000, 1000))
    assert raw.cached_prompt_tokens == 4000
    assert raw.prompt_tokens == 10000
    assert raw.completion_tokens == 1000


@pytest.mark.criterion("C3")
def test_c3_precedence_details_then_hits_then_zero(sdk: SimpleNamespace) -> None:
    # oracle: Requirement 3 precedence, by hand:
    # details.cached_tokens=700 with H=50 -> 700 (details win);
    # details.cached_tokens=0 with H=50 -> 0 (present and not None still wins);
    # details.cached_tokens=None with H=50 -> 50; no details attribute with H=50 -> 50;
    # neither field -> 0.
    base = {"prompt_tokens": 1000, "completion_tokens": 10}
    with_details = {**base, "prompt_tokens_details": SimpleNamespace(cached_tokens=700), "prompt_cache_hit_tokens": 50}
    assert _backend_create(sdk, with_details).cached_prompt_tokens == 700
    zero_details = {**base, "prompt_tokens_details": SimpleNamespace(cached_tokens=0), "prompt_cache_hit_tokens": 50}
    assert _backend_create(sdk, zero_details).cached_prompt_tokens == 0
    none_details = {**base, "prompt_tokens_details": SimpleNamespace(cached_tokens=None),
                    "prompt_cache_hit_tokens": 50}
    assert _backend_create(sdk, none_details).cached_prompt_tokens == 50
    no_details = {**base, "prompt_cache_hit_tokens": 50}
    assert _backend_create(sdk, no_details).cached_prompt_tokens == 50
    assert _backend_create(sdk, dict(base)).cached_prompt_tokens == 0
    for usage in (with_details, zero_details, none_details, no_details, base):
        assert _backend_create(sdk, usage).prompt_tokens == 1000


@pytest.mark.criterion("C3")
@given(prompt=st.integers(min_value=1, max_value=2_000_000), data=st.data())
def test_c3_property_hits_pass_through_and_prompt_is_unchanged(sdk: SimpleNamespace, prompt: int,
                                                               data: st.DataObject) -> None:
    # oracle: Requirement 3: for any P and 0 < H <= P with no prompt_tokens_details,
    # cached_prompt_tokens == H and prompt_tokens == P; adding details with cached_tokens=None
    # changes nothing (H still used).
    hits = data.draw(st.integers(min_value=1, max_value=prompt), label="hits")
    usage = _deepseek_usage(prompt, hits, 7)
    raw = _backend_create(sdk, usage)
    assert (raw.cached_prompt_tokens, raw.prompt_tokens) == (hits, prompt)
    raw_none = _backend_create(sdk, {**usage, "prompt_tokens_details": SimpleNamespace(cached_tokens=None)})
    assert (raw_none.cached_prompt_tokens, raw_none.prompt_tokens) == (hits, prompt)


# ----------------------------------------------------------------------------------------- C4


def _priced_call(config: LLMConfig, model: str, at: datetime) -> Any:
    """One gateway call over the real DeepSeek backend; the fake SDK must already be installed."""
    backend = build_provider_backend(config, "deepseek", {"DEEPSEEK_API_KEY": "k"})
    client = InstrumentedChatClient(backend, CostTracker(config.pricing), clock=lambda: at)
    return client.complete(messages=MESSAGES, model=model, stage="discover")


@pytest.mark.criterion("C4")
def test_c4_flash_and_pro_peak_and_off_peak(committed_config: Callable[[str], LLMConfig], sdk: SimpleNamespace,
                                            install_fake_openai: Callable[[Any], None]) -> None:
    # oracle: spec C4 + Notes rates, by hand; usage P=10000 (H=4000, misses 6000), completion 1000.
    # flash peak (Mon 02:00):  (6000*0.0003 + 4000*0.000006 + 1000*0.0012) / 1000
    #                        = (1.8 + 0.024 + 1.2) / 1000 = 0.003024
    # flash off  (Mon 12:00):  (6000*0.00015 + 4000*0.000003 + 1000*0.0006) / 1000
    #                        = (0.9 + 0.012 + 0.6) / 1000 = 0.001512
    # pro peak:  (6000*0.00132 + 4000*0.000044 + 1000*0.00396) / 1000 = (7.92 + 0.176 + 3.96) / 1000 = 0.012056
    # pro off:   (6000*0.00066 + 4000*0.000022 + 1000*0.00198) / 1000 = (3.96 + 0.088 + 1.98) / 1000 = 0.006028
    config = committed_config("deepseek")
    recorder = sdk.Recorder(_deepseek_usage(10000, 4000, 1000))
    install_fake_openai(recorder)
    cases = (("deepseek-flash", MONDAY_PEAK, 0.003024), ("deepseek-flash", MONDAY_OFF, 0.001512),
             ("deepseek-v4-pro", MONDAY_PEAK, 0.012056), ("deepseek-v4-pro", MONDAY_OFF, 0.006028))
    for model, at, expected in cases:
        result = _priced_call(config, model, at)
        assert result.cached_prompt_tokens == 4000
        assert result.prompt_tokens == 10000
        assert result.cost_usd == pytest.approx(expected, rel=REL), (model, at)


@pytest.mark.criterion("C4")
@given(hits=st.integers(min_value=1, max_value=10000),
       model=st.sampled_from(("deepseek-flash", "deepseek-v4-pro")),
       peak_offset_min=st.sampled_from((61, 120, 179, 361, 480, 599)),
       off_offset_min=st.sampled_from((1, 241, 359, 601, 720, 1439)),
       weekday=st.integers(min_value=0, max_value=4))
def test_c4_property_cost_follows_the_formula_at_both_rates(
        committed_config: Callable[[str], LLMConfig], sdk: SimpleNamespace, hits: int, model: str,
        peak_offset_min: int, off_offset_min: int, weekday: int) -> None:
    # oracle: Requirement 4 formula with the Notes' rows: cost = ((P-H)*input + H*cached_input
    # + C*output) / 1000 at peak rates inside 01:00-04:00 / 06:00-10:00 Mon-Fri and off-peak rates
    # elsewhere (offsets are minutes after midnight, clear of window edges), and at a Saturday time.
    config = committed_config("deepseek")
    prompt, completion = 10000, 1000
    off, peak = (FLASH_OFF, FLASH_PEAK) if model == "deepseek-flash" else (PRO_OFF, PRO_PEAK)

    def expected(r: dict[str, float]) -> float:
        return ((prompt - hits) * r["input"] + hits * r["cached_input"] + completion * r["output"]) / 1000

    day = datetime(2026, 10, 19, tzinfo=UTC) + timedelta(days=weekday)
    saturday = datetime(2026, 10, 24, tzinfo=UTC) + timedelta(minutes=peak_offset_min)
    recorder = sdk.Recorder(_deepseek_usage(prompt, hits, completion))
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, "openai", sdk.fake_openai_module(recorder))
        at_peak = _priced_call(config, model, day + timedelta(minutes=peak_offset_min))
        at_off = _priced_call(config, model, day + timedelta(minutes=off_offset_min))
        at_sat = _priced_call(config, model, saturday)
    assert at_peak.cost_usd == pytest.approx(expected(peak), rel=REL)
    assert at_off.cost_usd == pytest.approx(expected(off), rel=REL)
    assert at_sat.cost_usd == pytest.approx(expected(off), rel=REL)
    # The committed peak rows are exactly double the off-peak rows, so peak cost is twice off-peak.
    assert at_peak.cost_usd == pytest.approx(2 * at_off.cost_usd, rel=1e-9)  # tolerance: rounding only
