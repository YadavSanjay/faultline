# Harness Audit — XBRL-Based Flow (2026-07)

**Date:** 2026-07-14
**Scope:** The XBRL-based data flow, audited through the AI-harness-engineering lens
(nine dimensions; see `.claude/skills/ai-harness-engineering`).
**Standard:** rev 2026-07.

---

## Maturity summary

The XBRL flow is **two distinct sub-flows with sharply different maturity**. The
**consumption** sub-flow (ARag → scenariogen dual-RAG → LLM scenario grounding) is
**mostly Solid**: deterministic auditable calibration, fail-loud preflight, a
citation/freshness gate, circuit-breaker + retry transports, a versioned prompt
registry, and Protocol-fakeable clients with real test coverage. The **ingestion**
sub-flow (SEC EDGAR → `edgar_xbrl_facts.csv` / `edgar_bond_filings.csv`) is
**Absent-to-Partial on the harness dimensions that matter most**: zero tests, no
data-quality gate, silent error-swallowing, and — critically — the output is
**unconsumed** by any pricing/risk/scenario code, duplicating facts ARag already
serves. The top P0/P1 gaps both concentrate there: an ingestion path that can
"succeed" with empty/garbage data (the same failure class as the fixed Pensford
degenerate-curve bug), and no independent check of the LLM's qualitative company
legs against the deterministic facts.

---

## The flow, as built

### Sub-flow A — Ingestion (marketdata)

- `src/marketdata/reference/edgar_xbrl_downloader.py` → SEC `companyconcept` API →
  `edgar_xbrl_facts.csv` (7 issuers × 8 us-gaap tags: capex / revenue / leverage /
  coverage / liquidity).
- `src/marketdata/bonds/edgar_downloader.py` → SEC `submissions` API →
  `edgar_bond_filings.csv` (424B5 / 424B2 prospectus index).
- Orchestrated by `src/marketdata/reference/runner.py`; tagged
  **"reference — not a pricing input"** in `ui/bff/app/md_consumption.py:159`.

### Sub-flow B — Consumption (scenariogen dual-RAG + LLM)

ARag is a *separate service/repo* doing its own 10-K/Q XBRL
ingestion, reached two ways:

- **Deterministic REST** — `src/scenariogen/arag/client.py`
  (`facts_lookup` / `narrative_search`) → `src/scenariogen/arag/provider.py` →
  `src/scenariogen/arag/calibrate.py` scales each company leg:
  `scaled_move = seed × clamp(1 + elasticity·(exposure − baseline), min, max)`.
- **LLM / MCP** — `arag__facts_lookup`, `arag__narrative_search` exposed via
  `configs/scenariogen/llm/mcp.json` to the agentic loop in
  `src/scenariogen/llm/phases/evidence.py`.
- **Guardrails** — `src/scenariogen/preflight.py` (fail-loud service readiness),
  `src/scenariogen/arag/gate.py` (citation coverage + fiscal-year freshness),
  circuit-breaker / retry in `RestTransport`.

Downstream: scenario company drivers → materialize → price → StressReport → BFF → UI.

---

## Scored dimensions (XBRL flow)

| # | Dimension | Score | Evidence | Top gap |
|---|-----------|-------|----------|---------|
| 1 | Context & memory | **Partial** | State in CSV/JSON; trace capture externalizes calls | "Where ARag lives" duplicated across arag.json / mcp.json / RAGINFER_URL |
| 2 | Planning | **Solid** | dual-RAG + llm-flow design docs; `expect_tickers`, gate thresholds machine-checkable | — |
| 3 | Generation/execution | **Partial** | Bounded agentic loop; per-phase allowlists | Two access surfaces to one service (REST + MCP) |
| 4 | Evaluation (doer ≠ judge) | **Partial** | Deterministic `AragGate` + preflight on consumption | **Ingestion: 0 tests; LLM legs never cross-checked vs facts** |
| 5 | Deterministic guardrails | **Partial** | breaker, gate, fail-loud preflight | XBRL CSVs absent from `configs/marketdata/dq_gate.json` |
| 6 | Prompt & tool hygiene | **Solid-** | Prompt registry, namespaced tools, Protocol fakes | `arag__facts_compute` MCP tool has no REST twin (drift) |
| 7 | Cost tracking & controls | **Partial** | LLM budgets; per-call 600s ceiling + breaker | No run-level cap/telemetry on ARag calls (shared inference svc) |
| 8 | Autonomy safety & recovery | **Partial** | Fail-loud preflight; trace capture | Ingestion swallows per-request errors → silent empty CSV |
| 9 | Evolution discipline | **Partial** | Decision logs in design docs | Redundant unconsumed XBRL CSV never pruned/wired |

