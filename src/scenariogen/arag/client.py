"""ARag HTTP client (dual-RAG P0).

A thin client over ARag's REST v1 Analyst API. The transport sits behind an ``AragTransport``
Protocol so the whole integration is testable offline with a fake (mirroring ``macro/client``
and how ARag itself is built with fakes — no network). ``RestTransport`` is the real HTTP
backing, with the same retry/backoff/circuit-breaker resilience as the macro transport.

Two timeouts, per the dual-RAG design's fail-loud policy:
  * ``health_timeout_s`` — short (readiness probe must be quick), used by the preflight.
  * ``timeout_s`` — **generous, default 600s (10 min)** for the heavy ``narrative_search``
    call, which runs CPU inference on the shared service. A call that still exceeds it raises
    ``AragTransportError``; the caller (P1 provider) logs/documents it and stops that
    scenario's generation rather than proceeding with no company evidence.

Endpoints used (ARag ``src/api``):
  GET  /api/health                 liveness (audit DB + retrieval pipelines importable)
  GET  /api/v1/coverage            ingested filers / fiscal-years / forms
  POST /api/v1/narrative/search    qualitative Risk-Factor chunks
  POST /api/v1/facts/lookup        deterministic cited fact series
  POST /api/v1/answer              full synthesized pipeline (optional)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

from src.common.obs import metrics as obs_metrics

from ..trace.capture import record_rest_call
from .budget import AragBudget
from .capabilities import AragPaths
from .models import AragCoverage, AragFactSeries, AragHealth, AragNarrativeResult

logger = logging.getLogger("scenariogen.arag.client")

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Run-level ARag telemetry (F5): call count by endpoint/outcome + latency of the whole call
# (including retries). Paired with AragBudget so a runaway loop is both visible and bounded.
SCEN_ARAG_CALLS = obs_metrics.counter(
    "scenariogen", "arag_calls_total", "ARag REST calls by endpoint and outcome", ("purpose", "ok"))
SCEN_ARAG_LATENCY = obs_metrics.histogram(
    "scenariogen", "arag_call_latency_seconds", "ARag REST call latency incl. retries", ("purpose",))

# The generous per-call ceiling for ARag's heavy narrative retrieval (dual-RAG design §1,
# decision 2): 10 minutes. Beyond this the run stops the specific scenario, fail-loud.
MAX_CALL_TIMEOUT_S = 600.0


class AragTransportError(RuntimeError):
    """An ARag request failed after retries, or the circuit breaker is open."""


class AragTransport(Protocol):
    """Minimal HTTP surface AragClient needs; fakeable in tests."""

    def get(self, path: str) -> dict[str, Any]: ...
    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class RestTransport:
    """``requests``-backed transport with retry/backoff, Retry-After, and a circuit breaker.

    Structurally identical to ``macro.client.RequestsTransport`` (transient 429/5xx/network
    failures retried with exponential backoff; breaker opens after ``breaker_threshold``
    consecutive failures) but header-configurable — ARag needs no API key by default, so
    ``api_key`` is optional. The heavy ``requests`` import is deferred to construction;
    ``http`` is injectable for tests.
    """

    def __init__(self, base_url: str, api_key: str | None = None,
                 timeout_s: float = MAX_CALL_TIMEOUT_S, *, max_attempts: int = 2,
                 backoff_s: float = 2.0, backoff_max_s: float = 30.0,
                 breaker_threshold: int = 3, budget: AragBudget | None = None, http: Any = None):
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
        self._budget = budget
        self._consecutive_failures = 0
        self._open = False

    def get(self, path: str) -> dict[str, Any]:
        return self._traced("get", path, None)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._traced("post", path, payload)

    def _traced(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        """Run the request; enforce the run budget, emit telemetry, record the call-trace (P3)."""
        if self._budget is not None:
            self._budget.check()  # AragBudgetError here hard-stops the run (before consuming ARag)
        start = time.perf_counter()
        try:
            resp = (self._request(method, path, json=payload) if payload is not None
                    else self._request(method, path))
        except Exception as exc:
            self._after_call(path, start, ok=False)
            record_rest_call(service="arag", purpose=path, ok=False,
                             latency_ms=(time.perf_counter() - start) * 1000.0,
                             request=payload, error=str(exc))
            raise
        self._after_call(path, start, ok=True)
        record_rest_call(service="arag", purpose=path, ok=True,
                         latency_ms=(time.perf_counter() - start) * 1000.0,
                         request=payload, response=resp)
        return resp

    def _after_call(self, path: str, start: float, *, ok: bool) -> None:
        """Charge the run budget and emit call/latency telemetry for one completed request."""
        latency_s = time.perf_counter() - start
        if self._budget is not None:
            self._budget.charge(latency_s)
        SCEN_ARAG_LATENCY.labels(purpose=path).observe(latency_s)
        SCEN_ARAG_CALLS.labels(purpose=path, ok="true" if ok else "false").inc()

    # -- internals ----------------------------------------------------------- #
    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        if self._open:
            raise AragTransportError("ARag circuit breaker open (too many failures)")
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
                    raise AragTransportError(f"network error after {attempt} attempts: {exc}") from exc
                time.sleep(wait)
                wait = min(wait * 2, self.backoff_max_s)
                continue

            if resp.status_code in _RETRYABLE_STATUS and not last:
                delay = self._retry_after(resp) if resp.status_code == 429 else wait
                logger.warning("ARag %s %s -> HTTP %s (retry in %.1fs, attempt %d/%d)",
                               method.upper(), url, resp.status_code, delay, attempt, self.max_attempts)
                time.sleep(delay)
                wait = min(wait * 2, self.backoff_max_s)
                continue

            try:
                resp.raise_for_status()
            except Exception as exc:
                self._record_failure()
                raise AragTransportError(f"HTTP {resp.status_code} for {url}") from exc

            self._consecutive_failures = 0      # success closes the breaker
            return dict(resp.json())

        self._record_failure()
        raise AragTransportError(f"exhausted {self.max_attempts} attempts for {url}")

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.breaker_threshold and not self._open:
            self._open = True
            logger.error("ARag circuit breaker OPEN after %d consecutive failures",
                         self._consecutive_failures)

    def _retry_after(self, resp: Any) -> float:
        try:
            return min(float(resp.headers.get("Retry-After", self.backoff_s)), self.backoff_max_s)
        except (TypeError, ValueError):
            return self.backoff_s


class AragClient:
    def __init__(self, transport: AragTransport, *, as_of: str | None = None):
        self.transport = transport
        # Point-in-time fact reads (scenario-event-replay): XbrlRag returns each value as it stood
        # in the latest filing available on this ISO date. None = latest (today's behaviour).
        self.as_of = as_of

    def health(self) -> AragHealth:
        return AragHealth.from_response(self.transport.get(AragPaths.HEALTH))

    def coverage(self) -> AragCoverage:
        return AragCoverage.from_response(self.transport.get(AragPaths.COVERAGE))

    def narrative_search(self, company: str, query: str, *, section_hint: str | None = "Risk Factors",
                         fiscal_years: list[int] | None = None, top_k: int = 8) -> AragNarrativeResult:
        """Retrieve qualitative Risk-Factor / MD&A chunks for a company (no synthesis).

        This is the heavy CPU-inference call; the transport's generous ``timeout_s`` (10 min
        default) bounds it, and a timeout surfaces as ``AragTransportError``.
        """
        body: dict[str, Any] = {"company": company, "query": query, "top_k": top_k}
        if section_hint:
            body["section_hint"] = section_hint
        if fiscal_years:
            body["fiscal_years"] = fiscal_years
        return AragNarrativeResult.from_response(self.transport.post(AragPaths.NARRATIVE_SEARCH, body))

    def facts_lookup(self, company: str, concept: str, fiscal_years: list[int], *,
                     dimension: dict[str, str] | None = None,
                     fiscal_period: str = "FY") -> AragFactSeries:
        """Deterministic cited fact series (for company-leg magnitude calibration in P1)."""
        body: dict[str, Any] = {"company": company, "concept": concept,
                                "fiscal_years": fiscal_years, "fiscal_period": fiscal_period}
        if dimension:
            body["dimension"] = dimension
        if self.as_of:
            body["as_of"] = self.as_of
        return AragFactSeries.from_response(self.transport.post(AragPaths.FACTS_LOOKUP, body))

    def answer(self, query: str) -> dict[str, Any]:
        """Full synthesized ARag pipeline (prose). Used only for narrative grounding in P2."""
        return self.transport.post(AragPaths.ANSWER, {"query": query})
