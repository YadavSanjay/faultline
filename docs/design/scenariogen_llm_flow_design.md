# LLM-Driven Scenario Generation for `scenariogen` — Design

**Status:** Approved for implementation (P0 in progress)
**Module:** `src/scenariogen/llm/` · **Config:** `configs/scenariogen/llm/` · **Prompts:** `prompts/scenariogen/`
**Reference architecture:** ARag XBRL end-to-end LLM flow
**External services (MCP):** ARag `arag-analyst` (SEC 10-K/Q fundamentals) · MacroRAG `macro-rag` (macro factors / shock vectors)

---

## 1. Goal

Add an LLM/agentic flow that reads the project portfolio and generates **stress-testing
scenarios** with real, correlated risk-factor shocks plus a 1–2 page narrative (including
LLM invocation cost). Two modes:

1. **Comprehensive discovery** — agentically examine the portfolio's risk factors via company
   fundamentals (ARag, from 10-K/Q filings) **and** macro factors (MacroRAG), decompose into
   sub-problems, aggregate company- and macro-level risk factors into portfolio-level
   scenarios, then keep the **top-10 by potential max loss** (loss known only after pricing).
2. **Hypothetical / themed** — given free-text scenarios (e.g. "Taiwan geopolitics",
   "AI boom-bust", "rate hike → AI capex/valuations", "key-man risk at NVDA/TSLA"),
   generate correlated shocks + narrative per theme.

Both produce: actual correlated factor shocks, a 1–2 page narrative on the approach, and the
LLM cost. Generated scenarios are then priced with the existing engine and surfaced in the UI.

---

## 2. Core design principle — produce `ScenarioDefinition`s, reuse everything else

The existing module cleanly separates **"what shocks to apply"** (`ScenarioDefinition`: a few
*driver* moves + overlays + narrative/trigger/method/horizon) from **"how they propagate,
correlate, price, and get governed"** (`ScenarioGenerator.materialize` → Σ conditional-mean
propagation → `ScenarioStressEngine` full-reval P&L + ES capital → plausibility gate →
versioning → sign-off → `StressReport` JSON → BFF → UI).

**The LLM flow only produces `ScenarioDefinition`s.** Everything downstream is reused
unchanged. Consequences:

- **Correlation is enforced by the existing covariance engine, not the LLM.** The LLM proposes
  a small set of economically-justified *driver* shocks; `FactorCovariance.conditional_mean`
  fills every other factor at its market-consistent conditional expectation, yielding a
  complete internally-consistent correlated vector. This directly satisfies the "shocks must be
  correlated as in the real world" requirement, and it is already battle-tested and
  provenance-stamped. The LLM never hand-authors a 38-factor vector (which would be both less
  correct and more expensive).
- **The current implementation cannot break** — pure addition plus a new provider that mirrors
  the existing `MacroScenarioProvider` contract (which already has a static fallback).
- **Pricing + UI are reused** — LLM scenarios flow through the same `run_stress` →
  `StressReport` path the BFF/UI already read, so they appear in the existing
  catalog/detail/results with no read-path change.

MacroRAG's `macro_synthesize_shock_vector` already returns a **cross-correlated,
citation-gated, unit-typed** macro shock vector pinned to a `world_view_id` → those become the
macro driver legs. ARag fundamentals ground the per-name equity legs. Σ ties everything
together.

### 2.1 Layering of correlation