---

## Findings (ranked)

### F1 — P1 · Ingestion XBRL can silently succeed with empty/garbage data

`edgar_xbrl_downloader.py:88-93` swallows every per-`(issuer, tag)` failure as
`INFO`; a systemic fault (SEC 403 on a bad User-Agent, an endpoint move) yields an
empty/partial frame that `upsert_csv` writes without alarm. No entry in
`configs/marketdata/dq_gate.json`, and **no tests exist** (`tests/**/*edgar*` →
none). This is the exact failure class as the fixed Pensford degenerate-curve bug.

- **Fix:** add a "expected ≈ issuers × tags, got 0/N" floor + non-null `val` check;
  register both CSVs in the DQ gate; add a downloader unit test with a fake
  `HttpClient` (200 payload, 403, empty units).
- **Accept:** an all-403 run exits non-zero and does not overwrite the prior CSV.

### F2 — P2 · Two parallel XBRL fact sources; the marketdata one is dead weight

`edgar_xbrl_facts.csv` was built for design §4.8 use-case-2 (bond credit-spread
stress on #15-19, CDS basis #28-29) but is **unconsumed** — grep finds it only in
the downloader, the verify script, and the BFF catalog, never in
pricing/risk/scenariogen. Meanwhile ARag `facts_lookup` serves the
leverage/coverage facts actually used.

- **Fix (Dim 9 prune-or-wire):** either wire the CSV into credit-spread stress
  sizing, or mark it explicitly deprecated in favor of ARag and stop refreshing it.
- **Accept:** a documented decision + a consumer or a removal.

### F3 — P2 · One ARag service, two un-unified integrations

Deterministic REST (`src/scenariogen/arag/client.py`) and MCP
(`configs/scenariogen/llm/mcp.json`) both hit ARag, with separate config, separate
transport, and capability drift (`arag__facts_compute` exists on MCP, not REST).

- **Fix:** define one `AragFacts` interface; make the MCP path a thin adapter over
  the same client, or document why divergence is intentional.
- **Accept:** a single source of truth for ARag capabilities + endpoint.

### F4 — P2 · LLM company legs are never judged against deterministic facts

`evidence.py` `_coerce` silently drops malformed model rows and `_parse` falls back
to raw text on bad JSON; the qualitative company factors the LLM proposes are not
reconciled against `facts_lookup` magnitudes. Doer == judge for the narrative legs.

- **Fix:** add a fresh-context verification step (or extend `AragGate`) asserting
  each LLM company factor cites a covered ticker / fiscal-year and, where a
  calibration fact exists, sits within the clamped multiplier band.
- **Accept:** an unsupported LLM leg is flagged, not silently accepted.

### F5 — P2 · No run-level cost/time ceiling on ARag

The 600s per-call ceiling is per `narrative_search`; N companies × retries ×
scenarios has no aggregate cap, and consumption of the shared inference svc
(`127.0.0.1:8799`) is untracked.

- **Fix:** per-run wall-clock + call-count budget with a breaker; emit ARag
  latency/count telemetry alongside the existing LLM cost telemetry.
- **Accept:** a runaway scenario aborts at a configured budget.

### F6 — P3 · Config fragmentation for ARag location

`base_url` in `configs/scenariogen/arag.json:5` (`localhost:8001`) vs the MCP stdio
subprocess vs `RAGINFER_URL` — plus hardcoded absolute sibling-repo paths
(the ARag repo, now XbrlRag).

- **Fix:** one env-driven ARag config surface; parameterize the repo paths.
- **Accept:** moving ARag requires editing one place.

### F7 — P3 · Cross-repo contract is implicit and unversioned

Flow B depends on ARag + MacroRAG at pinned local paths; ARag's REST models are
parsed leniently, so a schema change degrades to empty results (fail-loud at the
gate — acceptable) but no **contract test** catches the drift early.

- **Fix:** a recorded-fixture contract test against ARag's `/v1` schema.
- **Accept:** an ARag field rename fails a test in this repo.

---

## Positives (keep)

- Deterministic, auditable calibration (`CalibrationResult` records
  fact → exposure → multiplier → citations).
- Fail-loud preflight that refuses silent static-shock fallback.
- The `AragGate` freshness / citation floor.
- Circuit-breaker + Retry-After transports.
- Per-phase tool allowlists.
- Versioned prompt registry.
- Protocol-fakeable clients with real tests on the consumption side.
- Trace capture on every REST / tool call.

---

## Suggested sequencing

1. **P1 — F1** (ingestion DQ floor + tests): highest risk-per-effort; closes a
   degenerate-data hole.
2. **P2 — F4** (LLM-vs-facts verifier), **F5** (cost ceiling), **F3** (unify surfaces).
3. **P2/P3 — F2** (prune-or-wire decision), then **F6/F7** (config + contract test).

---

## Resolution log (2026-07-15)

All seven findings resolved, each shipped to `main` with tests and a green
`ruff + mypy --strict + pytest` gate.

| # | Resolution | Key artifacts |
|---|------------|---------------|
| F1 | Fail-loud ingest floor (`MarketDataIngestError`) in both EDGAR downloaders + client injection for testability; both CSVs registered in the DQ gate | `src/marketdata/common/errors.py`, `edgar_xbrl_downloader.py`, `bonds/edgar_downloader.py`, `configs/marketdata/dq_gate.json`, `tests/marketdata/test_edgar_downloaders.py` |
| F4 | Deterministic company-leg fact-check after the LLM verify: grounding + plausibility-band clamp + coverage/freshness, merged into `verification` | `src/scenariogen/llm/phases/fact_check.py`, `flow.py`, `tests/scenariogen/llm/test_fact_check.py` |
| F5 | Run-level `AragBudget` breaker (call-count + wall-clock) + `scenariogen_arag_*` telemetry; hard-stops the run | `src/scenariogen/arag/budget.py`, `arag/client.py`, `runner.py`, `configs/scenariogen/arag.json`, `tests/scenariogen/test_arag_budget.py` |
| F3 | Single ARag capability catalog both surfaces reference; REST paths derive from it; drift test ties `mcp.json` allowlists to the catalog | `src/scenariogen/arag/capabilities.py`, `tests/scenariogen/test_arag_capabilities.py` |
| F2 | **Decision: deprecate** the standalone `edgar_xbrl_facts` feed in favour of ARag `facts_lookup` (disabled, de-listed from the gate, retained + reversible) | `reference_marketdata.json`, `dq_gate.json`, `md_consumption.py` |
| F6 | Env-overridable service location (`${VAR:-default}` expansion) so relocating ARag/MacroRAG is one environment edit | `src/common/config/env.py`, config loaders, `arag.json` / `macro.json` / `mcp.json`, `tests/.../test_config_env*.py` |
| F7 | Recorded ARag `/v1` contract fixtures + a test pinning the depended-on field names and parsing them through our models | `tests/scenariogen/fixtures/arag/*.json`, `tests/scenariogen/test_arag_contract.py` |

A pre-existing pair of `mypy --strict` errors (`bumps.py`, `lineage.py`, from an
unrelated C1 commit) surfaced during validation and was fixed in a separate
`chore(types)` commit so the tree is fully green.
