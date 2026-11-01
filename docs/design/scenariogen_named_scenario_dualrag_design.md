# Named-Scenario Dual-RAG Enrichment + Call Tracing — Design

**Status:** Approved for implementation (analysis + decisions locked 2026-07-12)
**Scope:** the four **named** stress scenarios only — `taiwan_geopolitical`, `ai_bust`,
`rate_capital`, `keyman_nvda`. (AI-generated scenarios in `src/scenariogen/llm/` are out of
scope here and will be cleaned up / regenerated in a later exercise.)
**Modules:** `src/scenariogen/` (classic path) · `src/scenariogen/macro/` · new
`src/scenariogen/arag/` · new `src/scenariogen/trace/` · `ui/bff/app/scenarios.py` ·
`ui/app/src/features/scenarios/`
**External services:** MacroRAG REST (`http://localhost:8000`) · ARag REST
(`http://localhost:8001`, `/api/v1/*`) · OpenAI gpt-5 (narrative only) · RagInference (`:8799`)
**Reference tracing architecture:** MacroRAG (`src/llm/usage.py` `CallRecord` →
`src/utils/trace.py` JSONL → `src/api/admin.py` `/admin/traces|metrics` → Trace Explorer UI)

---

## 1. Goal

Make the four **named** scenarios genuinely two-service scenarios: given the portfolio and a
simple worded scenario, **assimilate shocks from both MacroRAG (macro risk factors) and ARag
(company-specific risk factors from SEC 10-K/Q filings)** into one governed, correlated shock
vector, then price + measure risk with the existing engine. Every scenario summary must
**document what extra information each service contributed**. Every MacroRAG / ARag / LLM call
must be **captured (request/response/prompt/cost/latency) and surfaced in the UI**.

### Locked decisions

1. **Deterministic dual-RAG enrichment** (not LLM-calibrated shocks). Authored baseline legs in
   `scenarios.json` remain the governed starting point; MacroRAG supplies macro legs, ARag
   supplies company legs, both with per-leg attribution. The covariance engine, plausibility
   gate, versioning and sign-off are unchanged. The LLM (gpt-5) is used **only** to write the
   value-add narrative, never to set numbers.
2. **Fail loudly.** A health preflight probes both services before any pricing. If a service a
   scenario *requires* is unreachable the run **aborts with a clear error** rather than silently
   substituting static shocks. (Today the classic path silently falls back — that is removed for
   required services.) Per-call ceiling is a **generous 10 minutes (600s)** — the shared
   inference service on `:8799` should keep ARag well under this, but if a call still exceeds it,
   the run **logs + documents it and stops that specific scenario's generation** (not the whole
   run); other scenarios continue.

**Scenario-set authoring (added requirement).** The pipeline is re-writable: a fresh set of
hypothetical named scenarios can be authored (name + worded trigger/narrative + target
companies + macro/company queries + a seed baseline) and driven end-to-end through the same
dual-RAG process. Config is the authorable home — `scenarios.json` (baseline drivers/overlays +
narrative/trigger), `macro.json` (per-scenario macro synthesis question), `arag.json`
(per-scenario company Risk-Factor queries + `required_services`). See §4a.
3. **Full Trace Explorer.** Persist raw request/response + prompts per call to JSONL (capture
   toggle + truncation, à la MacroRAG `CallRecord`), add BFF trace/metrics endpoints, and build
   an FE waterfall with per-call request/response/prompt drill-down, per-call cost + latency, and
   a scenario-level total cost.

---

## 2. Current state (why this work is needed)

- The four named scenarios are **hand-authored static** driver vectors in
  `configs/scenariogen/scenarios.json`, expanded through the conditional-covariance engine
  (`covariance.py` → `scenario_gen.py`).
- MacroRAG is **optional and macro-only**: `--macro` → `macro/provider.py` overrides only the
  macro legs via REST `POST /query?mode=synthesize`; on any failure it **silently** falls back
  to static. Per-name equity legs (which dominate `ai_bust`/`keyman_nvda`/`taiwan_geopolitical`)
  are **never** service-sourced.
- **ARag is not wired into the named-scenario path at all.** It exists only in the separate
  agentic `src/scenariogen/llm/` flow (MCP + gpt-5), which generates its own scenarios.
- **No liveness probe** on the MCP path; **no explicit OpenAI client timeout**; MCP
  `call_timeout_s` code-default (90s) disagrees with config (300s).