| Layer | Source | Mechanism |
|---|---|---|
| Macro legs (rates, credit, vol, FX) | MacroRAG `macro_synthesize_shock_vector` | Already a single cross-correlated, cited, unit-typed vector pinned to `world_view_id` |
| Per-name equity legs (`eq:NVDA`, …) | ARag fundamentals + LLM calibration | Grounded in declared 10-K risk magnitudes (China revenue at risk, concentration, margin sensitivity) |
| All other factors | Existing `conditional_mean(drivers)` Σ propagation | Gaussian conditional expectation → real-world correlation across the full factor set |
| Idiosyncratic add-ons (key-man) | LLM overlays | Additive, post-propagation (covariance can't see them) |

---

## 3. Provider & orchestration decisions

- **LLM provider:** OpenAI **gpt-5 family**, mirroring ARag's gateway/prompts/cost code
  (per-stage tiering `gpt-5` / `gpt-5-mini` / `gpt-5-nano`). `OPENAI_API_KEY` from env. (The MCP
  servers are provider-neutral JSON, so this choice is independent of them.) We re-implement
  ARag's patterns minimally rather than importing across projects.
- **Orchestration:** **hybrid** — a deterministic phase skeleton fixes the stages
  (discover → evidence → propose → calibrate → price/rank → narrate); within phases, gpt-5
  drives **bounded** MCP tool-calling loops (capped tool-calls + token/cost budget). Reproducible
  and cost-bounded while still "agentic" where it matters.

---

## 4. Phase flow

### Mode 1 — Comprehensive discovery (top-10 by max loss)

- **A. Discover** — load `portfolio.json` (`portfolio_loader`), extract tickers/themes. Call
  `macro_discover_factors(portfolio)` for ranked macro factors; per portfolio company in ARag
  coverage, a quick `narrative_search` for declared 10-K risk themes. Output: ranked factor
  universe with citations.
- **B. Evidence** (bounded agentic loop) — gpt-5 uses ARag tools (`facts_lookup`,
  `facts_compute`, `narrative_search`) to extract declared risk factors + magnitudes, and
  MacroRAG (`macro_query`, `macro_get_citations`) for macro evidence. Output: cited
  `CompanyRiskFactor[]` + `MacroRiskFactor[]` — **every number carries a citation**.
- **C. Propose** — gpt-5 aggregates company + macro risk factors into portfolio-level scenarios
  keyed by shared drivers (e.g. "Taiwan supply shock", "AI-capex retrenchment", "rate-driven
  multiple compression", "hyperscaler-concentration unwind"), tagged with exposed trades. May
  produce > 10 candidates.
- **D. Calibrate** — per proposal: macro legs via `macro_synthesize_shock_vector(fields=…)`
  (cited, unit-typed, pinned to `world_view_id`); per-name equity legs from Phase-B
  fundamentals; convert to canonical units → `ScenarioDefinition`.
- **E. Price & rank** — feed **all** candidates through the existing `ScenarioGenerator.materialize`
  + `ScenarioStressEngine` (full-reval P&L + capital). Rank by total loss, **keep top-10**.
  (Max loss is only known post-pricing — so we price all candidates, then prune.)
- **F. Narrate** — 1–2 page markdown per kept scenario (approach, driver rationale, correlation
  logic, citations) + a **cost page** (per-stage tokens & USD).

### Mode 2 — Hypothetical / themed

Skip A; take free-text themes → targeted B → one proposal per theme (C) → D → price all (E, no
top-10 pruning) → F.

---

## 5. New module layout — `src/scenariogen/llm/`

```
config.py     load configs/scenariogen/llm/*.json (provider, per-stage model tiers, budgets, MCP specs)
gateway.py    InstrumentedChatClient (OpenAI gpt-5 unary gateway) -> LLMCallResult{content,tokens,cost,latency,retries}
cost.py       CostTracker + MODEL_PRICING (tokens/1000 * rate)            [mirrors ARag]
prompts.py    PromptManager/PromptTemplate (.txt + {var}, version hash) -> reads top-level prompts/scenariogen/
trace.py      contextvar per-run rollup (cost/tokens/tool-calls); hooks existing obs spans if cheap
schema.py     pydantic: FactorEvidence, Company/MacroRiskFactor, ScenarioProposal, CalibratedScenario, LLMScenarioArtifact
mcp/
  client.py   stdio_client + ClientSession; spawn ARag + MacroRAG per the config blocks
  registry.py MCP JSON-Schema -> OpenAI function tools; namespaced (arag__*, macro__*); per-phase allowlists
  loop.py     bounded gpt-5 function-calling loop <-> MCP tools/call (max_tool_calls + token budget, transient retry)
phases/       discover.py, evidence.py, propose.py, calibrate.py, narrate.py, verify.py
flow.py       LLMScenarioFlow — deterministic skeleton wiring A-F; mode1 vs mode2 entry
provider.py   LLMScenarioProvider — CalibratedScenario -> ScenarioDefinition (mirrors MacroScenarioProvider)
store.py      LLMScenarioArtifactStore — proposals/evidence/calibrated/narratives(md)/cost/run-manifest
runner.py     CLI: python -m src.scenariogen.llm.runner --mode comprehensive|hypothetical [--themes ...]
```

Mirrors ARag's proven patterns: config-as-code, per-stage model tiering, versioned `.txt`
prompts (schema + few-shots embedded in the system prompt, content-hash stamped onto each
call), one instrumented gateway returning a rich `LLMCallResult`, a 3-level cost rollup
(per-call / per-process / per-run via a `contextvars` trace), and a faithfulness `verify` stage
that drops ungrounded shocks.

---

## 6. MCP client (hand-rolled, since OpenAI)

Use the official `mcp` SDK `stdio_client(StdioServerParameters(command, args, env, cwd))` to
spawn both servers exactly per the provided config blocks → `tools/list` → convert each
`inputSchema` (JSON Schema) to an OpenAI function tool (namespaced, e.g. `arag__facts_lookup`,
`macro__synthesize_shock_vector`) → bounded loop dispatching `tool_calls` to `tools/call`.
Per-phase tool allowlists keep loops focused/cheap:

| Phase | Allowed tools |
|---|---|
| Discover | `macro__discover_factors`, `macro__vocabulary`, `arag__coverage`, `arag__narrative_search` |
| Evidence | `arag__facts_lookup`, `arag__facts_compute`, `arag__narrative_search`, `macro__query`, `macro__get_citations` |
| Calibrate | `macro__shock_schema`, `macro__synthesize_shock_vector`, `macro__get_citations` |

The `mcp` SDK is async; the flow runs the loop via `asyncio.run` wrappers so the scenariogen
CLI stays synchronous.

**Config-block notes (from research):** ARag's MCP server ignores `--tenant`/`--transport`
(stdio is its default; tenant is a no-op there) and its true entry is `src.mcp_server.server`;
MacroRAG's `--tenant scenariogen` scopes retrieval (currently the whole shared corpus). We pass
the user-provided blocks verbatim — harmless — and document the nuance.

---

## 7. Config & prompts

- `configs/scenariogen/llm/llm.json` — provider, per-stage model tiers, temps/max-tokens, retry,
  budgets (`max_tool_calls` per phase, `max_total_cost_usd`, `max_scenarios=10`).
- `configs/scenariogen/llm/mcp.json` — the two server specs (command/args/cwd/env) + per-phase
  tool allowlists + timeouts.
- `configs/scenariogen/llm/pricing.json` — optional `MODEL_PRICING` override (defaults in code).
- Loaders in `llm/config.py` follow the existing `src/scenariogen/config.py` plain-JSON pattern.
- `prompts/scenariogen/` — `{discover,evidence,propose,calibrate,narrate,verify}_{system,user}.txt`,
  `{var}` substitution, output schema + few-shots embedded, version-hashed.

---

## 8. Persistence & runner integration (non-breaking)

- `data/pricer/scenario_llm/<run_id>/` — run manifest (mode, models, `world_view_id`, cost
  rollup, config sigs), `proposals.json`, `evidence.json`, `calibrated/*.json`,
  `narratives/*.md`, `cost.json`.
- Calibrated scenarios register into the **existing** `ScenarioStore` (content-addressed
  versioning) and run through the **existing** `ScenarioStressEngine` / `StressReport` writer,
  with a backward-compatible `source: "llm" | "static"` tag + an `llm` provenance sub-object
  (models, cost, `world_view_id`, evidence citations, tool-call count) on scenario entries. The
  BFF/UI pick them up automatically.
- Reproducibility: pin `world_view_id` + covariance `cov_id` + prompt/config hashes in every run
  manifest.

---

## 9. UI changes (mirror existing patterns)

- **BFF**: `GET /api/scenarios/{name}/narrative` (markdown sections — mirrors
  `pricing.trade_docs`); extend `detail()`/`catalog()` with `source` + `llm` block; optional
  `GET /api/scenarios/llm/runs`. Register the narrative route **before** the `/{name}`
  catch-all.
- **FE**: "AI" badge in `ScenarioCatalog`; a **Narrative** internal tab + **AI Provenance** card
  in `ScenarioDetail` (render via the existing dependency-free `<Markdown>`; show USD/token
  cost, per-stage models, `world_view_id`, evidence citations) — mirroring the "How it's priced"
  `PricingMethodology` tab. Optional "AI Generation" top tab listing runs. Keep `api.ts` TS
  interfaces in sync (the BFF returns plain dicts, no pydantic).

---

## 10. Cost surfacing

`CostTracker` (mirror ARag) computes `cost = prompt/1000*in + completion/1000*out` from a
`MODEL_PRICING` table, accumulates per-call; `trace.py` rolls up per-run; cost lands in the run
manifest, the narrative cost page, and the UI provenance card. Cost is a first-class output and
a circuit-breaker bound (`max_total_cost_usd`).

---

## 11. Testing

Offline/deterministic unit tests under `tests/scenariogen/llm/` with a fake MCP client + fake
gateway (mirroring ARag's fake-pipeline tests): schema conversion, loop bounding/budget, cost
math, calibrate → canonical-units, provider adapter, and an end-to-end with a stubbed LLM
proposal **priced through the real engine on the real 38-trade portfolio**. Plus an opt-in live
smoke test (needs servers + keys). ruff / mypy / pytest kept green.

---

## 12. Phased implementation plan (one-by-one to `main`)

- **P0 — Scaffolding**: config loaders, prompts dir, gateway, cost tracker, trace, schema,
  store, config JSON files. (no MCP/UI)
- **P1 — MCP client layer**: connect both servers, tool conversion, bounded loop, allowlists;
  offline tests.
- **P2 — Mode 2 end-to-end** (simplest full spine): theme → evidence → propose(1) → calibrate →
  price via engine → narrate → store. Proves correlation + cost + narrative.
- **P3 — Mode 1**: discovery + aggregation + price-all + top-10 ranking.
- **P4 — Runner + StressReport integration**: `source`/`llm` provenance so existing UI surfaces
  them.
- **P5 — BFF**: narrative + provenance + runs endpoints.
- **P6 — FE**: narrative tab, AI-provenance card, badges, optional generation page.
- **P7 — Hardening**: faithfulness/verify stage, budgets/circuit-breaker, docs, gates green.

---

## 13. Risks & notes

- **gpt-5 cost variance** in agentic evidence loops — mitigated by per-phase tool allowlists,
  `max_tool_calls`, and a `max_total_cost_usd` circuit-breaker.
- **ARag queryable coverage** is whatever's loaded in its DB (NVDA fullest; XOM/AMZN/JPM/GOOGL
  active) — Phase A calls `coverage()` at runtime and degrades gracefully for portfolio names
  ARag lacks (falls back to macro + narrative-only).
- **No cross-project imports** — ARag/MacroRAG are consumed only over MCP (stdio subprocess);
  their LLM/gateway code is mirrored, not imported.

---

## 14. Key existing files referenced

- Scenario spine: `src/scenariogen/scenario_gen.py` (`ScenarioDefinition`, `to_canonical`,
  `ScenarioGenerator.materialize`), `factor_keys.py`, `factor_vector.py`, `covariance.py`
  (`conditional_mean`), `stress.py` (`ScenarioStressEngine`), `report.py`, `runner.py`,
  `versioning.py`, `plausibility.py`, `signoff.py`.
- Provider/fallback pattern to mirror: `src/scenariogen/macro/{provider,client,models}.py`.
- Config pattern: `src/scenariogen/config.py`, `configs/scenariogen/*.json`.
- UI/BFF: `ui/bff/app/scenarios.py`, `ui/bff/app/pricing.py` (markdown precedent),
  `ui/app/src/features/scenarios/*`, `ui/app/src/features/pricing/PricingMethodology.tsx`,
  `ui/app/src/components/shared/Markdown.tsx`, `ui/app/src/lib/api.ts`.

---

## 15. As-built (P0–P7) — deltas from the plan

All phases shipped; the flow is proven end-to-end and Mode-2 was live-validated grounded (a
"Strait of Silicon" scenario priced −$19.3M with a real MacroRAG `world_view_id` + citations).
Notable deltas from the original plan:

- **Provider = OpenAI gpt-5** (user decision), mirroring ARag's gateway/prompts/cost patterns.
- **Sister-server model loading moved to a shared inference service** (`RAGINFER_URL=http://127.0.0.1:8799`,
  in the `mcp.json` server env) — ARag/MacroRAG call it for embed/rerank instead of loading models
  in-process. Fixes an earlier stdio deadlock/cold-start; the flow needs this service up for grounded
  runs. `call_timeout_s` raised to 300 for the cold first call.
- **MacroRAG tool names are `macro_*`-prefixed**, so the server-prefixed names are `macro__macro_query`
  etc. (ARag tools are bare → `arag__coverage`). Allowlists in `mcp.json` reflect this.
- **Per-stage `max_tokens` raised to 8k–24k** — gpt-5 reasoning tokens were truncating the JSON at the
  original 4k–6k; `max_tool_calls_per_phase` set to 6 to bound CPU-bound retrieval latency.
- **MCP client** (`src/scenariogen/llm/mcp/client.py`): single long-lived service coroutine (anyio
  cancel-scope correctness) + per-call timeout that cancels a hung tool and marks the server degraded.
- **P4** writes LLM scenarios into the standard `StressReport` with `source`/`llm` provenance;
  **P5** the BFF unions reports across a date and adds `llm_scenarios` + a narrative endpoint;
  **P6** the FE renders an AI-scenarios catalog section, an AI-provenance card, and an AI-narrative tab.
- **P7** hardening: the `verify` faithfulness stage (`phases/verify.py`) drops ungrounded legs before
  pricing (guarded); the `max_total_cost_usd` cost circuit-breaker (gateway hard-stop + flow soft-stop).

Operator guide: `docs/guides/scenariogen_llm_flow.md`.

Open follow-ups (non-blocking): ARag `narrative_search` CPU latency (route via fast `facts_lookup`
or GPU the inference service); canonicalize `fx:USDTWD` → `fx:TWD` in the calibrate prompt; optional
live-validation of Mode-1.
