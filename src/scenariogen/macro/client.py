"""MacroRAG HTTP client (S4).

A thin client over MacroRAG's REST API. The transport sits behind a `MacroTransport`
Protocol so the whole integration is testable offline with a fake (mirroring how MacroRAG
itself is built with fakes — no network). `RequestsTransport` is the real HTTP backing
(API-key auth via the `X-API-Key` header, matching MacroRAG's `auth` dependency).

Endpoints used (MacroRAG `src/api/app.py`):
  GET  /healthz              liveness + index manifest
  POST /query                cited answer; with the synthesis handoff -> shock vector
  GET  /citations/{id}       a cited chunk's text + provenance
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from ..trace.capture import record_rest_call
from .models import MacroAnswer, MacroChunk, MacroFilter, MacroSynthesis

logger = logging.getLogger("scenariogen.macro.client")

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class MacroTransportError(RuntimeError):
    """A MacroRAG request failed after retries, or the circuit breaker is open."""


class MacroTransport(Protocol):
    """Minimal HTTP surface MacroRAGClient needs; fakeable in tests."""

    def get(self, path: str) -> dict[str, Any]: ...
    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class RequestsTransport:
    """`requests`-backed transport with retry/backoff, Retry-After, and a circuit breaker.

    Transient failures (429 / 5xx / network) are retried with exponential backoff (429 honours
    the ``Retry-After`` header). After `breaker_threshold` consecutive failures the breaker
    opens and further calls fail fast — so a down MacroRAG isn't hammered once per scenario.
    The heavy `requests` import is deferred to construction; `http` is injectable for tests.
    """

    def __init__(self, base_url: str, api_key: str | None = None, timeout_s: float = 30.0,
                 *, max_attempts: int = 3, backoff_s: float = 1.0, backoff_max_s: float = 16.0,
                 breaker_threshold: int = 5, http: Any = None):
        if http is None:
            import importlib

            # `requests` ships no type stubs; an Any handle keeps the dependency deferred
            # AND avoids stub-resolution nondeterminism in the strict gate.
            http = importlib.import_module("requests")
        self._http: Any = http
        self._exc: type[BaseException] = http.exceptions.RequestException
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.backoff_max_s = backoff_max_s
        self.breaker_threshold = breaker_threshold
        self._consecutive_failures = 0
        self._open = False

    def get(self, path: str) -> dict[str, Any]:
        return self._traced("get", path, None)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._traced("post", path, payload)

    def _traced(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Run the request and record it into the active call-trace (P3; no-op outside a run)."""
        start = time.perf_counter()
        try:
            resp = (self._request(method, path, json=payload) if payload is not None
                    else self._request(method, path))
        except Exception as exc:
            record_rest_call(service="macrorag", purpose=path, ok=False,
                             latency_ms=(time.perf_counter() - start) * 1000.0,
                             request=payload, error=str(exc))
            raise
        record_rest_call(service="macrorag", purpose=path, ok=True,
                         latency_ms=(time.perf_counter() - start) * 1000.0,
                         request=payload, response=resp)
        return resp

    # -- internals ----------------------------------------------------------- #
    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        if self._open:
            raise MacroTransportError("MacroRAG circuit breaker open (too many failures)")
        url = f"{self.base_url}{path}"
        fn = getattr(self._http, method)
        wait = self.backoff_s
        for attempt in range(1, self.max_attempts + 1):
            last = attempt == self.max_attempts
            try:
                resp = fn(url, headers=self.headers, timeout=self.timeout_s, **kw)
            except self._exc as exc:
                if last:
                    self._record_failure()
                    raise MacroTransportError(f"network error after {attempt} attempts: {exc}") from exc
                time.sleep(wait)
                wait = min(wait * 2, self.backoff_max_s)
                continue

            if resp.status_code in _RETRYABLE_STATUS and not last:
                delay = self._retry_after(resp) if resp.status_code == 429 else wait
                logger.warning("MacroRAG %s %s -> HTTP %s (retry in %.1fs, attempt %d/%d)",
                               method.upper(), url, resp.status_code, delay, attempt, self.max_attempts)
                time.sleep(delay)
                wait = min(wait * 2, self.backoff_max_s)
                continue

            try:
                resp.raise_for_status()
            except Exception as exc:
                self._record_failure()
                raise MacroTransportError(f"HTTP {resp.status_code} for {url}") from exc

            self._consecutive_failures = 0      # success closes the breaker
            return dict(resp.json())

        self._record_failure()
        raise MacroTransportError(f"exhausted {self.max_attempts} attempts for {url}")

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.breaker_threshold and not self._open:
            self._open = True
            logger.error("MacroRAG circuit breaker OPEN after %d consecutive failures",
                         self._consecutive_failures)

    def _retry_after(self, resp: Any) -> float:
        try:
            return min(float(resp.headers.get("Retry-After", self.backoff_s)), self.backoff_max_s)
        except (TypeError, ValueError):
            return self.backoff_s


class MacroRAGClient:
    def __init__(self, transport: MacroTransport):
        self.transport = transport

    def healthz(self) -> dict[str, Any]:
        return self.transport.get("/healthz")

    def query(self, question: str, filters: MacroFilter | None = None) -> MacroAnswer:
        return MacroAnswer.from_response(self.transport.post("/query", self._body(question, filters)))

    def synthesize(self, question: str, filters: MacroFilter | None = None, *,
                   horizon_days: int | None = None, fields: list[str] | None = None,
                   directions: dict[str, str] | None = None, no_cache: bool = False) -> MacroSynthesis:
        """Request the structured shock-vector handoff (catalog §9B / §11.3).

        Sends ``mode="synthesize"`` and parses the optional ``shock_vector`` block; when the
        response carries none, the result is unavailable and the caller falls back.

        Args:
            question: The scenario synthesis question.
            filters: Facet filters narrowing MacroRAG retrieval.
            horizon_days: The scenario horizon in trading days, sent as
                ``synthesis.horizon_days`` so MacroRAG pins moves to it and drops fields whose
                evidence horizon does not match. None omits it.
            fields: The shock-vector fields wanted, sent as ``synthesis.fields`` (MacroRAG
                emits only these). None or empty omits it.
            directions: Field -> ``"up"`` | ``"down"`` under MacroRAG's ``positive_means``
                convention, sent as ``synthesis.directions``; MacroRAG flags a field whose sign
                contradicts it ``direction_mismatch``. None or empty omits it.
            no_cache: When true, sends ``"no_cache": true`` at the top level of the body so
                MacroRAG neither reads nor writes its answer caches (stability runs). False omits
                the key, leaving the body byte-identical to a request without the flag.

        Returns:
            The parsed synthesis.
        """
        body = self._body(question, filters)
        body["mode"] = "synthesize"
        options: dict[str, Any] = {}
        if horizon_days is not None:
            options["horizon_days"] = horizon_days
        if fields:
            options["fields"] = list(fields)
        if directions:
            options["directions"] = dict(directions)
        if options:
            body["synthesis"] = options
        if no_cache:
            body["no_cache"] = True
        return MacroSynthesis.from_response(self.transport.post("/query", body))

    def citation(self, chunk_id: str) -> MacroChunk:
        return MacroChunk.from_dict(self.transport.get(f"/citations/{chunk_id}"))

    @staticmethod
    def _body(question: str, filters: MacroFilter | None) -> dict[str, Any]:
        body: dict[str, Any] = {"question": question}
        if filters is not None:
            body["filters"] = filters.to_payload()
        return body
