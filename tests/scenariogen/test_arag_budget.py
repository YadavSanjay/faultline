"""Run-level ARag budget + breaker (harness-audit F5).

Unit-tests AragBudget.check/charge semantics and that RestTransport enforces the budget:
the call that crosses a ceiling raises AragBudgetError (a hard-stop, not an AragTransportError,
so the per-scenario fail-loud handler cannot swallow it).
"""

from __future__ import annotations

from typing import Any

import pytest
from src.scenariogen.arag.budget import AragBudget, AragBudgetError
from src.scenariogen.arag.client import AragTransportError, RestTransport


class _NetErr(Exception):
    pass


class _FakeExceptions:
    RequestException = _NetErr


class _FakeResp:
    def __init__(self, status: int, json_data: Any = None):
        self.status_code = status
        self._json = json_data if json_data is not None else {"ok": 1}
        self.headers: dict = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _NetErr(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._json


class _FakeHttp:
    exceptions = _FakeExceptions

    def __init__(self, factory):
        self._factory = factory
        self.calls = 0

    def get(self, url: str, headers: dict, timeout: float, **kw: Any) -> Any:
        self.calls += 1
        return self._factory()

    post = get


# --------------------------------------------------------------------------- #
# AragBudget unit
# --------------------------------------------------------------------------- #

def test_unlimited_budget_never_raises():
    b = AragBudget()  # no ceilings
    for _ in range(100):
        b.check()
        b.charge(5.0)
    assert b.calls == 100


def _use(b: AragBudget, latency: float = 1.0) -> None:
    b.check()
    b.charge(latency)


def test_call_count_ceiling():
    b = AragBudget(max_calls=2)
    _use(b)
    _use(b)
    with pytest.raises(AragBudgetError, match="call budget"):
        b.check()              # 3rd blocked


def test_wall_clock_ceiling():
    b = AragBudget(max_wall_clock_s=10.0)
    _use(b, 6.0)
    _use(b, 6.0)               # cumulative 12 >= 10
    with pytest.raises(AragBudgetError, match="wall-clock budget"):
        b.check()


def test_error_is_not_a_transport_error():
    # Must NOT be swallowed by the provider's `except AragTransportError` -> it hard-stops the run.
    assert not issubclass(AragBudgetError, AragTransportError)


# --------------------------------------------------------------------------- #
# RestTransport enforcement
# --------------------------------------------------------------------------- #

def test_transport_enforces_call_budget():
    http = _FakeHttp(lambda: _FakeResp(200))
    budget = AragBudget(max_calls=2)
    t = RestTransport("http://arag.test", http=http, budget=budget)
    assert t.get("/api/health") == {"ok": 1}
    assert t.get("/api/health") == {"ok": 1}
    with pytest.raises(AragBudgetError):
        t.get("/api/health")            # 3rd call blocked before hitting the wire
    assert http.calls == 2              # the blocked call never reached the fake http
    assert budget.calls == 2


def test_transport_charges_even_on_failure():
    # A failed call still consumes the service, so it must count against the budget.
    http = _FakeHttp(lambda: _FakeResp(404))
    budget = AragBudget(max_calls=5)
    t = RestTransport("http://arag.test", http=http, budget=budget, max_attempts=1)
    with pytest.raises(AragTransportError):
        t.get("/api/v1/coverage")
    assert budget.calls == 1
