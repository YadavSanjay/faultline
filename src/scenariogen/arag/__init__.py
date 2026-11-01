"""ARag integration (dual-RAG P0): company-specific risk-factor shocks from SEC 10-K/Q.

Mirrors the ``macro/`` subpackage but sources the **company legs** of a named scenario
(per-name ``eq:<TICKER>`` idiosyncratic moves) from ARag's SEC-filings RAG rather than the
macro legs from MacroRAG. Talks to ARag over its REST API (``/api/v1/*``); the whole
integration is offline-testable behind an ``AragTransport`` Protocol.

Layers (each mirrors its ``macro/`` counterpart):
  client.py    RestTransport (retry/backoff/breaker) + AragClient (health/coverage/
               narrative_search/facts_lookup/answer)
  models.py    ARag REST request/response dataclasses
  mapping.py   company risk factors -> canonical eq:<TICKER> legs (AragCompanyRisk)
  gate.py      freshness (filing fiscal-year) + citation-coverage GATE
"""

from __future__ import annotations