- **No raw request/response/prompt persistence** anywhere — the in-memory transcript is
  discarded; `RunTrace` keeps counts/cost only; a full `manifest.json` lands on disk but **no BFF
  endpoint reads it**. RAG/MCP calls are not cost-priced. The UI shows only run-aggregate cost.

`macro.json` already carries per-scenario `synthesis_question` + facet filters, and its comments
explicitly say the equity legs are "set separately" — i.e. the ARag-shaped hole is already
marked in the config.

---

## 3. Architecture

```
                 named scenario (authored baseline: drivers + overlays + narrative/trigger)
                                              │
                  ┌───────────── DualRagEnrichmentProvider.resolve() ─────────────┐
   preflight ──▶  │  MacroRAGClient.synthesize()        AragClient.company_risks()│  ◀── fail-loud
  (healthz both)  │   → macro legs (UST/OAS/MOVE/VIX/FX)  → company legs (eq:<TKR> │      if required
                  │   + citations + world_view_id          idiosyncratic + overlays│      service down
                  │                                         + filing citations)     │
                  │                deterministic merge over authored baseline       │
                  │        origin ∈ {authored, macrorag, arag}; precedence + attribution
                  └───────────────────────────────┬───────────────────────────────┘
                                                   ▼
                 ScenarioGenerator.materialize (Σ conditional-mean propagation, unchanged)
                                                   ▼
             ScenarioStressEngine (full-reval P&L + ES capital) → plausibility → version → sign-off
                                                   ▼
       StressReport JSON  +  attribution block ("what each service added")  +  trace artifact (JSONL)
                                                   ▼
                          gpt-5 narrative (value-add prose only)   ← LLM
                                                   ▼
                   BFF /scenarios/* (+ new /trace, /cost, /attribution)  →  FE Trace Explorer
```

Every external call (MacroRAG, ARag, OpenAI) flows through an **instrumented call wrapper** that
records a `CallRecord` into a per-run contextvar trace, which is persisted as one JSONL file per
scenario run.

---

## 4. Phased plan

### Phase 0 — ARag REST client + service preflight (foundation)
- New `src/scenariogen/arag/` mirroring `macro/`:
  - `client.py` — `AragClient` over ARag REST: `health()` (`GET /api/health`),
    `coverage()` (`GET /api/v1/coverage`), `narrative_search()`
    (`POST /api/v1/narrative/search` — 10-K/Q risk-factor sections), `answer()`
    (`POST /api/v1/answer`), `facts_lookup()` (`POST /api/v1/facts/lookup`). Retry/backoff/
    circuit-breaker + **generous timeout** like `macro/client.py`'s `RequestsTransport`.
  - `models.py` — `AragCompanyRisk` (ticker, risk-factor text, proposed move, citations).
  - `mapping.py` — company risk factors → canonical `eq:<TKR>` idiosyncratic legs / `eqvol`
    overlays (units + sign discipline; per-name only, never macro).
  - `gate.py` — freshness + citation-coverage gate (mirror `macro/gate.py`).
- **Preflight**: a `preflight_services(required)` that calls `healthz`/`health` on both services
  before pricing and **raises** if a required one is down (decision 2). Applies to the REST path;
  the MCP LLM path (narrative) gets an explicit OpenAI timeout in Phase 3.
- Config: new `configs/scenariogen/arag.json` (per-scenario `narrative_query` + `entities` +
  `expect_tickers`, mirroring `macro.json`).

### Phase 4a — Scenario-set authoring + regeneration (added requirement)
- Make the named-scenario set **fully config-authored** (no hardcoding): a scenario is defined
  by an entry in `scenarios.json` (baseline drivers/overlays + narrative/trigger/horizon) +
  `macro.json` (macro synthesis question) + `arag.json` (company Risk-Factor queries +
  `required_services`). Adding/replacing the set is a config edit.
- Runner support to **generate + run a fresh set** in one command (author → preflight →
  dual-RAG resolve → materialize → price → attribution → trace). Retire/regenerate the current
  four as part of this (the AI-generated LLM scenarios are cleaned up separately).
- Optional convenience: a small authoring helper/CLI that scaffolds the three config entries
  from a single worded prompt + ticker list, so a new hypothetical is a couple of lines.

