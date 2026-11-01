"""C5: client exceptions are classified as `OpenAIChatBackend` does (spec Requirement 7)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from src.scenariogen.llm.anthropic_backend import AnthropicChatBackend
from src.scenariogen.llm.gateway import ChatBackendError, FatalChatError, TransientChatError

_MSG = [{"role": "user", "content": "q"}]
_TRANSIENT_STATUS = {408, 409, 425, 429}


def _raised(kit: SimpleNamespace, exc: BaseException) -> ChatBackendError:
    """The classified error `create` raises when the client's `messages.stream` raises `exc`."""
    fake = kit.FakeAnthropicClient([exc])
    backend = AnthropicChatBackend(client=fake, models=kit.MODELS)
    with pytest.raises(ChatBackendError) as info:
        backend.create(model=kit.SONNET, messages=_MSG)
    assert len(fake.calls) == 1
    return info.value


@pytest.mark.criterion("C5")
def test_rate_limit_with_retry_after(kit: SimpleNamespace) -> None:
    # oracle: Requirement 7 — RateLimitError is transient by name; retry-after "2" -> retry_after_s 2.0.
    err = _raised(kit, kit.RateLimitError({"retry-after": "2"}))
    assert isinstance(err, TransientChatError)
    # tolerance: "2" parses to exactly 2.0.
    assert err.retry_after_s == pytest.approx(2.0, rel=0, abs=0)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("status", [529, 500])
def test_overloaded_and_server_errors_are_transient(kit: SimpleNamespace, status: int) -> None:
    # oracle: Requirement 7 — status >= 500 is transient (Anthropic's 529 "overloaded" included); no
    # retry-after header -> retry_after_s None.
    err = _raised(kit, kit.StatusError(status))
    assert isinstance(err, TransientChatError)
    assert err.retry_after_s is None


@pytest.mark.criterion("C5")
def test_bad_request_is_fatal(kit: SimpleNamespace) -> None:
    # oracle: Requirement 7 — status 400 is not in the transient set.
    err = _raised(kit, kit.StatusError(400))
    assert isinstance(err, FatalChatError)
    assert not isinstance(err, TransientChatError)


@pytest.mark.criterion("C5")
def test_plain_value_error_is_fatal(kit: SimpleNamespace) -> None:
    # oracle: Requirement 7 — "everything else is fatal".
    err = _raised(kit, ValueError("bad"))
    assert isinstance(err, FatalChatError)


@pytest.mark.criterion("C5")
@given(status=st.integers(100, 599),
       retry_after=st.one_of(st.none(), st.integers(0, 120)))
def test_status_classification_property(kit: SimpleNamespace, status: int, retry_after: int | None) -> None:
    # oracle: Requirement 7 — transient iff status in {408, 409, 425, 429} or >= 500, carrying a numeric
    # retry-after header as seconds (None without one); otherwise fatal.
    headers = {} if retry_after is None else {"retry-after": str(retry_after)}
    err = _raised(kit, kit.StatusError(status, headers))
    if status in _TRANSIENT_STATUS or status >= 500:
        assert isinstance(err, TransientChatError)
        if retry_after is None:
            assert err.retry_after_s is None
        else:
            # tolerance: an integer header parses to exactly that float.
            assert err.retry_after_s == pytest.approx(float(retry_after), rel=0, abs=0)
    else:
        assert isinstance(err, FatalChatError)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
def test_class_name_alone_makes_transient(kit: SimpleNamespace) -> None:
    # oracle: Requirement 7 — the same status-400 error is fatal under a neutral class name but
    # transient when its class is named like an SDK transient error (classification by class name).
    transient_names = ["APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"]
    assert isinstance(_raised(kit, kit.StatusError(400)), FatalChatError)
    for name in transient_names:
        renamed = type(name, (kit.StatusError,), {})(400)
        assert isinstance(_raised(kit, renamed), TransientChatError), name
