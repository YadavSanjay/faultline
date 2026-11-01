# Operator guide — LLM scenario generation (`src/scenariogen/llm`)

How to run the LLM-driven stress-scenario generator, what it needs, and what it produces.
Design + rationale: [scenariogen_llm_flow_design.md](../design/scenariogen_llm_flow_design.md);
providers: [llm_multi_provider_design.md](../design/llm_multi_provider_design.md).

## What it does

Generates correlated stress-test scenarios for the portfolio using an agentic LLM flow that reads
company fundamentals (ARag MCP) and macro factors (MacroRAG MCP), then prices and narrates them
through the **existing** scenariogen machinery. The models come from a **provider profile**:
`openai` (default; gpt-5 / gpt-5-mini / gpt-5-nano), `anthropic` (Claude Sonnet 5.5 for propose
and calibrate, Claude Haiku 5.5 elsewhere) or `deepseek` (deepseek-v4-pro for propose and
calibrate, deepseek-flash elsewhere). Two modes:

- **Hypothetical (Mode 2)** — one scenario per free-text theme (e.g. "Taiwan geopolitics").
- **Comprehensive (Mode 1)** — discover the portfolio's risk-factor universe, propose up to
  `max_candidates` scenarios, price them all, and keep the top-N by max loss.

The LLM only produces `ScenarioDefinition`s (a handful of driver shocks); the existing covariance
engine propagates them into a full correlated factor vector, and the existing full-reval engine
prices them. Nothing in the pricer or the static scenario path changes.

## Prerequisites

1. **The API key of every provider the profile uses**: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
   and/or `DEEPSEEK_API_KEY` in the environment (the runner does not auto-load `.env`; export them
   or load it before running). Each also takes a `<NAME>_FILE` variant pointing at a secret file.
   The runner checks the keys before it starts the MCP servers and fails naming any that are
   missing. The attribution narrative in `scenariogen.runner --dual-rag` is fail-soft instead: with
   no key it keeps the deterministic attribution only.
2. **The two MCP servers** must be launchable per `configs/scenariogen/llm/mcp.json` (server keys
   `arag` / `macro`; their locations are env-overridable via `ARAG_HOME` / `MACRORAG_HOME` /
   `MCP_PYTHON`, defaulting to the paths below):
   - `arag` at `../XbrlRag` (a sibling checkout) — SEC 10-K/Q fundamentals.
   - `macro` at `../MacroRag` (a sibling checkout) — macro factors + citation-gated shock vectors.
   Both need their backends up (Postgres for ARag, Qdrant for MacroRAG).
3. **The shared inference service** on `http://127.0.0.1:8799` (embeddings + reranker), referenced
   by both servers via `RAGINFER_URL`. If it's down, retrieval tools fail fast (~seconds) and the
   flow degrades to the model's own reasoning (scenarios still price, but without fresh citations).

## Run

```powershell
# Mode 2 — themed
conda run --no-capture-output -n py312 python -m src.scenariogen.llm.runner --mode hypothetical `
  --themes "Taiwan geopolitics" "AI boom-bust"

# Mode 1 — comprehensive (discover + top-N by max loss)
conda run --no-capture-output -n py312 python -m src.scenariogen.llm.runner --mode comprehensive --no-capital
```

Flags: `--themes` (required for hypothetical), `--valuation-date`, `--asset-class`, `--no-capital`
(skip the heavy ES-under-shock capital pass — recommended while iterating), `--var-lookback`,
`--cov-lookback`, `--llm-profile {openai|anthropic|deepseek}` (default: `$SCENARIOGEN_LLM_PROFILE`,
else `openai`).

```powershell
# the same Mode-2 run on Claude
conda run --no-capture-output -n py312 python -m src.scenariogen.llm.runner --mode hypothetical `
  --themes "Taiwan geopolitics" --llm-profile anthropic --no-capital
```

> First macro/ARag retrieval call is a cold start (model load + rerank on the inference service).
> Budget several minutes; `call_timeout_s` in `mcp.json` (300) covers most tools, and MacroRAG's
> shock synthesis has its own `tool_timeouts_s` entry (900).

## Configuration (`configs/scenariogen/llm/`)