### Phase 1 — Two-service assimilation for named scenarios
- New `DualRagEnrichmentProvider` (supersedes the macro-only provider for named runs): starts
  from authored baseline, applies MacroRAG macro legs and ARag company legs with explicit
  **precedence** (service legs override authored legs of the same factor; conflicts resolved
  service-over-authored, macro-vs-arag disjoint by factor class), records per-leg
  `origin ∈ {authored, macrorag, arag}` + source citation/rationale.
- Fail-loud wiring: if a scenario declares a service required and it's unavailable/timed out →
  abort that run (no silent static substitution).
- Runner: replace `--macro` with `--dual-rag` (or make dual-rag the default for named runs);
  keep `--offline` for authored-only regression.

### Phase 2 — Value-add attribution on the scenario summary
- Compute, per scenario, the **delta each service made vs the authored baseline** — the concrete
  "extra information": which legs MacroRAG changed (+ `world_view_id` + citations), which legs
  ARag added/changed (+ filing citations), and the resulting P&L attribution of that delta.
- Persist an `attribution` block in the StressReport: `{macrorag: {...}, arag: {...},
  authored: {...}}` with per-service legs, citations, and Δ-vs-authored.
- gpt-5 narrative (LLM only): a short "what each service contributed and why it matters"
  write-up, grounded in the attribution block (reuse `llm/gateway.py`).

### Phase 3 — Timeout hardening + full call capture (backend)
- **Timeouts**: explicit generous `timeout=` on `openai.OpenAI(...)` (`gateway.py`); reconcile
  MCP `call_timeout_s` default; confirm generous MacroRAG/ARag REST timeouts. Policy: wait up to
  the generous ceiling, then fail loudly — never proceed with no service input.
- **Call capture** (`src/scenariogen/trace/`): a `CallRecord` (purpose, service, endpoint/model,
  request payload, response payload / raw completion, system+user prompt for LLM, tokens, cost,
  latency, `prompt_hash`, outcome) recorded via contextvar aggregation with a **capture toggle +
  char truncation** (mirror MacroRAG `usage.py`). Wrap MacroRAG, ARag, and OpenAI call-sites.
  Price LLM calls via existing `cost.py`; RAG calls recorded at cost 0 with a flag (or a
  configured per-call price). Persist one JSONL per scenario run under
  `data/pricer/scenario/<date>/trace/<run_id>.jsonl` + a rollup in the StressReport.

### Phase 4 — BFF trace/metrics endpoints
- `GET /api/scenarios/{name}/trace` — slimmed per-call list (no large bodies).
- `GET /api/scenarios/trace/{call_id}` — full record incl. request/response/prompt.
- `GET /api/scenarios/{name}/cost` — per-call + per-stage + scenario total.
- `GET /api/scenarios/{name}/attribution` — the Phase-2 value-add block.
- Read the new trace JSONL + the already-on-disk `manifest.json`; slim/aggregate like
  MacroRAG's `_slim_trace` / `_aggregate_metrics`.

### Phase 5 — FE Trace Explorer + cost + attribution
- New **"Calls / Trace"** tab on `ScenarioDetail`: waterfall of MacroRAG/ARag/LLM calls with
  latency bars, per-call cost, and drill-down into request / response / prompt (reuse
  `Markdown`/code viewer). Scenario **total cost** in the header.
- **"Service value-add"** panel on the summary: "MacroRAG contributed … / ARag contributed …"
  with citations and Δ-vs-authored.
- Tag per-leg origin (authored / macrorag / arag) in the existing materialized-vector table.

### Phase 6 — Live regeneration + verification
- Run the four enriched named scenarios against live services (MacroRAG :8000, ARag REST,
  inference :8799, `OPENAI_API_KEY`). Verify grounding, attribution, cost, and traces render.
  Update memory + operator guide.

---

## 5. Risks / open items
- **ARag `narrative_search` latency** on CPU inference can exceed timeouts (known issue). With
  fail-loud this would abort a run; mitigate by preferring `facts_lookup`/`answer` where possible
  and/or GPU-ing the `:8799` inference service. Generous timeout must be tuned against this.
- **Prompt/response capture** may contain large payloads — truncation cap + capture toggle
  (default on, MacroRAG uses 20k chars).
- **Two MacroRAG integrations** (REST classic vs MCP LLM) remain; this work touches the REST one.
  Consolidation is out of scope.
- **Sign discipline / units** for ARag company legs must be validated against the return-floor
  clamp (`RETURN_FLOOR = -0.95`).
