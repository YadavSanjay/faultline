"""Service preflight for the dual-RAG named-scenario flow (decision 2: fail loudly).

Before any named scenario is generated/priced, the RAG services a run *requires* must be up.
This module probes MacroRAG and ARag liveness and, if a **required** service is unreachable,
raises ``ServiceUnavailableError`` so the run aborts up front rather than silently falling back
to authored/static shocks. (This is the deliberate reversal of the old macro path's silent
fallback.) Which services a given scenario requires is declared per-scenario in config; the
runner (P1) unions those into the ``required`` set passed here.

The heavy generation-time timeout (10 min per ARag ``narrative_search`` call) is enforced by
the client transport, not here — the preflight is a *quick* readiness probe and should be given
health-tuned (short-timeout) clients. A required service that is up here but a call that later
exceeds the 10-min ceiling is logged/documented and stops that one scenario (handled in P1).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from .arag.models import AragHealth

logger = logging.getLogger("scenariogen.preflight")

MACRO = "macro"
ARAG = "arag"


class _MacroHealthClient(Protocol):
    def healthz(self) -> dict[str, Any]: ...


class _AragHealthClient(Protocol):
    def health(self) -> AragHealth: ...


@dataclass
class ServiceHealth:
    """The outcome of one service's readiness probe."""

    name: str
    ok: bool
    required: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "required": self.required, "detail": self.detail}


class ServiceUnavailableError(RuntimeError):
    """One or more *required* RAG services failed the preflight probe (fail-loud)."""

    def __init__(self, failures: list[ServiceHealth]):
        self.failures = failures
        names = ", ".join(f"{f.name} ({f.detail})" for f in failures)
        super().__init__(
            f"required scenariogen service(s) unavailable: {names}. "
            "Start the service(s) and retry, or drop them from the run's required set."
        )


def _probe_macro(client: _MacroHealthClient, *, required: bool) -> ServiceHealth:
    try:
        resp = client.healthz()
    except Exception as exc:  # network/HTTP/breaker — treat as down
        return ServiceHealth(MACRO, ok=False, required=required, detail=f"probe failed: {exc}")
    status = str(resp.get("status", "")).lower()
    ok = status == "ok"
    return ServiceHealth(MACRO, ok=ok, required=required,
                         detail="ok" if ok else f"status={status or 'missing'}")


def _probe_arag(client: _AragHealthClient, *, required: bool) -> ServiceHealth:
    try:
        h = client.health()
    except Exception as exc:
        return ServiceHealth(ARAG, ok=False, required=required, detail=f"probe failed: {exc}")
    if h.ok:
        return ServiceHealth(ARAG, ok=True, required=required, detail="ok")
    detail = f"status={h.status or 'missing'}, pipelines={h.pipelines}"
    return ServiceHealth(ARAG, ok=False, required=required, detail=detail)


def preflight_services(
    required: Iterable[str],
    *,
    macro_client: _MacroHealthClient | None = None,
    arag_client: _AragHealthClient | None = None,
) -> dict[str, ServiceHealth]:
    """Probe MacroRAG/ARag liveness; raise if any *required* service is unavailable.

    ``required`` names the services the run cannot proceed without (subset of
    ``{"macro", "arag"}``). A service in ``required`` with no client wired, or whose probe
    fails, is a hard failure. Non-required services are still probed and reported (so the UI
    can show "arag: down, not required") but never block the run.

    Returns a ``{name: ServiceHealth}`` map on success; raises ``ServiceUnavailableError``
    listing every required service that is down.
    """
    req = {s.strip().lower() for s in required}
    results: dict[str, ServiceHealth] = {}

    # MacroRAG
    if macro_client is not None:
        results[MACRO] = _probe_macro(macro_client, required=MACRO in req)
    elif MACRO in req:
        results[MACRO] = ServiceHealth(MACRO, ok=False, required=True, detail="no client wired")

    # ARag
    if arag_client is not None:
        results[ARAG] = _probe_arag(arag_client, required=ARAG in req)
    elif ARAG in req:
        results[ARAG] = ServiceHealth(ARAG, ok=False, required=True, detail="no client wired")

    for h in results.values():
        level = logging.INFO if h.ok else (logging.ERROR if h.required else logging.WARNING)
        logger.log(level, "preflight %s: %s%s", h.name, "ok" if h.ok else "DOWN",
                   "" if h.ok else f" ({h.detail}{'' if h.required else ', not required'})")

    failures = [h for h in results.values() if h.required and not h.ok]
    if failures:
        raise ServiceUnavailableError(failures)
    return results
