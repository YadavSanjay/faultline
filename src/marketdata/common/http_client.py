"""
Generic REST client for the market-data pipeline.

Handles:
  - Auth types: none | user_agent_only | api_key_param | api_key_header | bearer
  - Exponential-backoff retry on transient HTTP errors (429, 5xx)
  - Per-source rate limiting (delay between successive calls)
  - Configurable timeout
  - Returns raw requests.Response — callers do .json() / .text / .content

Config dict schema (subset of a source entry from *_marketdata.json):
  base_url          str   e.g. "https://markets.newyorkfed.org"
  auth              dict  {type, key_env?, param_name?, header_name?}
  timeout_s         int   default 30
  rate_limit_delay_s float default 0.3
  retry             dict  {max_attempts, wait_min_s, wait_max_s}
  extra_headers     dict  optional static headers merged into every request

User-Agent: "Faultline/1.0 <contact email>" when FAULTLINE_CONTACT_EMAIL is in the credentials, else
"Faultline/1.0". A `user_agent_only` source (SEC, GPR, EPU) identifies itself by that contact, so
building a client for one without the email raises instead of sending an anonymous request.
"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

CONTACT_EMAIL_KEY = "FAULTLINE_CONTACT_EMAIL"

_BASE_HEADERS = {
    "Accept": "application/json, text/csv, application/octet-stream, */*",
}

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpClientError(Exception):
    """Raised when a request fails after all retries."""


def user_agent(credentials: dict[str, str], *, require_contact: bool = False) -> str:
    """Build the pipeline's User-Agent, carrying the operator's contact email when configured.

    Args:
        credentials: The downloaders' credentials dict; the email is read from its
            FAULTLINE_CONTACT_EMAIL entry.
        require_contact: Raise rather than fall back to an anonymous User-Agent.

    Returns:
        "Faultline/1.0 <email>", or "Faultline/1.0" when no email is configured.

    Raises:
        HttpClientError: If require_contact is set and no email is configured.
    """
    email = credentials.get(CONTACT_EMAIL_KEY, "")
    if email:
        return f"Faultline/1.0 {email}"
    if require_contact:
        raise HttpClientError(
            f"set {CONTACT_EMAIL_KEY} in .env: this source requires a contact email in the User-Agent")
    return "Faultline/1.0"


class HttpClient:
    """
    Session-based HTTP client with retry, auth, and throttling.

    Instantiate once per source (one source = one base_url + auth config).
    Thread-safety: not guaranteed — use one instance per thread.
    """

    def __init__(self, source_config: dict, credentials: dict[str, str] | None = None,
                 session: requests.Session | None = None):
        self._cfg = source_config
        self._creds = credentials or {}
        self._base_url = source_config["base_url"].rstrip("/")
        self._timeout = int(source_config.get("timeout_s", 30))
        self._delay = float(source_config.get("rate_limit_delay_s", 0.3))

        retry_cfg = source_config.get("retry", {})
        self._max_attempts = int(retry_cfg.get("max_attempts", 3))
        self._wait_min = float(retry_cfg.get("wait_min_s", 1.0))
        self._wait_max = float(retry_cfg.get("wait_max_s", 16.0))

        auth_type = source_config.get("auth", {}).get("type", "none")
        self._user_agent = user_agent(self._creds, require_contact=auth_type == "user_agent_only")

        # `session` is injectable so tests can drive retry/auth/throttle logic without network.
        self._session = session if session is not None \
            else self._build_session(source_config.get("extra_headers", {}))
        self._last_call_ts: float = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(
        self,
        endpoint: str,
        params: dict | None = None,
        extra_headers: dict | None = None,
    ) -> requests.Response:
        return self._request("GET", endpoint, params=params or {}, extra_headers=extra_headers or {})

    def post(
        self,
        endpoint: str,
        body: Any = None,
        params: dict | None = None,
    ) -> requests.Response:
        return self._request("POST", endpoint, params=params or {}, body=body)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_session(self, extra_headers: dict) -> requests.Session:
        s = requests.Session()
        s.headers.update(_BASE_HEADERS)
        s.headers["User-Agent"] = self._user_agent
        if extra_headers:
            s.headers.update(extra_headers)
        return s

    def _apply_auth(self, params: dict, headers: dict) -> tuple[dict, dict]:
        auth = self._cfg.get("auth", {"type": "none"})
        kind = auth.get("type", "none")

        if kind in ("none", "user_agent_only"):
            pass  # User-Agent already in session headers

        elif kind == "api_key_param":
            val = self._creds.get(auth.get("key_env", ""), "")
            name = auth.get("param_name", "api_key")
            if val:
                params[name] = val
            else:
                logger.warning("api_key_param: env var %r not set", auth.get("key_env"))

        elif kind == "api_key_header":
            val = self._creds.get(auth.get("key_env", ""), "")
            name = auth.get("header_name", "X-Api-Key")
            if val:
                headers[name] = val
            else:
                logger.warning("api_key_header: env var %r not set", auth.get("key_env"))

        elif kind == "bearer":
            val = self._creds.get(auth.get("key_env", ""), "")
            if val:
                headers["Authorization"] = f"Bearer {val}"
            else:
                logger.warning("bearer: env var %r not set", auth.get("key_env"))

        else:
            logger.warning("Unknown auth type %r — proceeding without auth", kind)

        return params, headers

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_ts
        gap = self._delay - elapsed
        if gap > 0:
            time.sleep(gap)
        self._last_call_ts = time.monotonic()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict,
        extra_headers: dict | None = None,
        body: Any = None,
    ) -> requests.Response:
        url = f"{self._base_url}{endpoint}"
        merged_params = dict(params)
        merged_headers = dict(extra_headers or {})
        merged_params, merged_headers = self._apply_auth(merged_params, merged_headers)

        wait = self._wait_min
        for attempt in range(1, self._max_attempts + 1):
            self._throttle()
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    params=merged_params,
                    headers=merged_headers,
                    json=body if method == "POST" else None,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                logger.warning("Attempt %d/%d — network error: %s", attempt, self._max_attempts, exc)
                if attempt == self._max_attempts:
                    raise HttpClientError(f"Network error after {attempt} attempts: {exc}") from exc
                time.sleep(wait)
                wait = min(wait * 2, self._wait_max)
                continue

            if resp.status_code in _RETRYABLE_STATUS:
                logger.warning(
                    "Attempt %d/%d — HTTP %s %s (retryable)",
                    attempt, self._max_attempts, resp.status_code, url,
                )
                if attempt == self._max_attempts:
                    raise HttpClientError(f"HTTP {resp.status_code} after {attempt} attempts: {url}")
                time.sleep(wait)
                wait = min(wait * 2, self._wait_max)
                continue

            if not resp.ok:
                raise HttpClientError(
                    f"HTTP {resp.status_code} {url}: {resp.text[:300]}"
                )

            logger.debug("HTTP %s %s %s (attempt %d)", method, resp.status_code, url, attempt)
            return resp

        raise HttpClientError(f"Exhausted {self._max_attempts} attempts for {url}")
