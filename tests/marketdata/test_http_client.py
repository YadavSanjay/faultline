"""HttpClient: auth application, retry/backoff, and error handling — no network.

Drives the client through an injected fake session so retry, throttling, and auth-header
construction are exercised deterministically.
"""

from __future__ import annotations

import pytest
import requests
from src.marketdata.common.http_client import HttpClient, HttpClientError


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self):
        return self._json


class FakeSession:
    """Records each request and returns/raises the next queued item."""

    def __init__(self, queued):
        self.queued = list(queued)
        self.calls: list[dict] = []
        self.headers: dict = {}

    def request(self, method, url, params, headers, json, timeout):
        self.calls.append({"method": method, "url": url,
                           "params": dict(params), "headers": dict(headers)})
        item = self.queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make retry/backoff instant."""
    monkeypatch.setattr("src.marketdata.common.http_client.time.sleep", lambda _s: None)


def _client(session, auth=None, creds=None):
    cfg = {"base_url": "https://example.test", "rate_limit_delay_s": 0,
           "retry": {"max_attempts": 3, "wait_min_s": 0.0, "wait_max_s": 0.0}}
    if auth is not None:
        cfg["auth"] = auth
    return HttpClient(cfg, credentials=creds or {}, session=session)


def test_auth_api_key_param_adds_param():
    sess = FakeSession([FakeResponse(200)])
    c = _client(sess, auth={"type": "api_key_param", "key_env": "K", "param_name": "key"},
                creds={"K": "secret"})
    c.get("/data")
    assert sess.calls[0]["params"]["key"] == "secret"


def test_auth_bearer_sets_header():
    sess = FakeSession([FakeResponse(200)])
    c = _client(sess, auth={"type": "bearer", "key_env": "TOKEN"}, creds={"TOKEN": "abc"})
    c.get("/data")
    assert sess.calls[0]["headers"]["Authorization"] == "Bearer abc"


def test_missing_key_omits_param():
    sess = FakeSession([FakeResponse(200)])
    c = _client(sess, auth={"type": "api_key_param", "key_env": "K", "param_name": "key"},
                creds={})  # no credential
    c.get("/data")
    assert "key" not in sess.calls[0]["params"]


def test_retry_then_success():
    sess = FakeSession([FakeResponse(503), FakeResponse(200, text="ok")])
    resp = _client(sess).get("/data")
    assert resp.status_code == 200
    assert len(sess.calls) == 2


def test_retry_exhausted_raises():
    sess = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    with pytest.raises(HttpClientError):
        _client(sess).get("/data")
    assert len(sess.calls) == 3


def test_4xx_raises_without_retry():
    sess = FakeSession([FakeResponse(404, text="nope")])
    with pytest.raises(HttpClientError):
        _client(sess).get("/data")
    assert len(sess.calls) == 1  # client errors are not retried


def test_network_error_then_success():
    sess = FakeSession([requests.ConnectionError("boom"), FakeResponse(200)])
    resp = _client(sess).get("/data")
    assert resp.status_code == 200
    assert len(sess.calls) == 2


def _session_client(auth, creds):
    """A client that builds its own requests.Session (headers only; no request is sent)."""
    cfg = {"base_url": "https://example.test", "auth": auth}
    return HttpClient(cfg, credentials=creds)


def test_user_agent_carries_contact_email():
    c = _session_client({"type": "user_agent_only"}, {"FAULTLINE_CONTACT_EMAIL": "ops@example.test"})
    assert c._session.headers["User-Agent"] == "Faultline/1.0 ops@example.test"


def test_user_agent_only_source_requires_contact_email():
    with pytest.raises(HttpClientError, match="FAULTLINE_CONTACT_EMAIL"):
        _session_client({"type": "user_agent_only"}, {})


def test_keyless_source_falls_back_to_anonymous_user_agent():
    c = _session_client({"type": "none"}, {})
    assert c._session.headers["User-Agent"] == "Faultline/1.0"


def test_extra_headers_still_override_user_agent():
    cfg = {"base_url": "https://example.test", "auth": {"type": "none"},
           "extra_headers": {"User-Agent": "Mozilla/5.0"}}
    assert HttpClient(cfg, credentials={})._session.headers["User-Agent"] == "Mozilla/5.0"
