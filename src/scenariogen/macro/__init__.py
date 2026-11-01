"""MacroRAG integration (S4) — narrative/policy driver sourcing for stress scenarios.

MacroRAG (the macro sub-system, a separate repo) supplies the *direction,
magnitude, trigger and citations* of a scenario's macro **drivers** — the layer the
numeric pipeline can't produce (design §4C, catalog §9B). This package is the AITrade-side
client + handoff:

  models.py    request/response dataclasses mirroring MacroRAG's live REST API, plus the
               agreed structured shock-vector handoff contract
  client.py    transport interface + requests-backed HTTP client + MacroRAGClient
  mapping.py   MacroRAG shock-vector fields -> canonical factor keys (+ unit conversion)
  gate.py      freshness + citation-coverage GATE on macro chunks (catalog §10)
  provider.py  MacroScenarioProvider: resolve a scenario's drivers from MacroRAG, with a
               graceful fallback to the static configs/scenariogen/scenarios.json drivers

The synthesis (shock-vector-with-citations) capability is a future MacroRAG build item
(catalog §11.3); until it ships, `MacroRAGClient.synthesize` returns nothing and the
provider falls back to the authored static drivers, so the stress run always works.
"""

from __future__ import annotations

from .client import MacroRAGClient, MacroTransport, RequestsTransport
from .gate import MacroGate, MacroGateResult
from .mapping import MacroDriver, to_canonical_drivers
from .models import MacroAnswer, MacroChunk, MacroFilter, MacroShockField, MacroSynthesis
from .provider import DriverResolution, MacroScenarioProvider

__all__ = [
    "DriverResolution",
    "MacroAnswer",
    "MacroChunk",
    "MacroDriver",
    "MacroFilter",
    "MacroGate",
    "MacroGateResult",
    "MacroRAGClient",
    "MacroScenarioProvider",
    "MacroShockField",
    "MacroSynthesis",
    "MacroTransport",
    "RequestsTransport",
    "to_canonical_drivers",
]
