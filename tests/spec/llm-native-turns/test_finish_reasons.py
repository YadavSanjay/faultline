"""C5: finish-reason handling in `InstrumentedChatClient.complete` over a fake `ChatBackend`."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.gateway import FatalChatError, TransientChatError

_ISR = "insufficient_system_resource"
_MSG = [{"role": "user", "content": "q"}]


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("reason", ["refusal", "aborted"])
def test_refusal_and_aborted_are_fatal_without_retry(kit: SimpleNamespace, reason: str) -> None:
    # oracle: Requirement 5 (D6) — refusal / aborted raise FatalChatError after exactly one backend call.
    backend = kit.FakeChatBackend([kit.raw(reason), kit.raw("stop")])
    with pytest.raises(FatalChatError):
        kit.fake_backend_gateway(backend).complete(messages=_MSG, model=kit.MODEL, stage="s")
    assert backend.n_calls == 1


@pytest.mark.criterion("C5")
def test_one_insufficient_resource_then_stop_retries_once(kit: SimpleNamespace) -> None:
    # oracle: Requirement 5 — insufficient_system_resource is retried like a transient error:
    # one failure + one success = 2 attempts, 1 retry.
    backend = kit.FakeChatBackend([kit.raw(_ISR, content="bad"), kit.raw("stop", content="good")])
    result = kit.fake_backend_gateway(backend).complete(messages=_MSG, model=kit.MODEL, stage="s")
    assert result.attempts == 2
    assert result.retries == 1
    assert result.finish_reason == "stop"
    assert result.content == "good"
    assert backend.n_calls == 2


@pytest.mark.criterion("C5")
def test_insufficient_resource_every_attempt_is_transient(kit: SimpleNamespace) -> None:
    # oracle: Requirement 5 — when every one of max_attempts=3 attempts returns it, TransientChatError.
    backend = kit.FakeChatBackend([kit.raw(_ISR)])
    with pytest.raises(TransientChatError):
        kit.fake_backend_gateway(backend, max_attempts=3).complete(messages=_MSG, model=kit.MODEL, stage="s")
    assert backend.n_calls == 3


@pytest.mark.criterion("C5")
@given(max_attempts=st.integers(1, 5), n_isr=st.integers(0, 6))
def test_insufficient_resource_retry_property(kit: SimpleNamespace, max_attempts: int, n_isr: int) -> None:
    # oracle: Requirement 5 — n_isr failures then stop: success with attempts n_isr+1 and retries n_isr
    # while n_isr < max_attempts, else TransientChatError after exactly max_attempts calls.
    backend = kit.FakeChatBackend([kit.raw(_ISR)] * n_isr + [kit.raw("stop", content="good")])
    gw = kit.fake_backend_gateway(backend, max_attempts=max_attempts)
    if n_isr < max_attempts:
        result = gw.complete(messages=_MSG, model=kit.MODEL, stage="s")
        assert result.attempts == n_isr + 1
        assert result.retries == n_isr
        assert result.content == "good"
        assert backend.n_calls == n_isr + 1
    else:
        with pytest.raises(TransientChatError):
            gw.complete(messages=_MSG, model=kit.MODEL, stage="s")
        assert backend.n_calls == max_attempts


@pytest.mark.criterion("C5")
@given(reason=st.sampled_from(["refusal", "aborted", "content_filter"]), max_attempts=st.integers(1, 5))
def test_fatal_reasons_property(kit: SimpleNamespace, reason: str, max_attempts: int) -> None:
    # oracle: Requirement 5 — refusal, aborted and content_filter are all fatal after one call,
    # whatever the attempt limit.
    backend = kit.FakeChatBackend([kit.raw(reason), kit.raw("stop")])
    with pytest.raises(FatalChatError):
        kit.fake_backend_gateway(backend, max_attempts=max_attempts).complete(messages=_MSG, model=kit.MODEL,
                                                                              stage="s")
    assert backend.n_calls == 1
