"""C1: `openai_client_kwargs` builds the SDK client kwargs with one retry policy (spec Requirement 1)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.gateway import openai_client_kwargs

_optional_text = st.one_of(st.none(), st.text(max_size=12))
_valid_timeouts = st.floats(min_value=0.0, exclude_min=True, allow_nan=False, allow_infinity=False)
_invalid_timeouts = st.one_of(
    st.floats(max_value=0.0, allow_nan=False),  # zero, negatives and -inf
    st.just(math.nan),
    st.just(math.inf),
)


@pytest.mark.criterion("C1")
def test_all_arguments_given() -> None:
    # oracle: Requirement 1 / C1 — max_retries=0, timeout=t, plus api_key and base_url when not None.
    out = openai_client_kwargs(api_key="sk-test", base_url="https://llm.example/v1", timeout_s=42.5)
    assert set(out) == {"max_retries", "timeout", "api_key", "base_url"}
    assert out["max_retries"] == 0
    assert out["api_key"] == "sk-test"
    assert out["base_url"] == "https://llm.example/v1"
    # tolerance: the timeout is passed through unchanged, so it must be exact.
    assert out["timeout"] == pytest.approx(42.5, rel=0, abs=0)


@pytest.mark.criterion("C1")
def test_none_key_and_url_are_left_out() -> None:
    # oracle: Requirement 1 / C1 — api_key and base_url are included only when not None.
    out = openai_client_kwargs(api_key=None, base_url=None, timeout_s=600.0)
    assert set(out) == {"max_retries", "timeout"}
    assert out["max_retries"] == 0
    # tolerance: the timeout is passed through unchanged, so it must be exact.
    assert out["timeout"] == pytest.approx(600.0, rel=0, abs=0)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("timeout_s", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_timeout_raises(timeout_s: float) -> None:
    # oracle: Requirement 1 / C1 — a timeout that is <= 0, NaN or infinite raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # any non-empty message; the spec fixes none
        openai_client_kwargs(api_key="sk-test", base_url=None, timeout_s=timeout_s)


@pytest.mark.criterion("C1")
@given(api_key=_optional_text, base_url=_optional_text, timeout_s=_valid_timeouts)
def test_kwargs_property(api_key: str | None, base_url: str | None, timeout_s: float) -> None:
    # oracle: Requirement 1 / C1 — the expected dict is built here from the rule, key by key.
    expected_keys = {"max_retries", "timeout"}
    if api_key is not None:
        expected_keys.add("api_key")
    if base_url is not None:
        expected_keys.add("base_url")
    out = openai_client_kwargs(api_key=api_key, base_url=base_url, timeout_s=timeout_s)
    assert set(out) == expected_keys
    assert out["max_retries"] == 0
    # tolerance: the timeout is passed through unchanged, so it must be exact.
    assert out["timeout"] == pytest.approx(timeout_s, rel=0, abs=0)
    if api_key is not None:
        assert out["api_key"] == api_key
    if base_url is not None:
        assert out["base_url"] == base_url


@pytest.mark.criterion("C1")
@given(api_key=_optional_text, base_url=_optional_text, timeout_s=_invalid_timeouts)
def test_invalid_timeout_property(api_key: str | None, base_url: str | None, timeout_s: float) -> None:
    # oracle: Requirement 1 / C1 — every timeout that is not a finite number > 0 raises ValueError.
    with pytest.raises(ValueError, match=r"\S"):  # any non-empty message; the spec fixes none
        openai_client_kwargs(api_key=api_key, base_url=base_url, timeout_s=timeout_s)