- **`llm.json`** — validated on load; a mistyped key raises `ConfigError`. It holds:
  - `active_profile` (`${SCENARIOGEN_LLM_PROFILE:-openai}`);
  - `providers` (key env var, timeout, DeepSeek's `base_url`);
  - the `models` registry: each model's provider, output cap, `json_mode`, and the
    `effort` / `thinking` / `temperature` rules it accepts;
  - `profiles` mapping every stage to a model;
  - per-stage `max_tokens` (kept generous so reasoning tokens don't truncate the JSON; capped at
    the model's output limit);
  - `retry`, and `budgets` (`max_tool_calls_per_phase`, `max_total_cost_usd`, `max_scenarios`,
    `max_candidates`).
- **`mcp.json`** — the two server launch specs, `timeout_s` (overall wait per call), `call_timeout_s`
  (default per-tool call timeout), `tool_timeouts_s` (per-tool overrides, keyed like the allowlists),
  `degrade_after_timeouts`, and per-phase tool allowlists. The timeouts are validated at startup
  (`CallTimeoutPolicy.from_settings`): every value finite and > 0, no call timeout above `timeout_s`.
- **`pricing.json`** — the governed rate card (USD per 1K tokens). Every model a profile uses
  must have a row, or loading fails; an unknown model never costs $0. Rows price uncached input,
  cached input, cache writes and output, plus Claude Haiku's prompt-length tier and DeepSeek's
  UTC peak windows. Each call is costed at its start time.
- Prompts: `prompts/scenariogen/*.txt` (one per phase × role).

## Safeguards

- **Provider rules, fail-loud** — there is one retry policy (the gateway's; SDK retries are off),
  no fallback to another model or provider, and a refusal (`refusal` / `aborted` /
  `content_filter`) is a fatal error. A response from a model other than the one requested (or its
  dated snapshot, e.g. `gpt-5-2025-08-07`) is rejected. Claude requests stream, carry the
  registry's thinking / effort, and ask for prompt caching. DeepSeek requests carry the
  registry's `reasoning_effort`.
- **Faithfulness verify** (P7) — after calibration, a cheap verify-stage pass checks each driver leg
  against its cited evidence and drops UNSUPPORTED legs before pricing (never drops the last leg;
  a parse failure keeps all legs). The outcome is recorded in the scenario's `verification` block.
- **Deterministic fact-check** (harness-audit F4, `llm/phases/fact_check.py`) — runs *after* verify
  with **no LLM call**: every `eq:<TICKER>` company leg is grounding-checked (≥1 citation), its move
  is **clamped** into `[RETURN_FLOOR, +0.95]` if implausible, and (when ARag coverage is available)
  its ticker/fiscal-years are checked as covered/fresh. Verdicts merge into `verification["fact_check"]`;
  it never drops a leg. This is the deterministic half of "doer ≠ judge" over the LLM's company legs.
- **Code-made MacroRAG synthesis** (spec `llm-grounding-gate`) — before calibrating each scenario
  the flow itself calls `macro_synthesize_shock_vector` once and hands the response to the model
  (without its `world_view_id`); the model can't call it again in calibrate. The scenario's
  `world_view_id` is set by code from that response, never from the model. If the call fails
  (e.g. a timeout), the scenario is refused with reason `synthesis_failed`.
- **Grounding gate** (`llm/grounding.py`) — the last check before pricing, with no LLM call. A
  scenario is refused (not priced, kept, narrated or published) if every leg is UNSUPPORTED, the
  synthesis has a coverage gap (`synthesis_diagnostics.coverage_gap` or an empty shock vector), the
  synthesis has no `world_view_id`, a leg has no citation, or a leg cites a chunk id that no MCP
  tool returned during the run. Refusals are logged at ERROR and listed with their reason codes in
  `manifest.json` → `rejected`; the run carries on with the other scenarios, and a run whose
  scenarios are all refused writes no StressReport.
- **Cost circuit-breaker** — `budgets.max_total_cost_usd` is enforced two ways: the flow stops
  starting new scenarios/candidates once the run's cost reaches it, and the gateway hard-stops any
  further LLM call (`BudgetExceededError`) as a runaway backstop.
- **Per-tool MCP timeout + degrade** (spec `mcp-call-timeouts`) — a hung sister-service call is
  cancelled after its tool's timeout (`tool_timeouts_s` entry, else `call_timeout_s`). After
  `degrade_after_timeouts` timeouts that **tool** is skipped for the rest of the run; the other tools
  on its server keep working, so one slow tool can't freeze the run or disable a whole server.

## Outputs

- **Artifacts**: `data/pricer/scenario_llm/<run_id>/` — `manifest.json` (proposals, calibrated
  scenarios, ranking, cost, models, `world_view_id`, and `llm`: the profile, the provider and model
  per stage, effort / thinking per model, and the provider SDK versions) and `narratives/<slug>.md`
  (the 1–2 page narrative with the LLM invocation cost). Manifests written before multi-provider
  support have no `llm` block and ran on `openai`.
- **LLM ops console** (Operations → LLM cost): cost by stage and by provider, the active profile,
  and per run its profile and each stage's provider.
- **StressReport**: `data/pricer/scenario/<date>/scenario_<run_id>.json` — the standard report the
  BFF/UI read, each scenario tagged `source: "llm"` with an `llm` provenance block.

## In the UI

Run the BFF + app (see `docs/guides/` UI run notes). Under **Stress Scenarios**:

- The catalog shows an **"AI-generated scenarios"** section (worst-loss-first, with an AI badge,
  world-view id, and generation cost).
- Each AI scenario's detail page has an **AI-generated** badge, an **AI generation** provenance card
  (models, cost, per-leg citations), and an **AI Narrative** tab rendering the markdown.

## Known follow-ups

- ARag `narrative_search` is CPU-bound (~minutes) on first use; if it exceeds `call_timeout_s` that
  tool degrades. Route equity grounding through the fast `facts_lookup`, or give the inference service a GPU.

FX factor keys are already normalized: calibration output that names a leg `fx:USDTWD` (MacroRAG's
USD-base convention) is canonicalized to `fx:TWD` deterministically in `llm/phases/calibrate.py`
(`_canonicalize_factors` → `factor_keys.canonical_fx_key`), covered by
`tests/scenariogen/llm/test_fx_canonicalization.py`.
