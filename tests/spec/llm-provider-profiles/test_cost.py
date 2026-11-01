"""llm-provider-profiles C5-C6: ``cost_usd`` (cache, tier and peak rates) and ``estimate_cost``.

Rates are the spec Notes' design-§3 rows, USD per 1,000 tokens. Every expected cost is the formula of
Requirement 6 worked by hand in the comment next to it:
``(uncached*input + cached*cached_input + written*cache_write + completion*output) / 1000``,
``uncached = prompt - cached - written``. 2026-10-19 is a Monday, 2026-10-24 a Saturday.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.cost import cost_usd, estimate_cost

HAIKU: dict[str, Any] = {
    "input": 0.0001, "output": 0.0005, "cached_input": 0.00001, "cache_write": 0.000125,
    "tiers": [{"above_prompt_tokens": 100000, "input": 0.0005, "output": 0.0025, "cached_input": 0.00005,
               "cache_write": 0.000625}],
}
HAIKU_BASE = {"input": 0.0001, "output": 0.0005, "cached_input": 0.00001, "cache_write": 0.000125}
HAIKU_TIER = {"input": 0.0005, "output": 0.0025, "cached_input": 0.00005, "cache_write": 0.000625}
FLASH: dict[str, Any] = {
    "input": 0.00015, "cached_input": 0.000003, "output": 0.0006,
    "peak": {"weekdays_utc": ["01:00-04:00", "06:00-10:00"], "input": 0.0003, "cached_input": 0.000006,
             "output": 0.0012},
}
FLASH_OFF = {"input": 0.00015, "cached_input": 0.000003, "output": 0.0006}
FLASH_PEAK = {"input": 0.0003, "cached_input": 0.000006, "output": 0.0012}

MONDAY_NOON = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)  # outside both peak windows
REL = 1e-12  # tolerance: a handful of float multiplications and additions; only rounding error remains


def _formula(r: dict[str, float], p: int, c: int, cached: int, written: int) -> float:
    """Requirement 6 written out, with the missing-rate fallback to input."""
    cin = r.get("cached_input", r["input"])
    cw = r.get("cache_write", r["input"])
    return ((p - cached - written) * r["input"] + cached * cin + written * cw + c * r["output"]) / 1000


# ----------------------------------------------------------------------------------------- C5


@pytest.mark.criterion("C5")
def test_c5_base_cached_and_written() -> None:
    # oracle: Requirement 6 by hand, Haiku base rates, p=10000, cached=2000, written=1000, c=500:
    # 7000*0.0001=0.7; 2000*0.00001=0.02; 1000*0.000125=0.125; 500*0.0005=0.25; sum 1.095 / 1000 = 0.001095.
    got = cost_usd(HAIKU, prompt_tokens=10000, completion_tokens=500, cached_prompt_tokens=2000,
                   cache_write_tokens=1000, at=MONDAY_NOON)
    assert got == pytest.approx(0.001095, rel=REL)


@pytest.mark.criterion("C5")
def test_c5_missing_rates_fall_back_to_input() -> None:
    # oracle: Requirement 6 by hand: no cached_input or cache_write -> both priced at input 0.002.
    # p=1000 (400 cached, 100 written), c=50: 1000*0.002=2 + 50*0.008=0.4 -> 2.4 / 1000 = 0.0024.
    row = {"input": 0.002, "output": 0.008}
    got = cost_usd(row, prompt_tokens=1000, completion_tokens=50, cached_prompt_tokens=400,
                   cache_write_tokens=100, at=MONDAY_NOON)
    assert got == pytest.approx(0.0024, rel=REL)
    # Only cache_write missing: 600*0.002=1.2; 300*0.0002=0.06; 100*0.002=0.2; 50*0.008=0.4 -> 1.86 / 1000.
    row = {"input": 0.002, "output": 0.008, "cached_input": 0.0002}
    got = cost_usd(row, prompt_tokens=1000, completion_tokens=50, cached_prompt_tokens=300,
                   cache_write_tokens=100, at=MONDAY_NOON)
    assert got == pytest.approx(0.00186, rel=REL)


@pytest.mark.criterion("C5")
def test_c5_haiku_tier_at_100k() -> None:
    # oracle: Requirement 6 tier rule by hand (tier applies when prompt > 100000):
    # p=100000, c=1000: base 100000*0.0001=10 + 1000*0.0005=0.5 -> 0.0105;
    # p=100001, c=1000: tier 100001*0.0005=50.0005 + 1000*0.0025=2.5 -> 0.0525005;
    # p=150000, cached 50000, written 10000, c=2000: tier 90000*0.0005=45 + 50000*0.00005=2.5
    #   + 10000*0.000625=6.25 + 2000*0.0025=5 -> 58.75 / 1000 = 0.05875.
    kw: dict[str, Any] = {"at": MONDAY_NOON}
    assert cost_usd(HAIKU, prompt_tokens=100000, completion_tokens=1000, cached_prompt_tokens=0,
                    cache_write_tokens=0, **kw) == pytest.approx(0.0105, rel=REL)
    assert cost_usd(HAIKU, prompt_tokens=100001, completion_tokens=1000, cached_prompt_tokens=0,
                    cache_write_tokens=0, **kw) == pytest.approx(0.0525005, rel=REL)
    assert cost_usd(HAIKU, prompt_tokens=150000, completion_tokens=2000, cached_prompt_tokens=50000,
                    cache_write_tokens=10000, **kw) == pytest.approx(0.05875, rel=REL)


@pytest.mark.criterion("C5")
def test_c5_several_tiers_largest_threshold_below() -> None:
    # oracle: Requirement 6: the tier with the largest threshold below prompt_tokens applies. Tiers at 1000
    # (input 0.002, output 0.02) and 5000 (input 0.003, output 0.03), listed out of order; c=100:
    # p=800 base 800*0.001+100*0.01=1.8 -> 0.0018; p=3000 tier 1000: 6+2=8 -> 0.008;
    # p=5000 still tier 1000: 10+2=12 -> 0.012; p=6000 tier 5000: 18+3=21 -> 0.021.
    row = {"input": 0.001, "output": 0.01,
           "tiers": [{"above_prompt_tokens": 5000, "input": 0.003, "output": 0.03},
                     {"above_prompt_tokens": 1000, "input": 0.002, "output": 0.02}]}
    for p, expected in ((800, 0.0018), (3000, 0.008), (5000, 0.012), (6000, 0.021)):
        got = cost_usd(row, prompt_tokens=p, completion_tokens=100, cached_prompt_tokens=0,
                       cache_write_tokens=0, at=MONDAY_NOON)
        assert got == pytest.approx(expected, rel=REL), p


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    ("at", "peak"),
    [
        (datetime(2026, 10, 19, 1, 0, tzinfo=UTC), True),    # Monday 01:00, window start inclusive
        (datetime(2026, 10, 19, 4, 0, tzinfo=UTC), False),   # Monday 04:00, window end exclusive
        (datetime(2026, 10, 19, 5, 0, tzinfo=UTC), False),   # between the windows
        (datetime(2026, 10, 19, 9, 59, tzinfo=UTC), True),   # Monday 09:59
        (datetime(2026, 10, 19, 10, 0, tzinfo=UTC), False),  # Monday 10:00
        (datetime(2026, 10, 23, 7, 0, tzinfo=UTC), True),    # Friday 07:00
        (datetime(2026, 10, 24, 2, 0, tzinfo=UTC), False),   # Saturday 02:00
        (datetime(2026, 10, 25, 7, 0, tzinfo=UTC), False),   # Sunday 07:00
    ],
)
def test_c5_deepseek_peak_windows(at: datetime, peak: bool) -> None:
    # oracle: Requirement 6 peak rule by hand, p=10000 (4000 cached), c=1000:
    # off-peak 6000*0.00015=0.9 + 4000*0.000003=0.012 + 1000*0.0006=0.6 -> 1.512 / 1000 = 0.001512;
    # peak     6000*0.0003=1.8  + 4000*0.000006=0.024 + 1000*0.0012=1.2 -> 3.024 / 1000 = 0.003024.
    expected = 0.003024 if peak else 0.001512
    got = cost_usd(FLASH, prompt_tokens=10000, completion_tokens=1000, cached_prompt_tokens=4000,
                   cache_write_tokens=0, at=at)
    assert got == pytest.approx(expected, rel=REL)


@pytest.mark.criterion("C5")
def test_c5_non_utc_at_is_converted() -> None:
    # oracle: Requirement 6: `at` is converted to UTC. Monday 11:30 at +09:00 = Monday 02:30 UTC (peak, though
    # 11:30 local is outside the windows); Monday 23:00 at -03:00 = Tuesday 02:00 UTC (peak); Monday 07:00 at
    # +09:00 = Sunday 22:00 UTC (off-peak, though 07:00 local is inside a window). Costs as in the peak test.
    cases = [
        (datetime(2026, 10, 19, 11, 30, tzinfo=timezone(timedelta(hours=9))), 0.003024),
        (datetime(2026, 10, 19, 23, 0, tzinfo=timezone(timedelta(hours=-3))), 0.003024),
        (datetime(2026, 10, 19, 7, 0, tzinfo=timezone(timedelta(hours=9))), 0.001512),
    ]
    for at, expected in cases:
        got = cost_usd(FLASH, prompt_tokens=10000, completion_tokens=1000, cached_prompt_tokens=4000,
                       cache_write_tokens=0, at=at)
        assert got == pytest.approx(expected, rel=REL), at


@pytest.mark.criterion("C5")
def test_c5_invalid_inputs_raise() -> None:
    # oracle: Requirement 6: a naive `at`, or cached + written > prompt, raises ValueError; the valid
    # neighbour (cached + written == prompt) prices as 600*0.00001 + 400*0.000125 = 0.056 -> 0.000056.
    with pytest.raises(ValueError, match=r"\S"):  # any non-empty message; the spec fixes none
        cost_usd(HAIKU, prompt_tokens=1000, completion_tokens=10, cached_prompt_tokens=0,
                 cache_write_tokens=0, at=MONDAY_NOON.replace(tzinfo=None))
    with pytest.raises(ValueError, match=r"\S"):  # any non-empty message; the spec fixes none
        cost_usd(HAIKU, prompt_tokens=1000, completion_tokens=0, cached_prompt_tokens=600,
                 cache_write_tokens=401, at=MONDAY_NOON)
    got = cost_usd(HAIKU, prompt_tokens=1000, completion_tokens=0, cached_prompt_tokens=600,
                   cache_write_tokens=400, at=MONDAY_NOON)
    assert got == pytest.approx(0.000056, rel=REL)


_TOKENS = st.integers(min_value=0, max_value=2_000_000)


@pytest.mark.criterion("C5")
@given(p=_TOKENS, c=_TOKENS, cached_frac=st.floats(0, 1), written_frac=st.floats(0, 1))
def test_c5_property_formula_and_tier(p: int, c: int, cached_frac: float, written_frac: float) -> None:
    # oracle: Requirement 6 formula evaluated in the test; Haiku base rates when p <= 100000, tier rates above.
    cached = int(p * cached_frac)
    written = int((p - cached) * written_frac)
    rates = HAIKU_TIER if p > 100000 else HAIKU_BASE
    got = cost_usd(HAIKU, prompt_tokens=p, completion_tokens=c, cached_prompt_tokens=cached,
                   cache_write_tokens=written, at=MONDAY_NOON)
    assert got == pytest.approx(_formula(rates, p, c, cached, written), rel=1e-9, abs=1e-15)  # rounding only


@pytest.mark.criterion("C5")
@given(minute_of_week=st.integers(min_value=0, max_value=7 * 24 * 60 - 1),
       offset_min=st.integers(min_value=-14 * 60, max_value=14 * 60))
def test_c5_property_peak_by_utc_minute(minute_of_week: int, offset_min: int) -> None:
    # oracle: Requirement 6 peak rule by hand: Monday-Friday (UTC) and minute-of-day in [60, 240) or
    # [360, 600) is peak. `at` is the same UTC instant expressed in a random fixed offset.
    day, minute = divmod(minute_of_week, 24 * 60)
    utc = datetime(2026, 10, 19, tzinfo=UTC) + timedelta(days=day, minutes=minute)  # day 0 = Monday
    at = utc.astimezone(timezone(timedelta(minutes=offset_min)))
    peak = day < 5 and (60 <= minute < 240 or 360 <= minute < 600)
    got = cost_usd(FLASH, prompt_tokens=10000, completion_tokens=1000, cached_prompt_tokens=4000,
                   cache_write_tokens=0, at=at)
    assert got == pytest.approx(_formula(FLASH_PEAK if peak else FLASH_OFF, 10000, 1000, 4000, 0), rel=REL)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(p=_TOKENS, c=_TOKENS, cached_frac=st.floats(0, 1), written_frac=st.floats(0, 1))
def test_c5_metamorphic_fallback_equals_explicit_input(p: int, c: int, cached_frac: float,
                                                       written_frac: float) -> None:
    # oracle: Requirement 6: a row without cached_input/cache_write prices exactly like the same row with both
    # set to its input rate; and that equals the formula with every prompt token at input.
    cached = int(p * cached_frac)
    written = int((p - cached) * written_frac)
    bare = {"input": 0.0004, "output": 0.0016}
    full = {"input": 0.0004, "output": 0.0016, "cached_input": 0.0004, "cache_write": 0.0004}
    kw: dict[str, Any] = {"prompt_tokens": p, "completion_tokens": c, "cached_prompt_tokens": cached,
                          "cache_write_tokens": written, "at": MONDAY_NOON}
    expected = (p * 0.0004 + c * 0.0016) / 1000
    # tolerance: rounding error only; the absolute floor covers the all-zero case.
    assert cost_usd(bare, **kw) == pytest.approx(cost_usd(full, **kw), rel=1e-12, abs=1e-15)
    assert cost_usd(bare, **kw) == pytest.approx(expected, rel=1e-9, abs=1e-15)


# ----------------------------------------------------------------------------------------- C6

PRICING = {"gpt-5": {"input": 0.00125, "output": 0.01}, "gpt-5-mini": {"input": 0.00025, "output": 0.002}}


@pytest.mark.criterion("C6")
def test_c6_unknown_model_raises_known_model_priced() -> None:
    # oracle: Requirement 7: no row -> ValueError naming the model; gpt-5 p=2000, c=300:
    # 2000/1000*0.00125 = 0.0025 + 300/1000*0.01 = 0.003 -> 0.0055.
    with pytest.raises(ValueError, match="claude-haiku-5-5"):
        estimate_cost("claude-haiku-5-5", 1000, 100, PRICING)
    assert estimate_cost("gpt-5", 2000, 300, PRICING) == pytest.approx(0.0055, rel=REL)


@pytest.mark.criterion("C6")
def test_c6_unknown_model_raises_with_default_table() -> None:
    # oracle: Requirement 7: the default rate card has no row for an invented model -> ValueError naming it.
    with pytest.raises(ValueError, match="no-such-model-x"):
        estimate_cost("no-such-model-x", 1000, 100)
    with pytest.raises(ValueError, match="no-such-model-x"):
        estimate_cost("no-such-model-x", 0, 0, PRICING)


@pytest.mark.criterion("C6")
@given(p=_TOKENS, c=_TOKENS, model=st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=12))
def test_c6_property_known_priced_unknown_raises(p: int, c: int, model: str) -> None:
    # oracle: Requirement 7: a known model costs p/1000*input + c/1000*output (gpt-5-mini 0.00025 / 0.002);
    # any model without a row raises ValueError instead of returning 0.
    assert estimate_cost("gpt-5-mini", p, c, PRICING) == pytest.approx(
        p / 1000 * 0.00025 + c / 1000 * 0.002, rel=1e-12, abs=1e-15)  # rounding only
    unknown = "x-" + model
    with pytest.raises(ValueError, match=re.escape(unknown)):
        estimate_cost(unknown, p, c, PRICING)
