# CLAUDE.md — `src/scenariogen`

Correlated stress-scenario generator. From a few narrative driver moves it propagates a
covariance-consistent shock vector across all factors, fully reprices the book (reusing the pricer
+ risk full-reval engine), computes ES-under-shock capital, gates plausibility, versions/signs-off,
and solves reverse stress. Operator guides:
[../../docs/guides/scenariogen_guide.md](../../docs/guides/scenariogen_guide.md) ·
[scenariogen_llm_flow.md](../../docs/guides/scenariogen_llm_flow.md).

## Two driver-sourcing paths

1. **Deterministic dual-RAG** (`runner.py --dual-rag`) — MacroRAG sets macro drivers; **ARag** sets
   each covered name's idiosyncratic company overlay grounded in SEC 10-K/Q Risk Factors
   (`final(eq:NVDA) = systematic(macro) + idiosyncratic(ARag)`, `dualrag.py`). **Fail-loud**:
   `preflight_services` aborts the run if a *required* service is down; per-scenario ARag failure
   raises `ScenarioGenerationError`; a breached run-level ARag budget hard-stops. `--macro` is the
   older MacroRAG-only path with **graceful** static fallback.
2. **Agentic LLM** (`llm/runner.py --mode {hypothetical|comprehensive} [--llm-profile ...]`) — an
   LLM over two stdio MCP servers (ARag, MacroRAG). Phases: discover → evidence → propose →
   calibrate → **verify** (P7 LLM faithfulness) → **fact_check** (F4, deterministic clamp/flag, no
   LLM) → price → narrate. Bounded by per-phase tool-call caps + a cost breaker.
   - **Providers** (design `docs/design/llm_multi_provider_design.md`): profiles `openai` (default),
     `anthropic`, `deepseek` in `configs/scenariogen/llm/llm.json`, validated by
     `llm/profiles.py`.
   - **Every call goes through `llm/gateway.py`.** `InstrumentedChatClient` owns retry, cost,
     budget, finish-reason handling and the served-model check. It routes to `OpenAIChatBackend`
     (OpenAI and DeepSeek) or `llm/anthropic_backend.py`.
   - **Don't add** SDK retries, model fallbacks or a $0 cost path; each is a deliberate fail-loud
     decision (D3, D6, D9).
   - **New model:** add a registry row in `llm.json` and a rate row in `pricing.json`. Loading fails
     without both.

## Structure

- `arag/` — deterministic ARag REST: `client.py` (+ `RestTransport`, retry/breaker, **F5 run-budget +
  telemetry**), `provider.py`, `calibrate.py` (fact-scaling), `gate.py` (freshness+citations),
  `budget.py` (`AragBudget`/`AragBudgetError`), `capabilities.py` (**F3 single capability catalog**),
  `models.py`.
- `macro/` — deterministic MacroRAG REST (graceful fallback).
- `news/` — **P3/A3-A4** news-event calibration (mirrors `arag/`/`macro/`): `provider.py`
  (`NewsEventProvider` reads `data/marketdata/news/sec_events.csv` + `gdelt_salience.csv`, resolves
  **PIT** to the run's as_of), `mapping.py` (clamped multiplier `1 + β_event·event_score(age-decayed)
  + β_tone·(-tone_z)` ∈ `[0.8,1.5]`), `gate.py` (freshness + min-coverage), `tool_source.py` (A4
  `ToolSource` so the agentic path sees events). Wires into `dualrag.py` — scales the ARag
  idiosyncratic leg (mode-(ii), no Σ cost); 5.02 fires `keyman_nvda` as a live trigger. Config
  `news.json`. None/held-gate → no-op. See design §2 / §11.12.
- `llm/` — agentic path: `runner.py`, `flow.py`, `gateway.py`, `cost.py`, `trace.py`, `mcp/`
  (stdio client, tool loop, registry), `phases/` (incl. `fact_check.py`), `schema.py`, `store.py`.
- `trace/` — call-trace capture for dual-RAG runs.
- `options/` — B4 `OptionSignalProvider` (market-implied plausibility check), **point-in-time** since
  step 10 (`options-shock-sizing`): per name the latest snapshot ≤ as_of within `options_sizing.json`
  `max_staleness_days`, else the name's own CBOE vol index (lognormal) where `vol_index` lists one.
  `sizing.py` holds the pure rules. A scenario with `"severity": {..., "source": "options"}`
  (`keyman_nvda`) takes its lead's tiers from the option quantiles (`anchoring.option_anchors`; falls
  back to history with tier flag `option_anchor_unavailable`); every adverse tier ≤ 21d gets tier flag
  `below_option_tail` when the market's 1% quantile is more extreme than its lead. `severity_multiplier`
  was removed; `credit_implied.csv` waits for scenario #8 (step 11).
- **F8 geopolitical** (P7): `geopolitical.py` — `GeopoliticalProvider` reads GPR (`data/marketdata/
  stress/gpr_daily.csv` global + `gpr_country.csv` GPRC_TWN/CHN), resolves PIT a clamped severity
  **multiplier** `clamp(1+β(2·pct−1),0.85,1.4)` + **live-trigger** (`z≥1.5 or pct≥0.9`). A scenario
  tagged `"geopolitical":{"series":...}` in scenarios.json (taiwan→GPRC_TWN) has its whole shock scaled
  via `catalog.materialize(..., severity_multiplier=)`; stamped on `ScenarioStressResult.geopolitical`.
  Mode-(ii), no Σ cost; None/untagged → no-op. config `geopolitical.json`. See design §11.14.
- **F9 throughput** (P7): `throughput.py` — `ThroughputProvider` reads FRED `IPG3344S` (`stress/
  fred_throughput.csv`) + EIA power demand; resolves PIT a severity multiplier that's the **inverse** of
  F8 (`clamp(1+β(1−2·pct),0.85,1.4)` — *decelerating* throughput amplifies) + a thesis flag
  (intact/cooling/breaking). `ai_deflation` tagged `"throughput":{}`; stamped on `ScenarioStressResult.
  throughput`. The runner combines geo × throughput into one `severity_multiplier`. config `throughput.json`.
- **F5 crowding** (P7): `crowding.py` — `CrowdingProvider` reads FINRA days-to-cover (`data/marketdata/
  crowding/short_interest.csv`); resolves PIT a per-name multiplier `clamp(1+β(2·pct−1),0.8,1.4)` (crowded
  amplifies) + a #6-SMH crowded-**short** squeeze flag. Wires into `dualrag.py` **composed with news** on
  the ARag idiosyncratic legs (`idiosyncratic = ARag × news × crowding`; shared provenance key
  `calibration`). Dual-RAG-mode; config `crowding.json`. CFTC COT dropped (unreachable).
- **Point-in-time event replay** (step 14b): `event_replay_runner` rebuilds the set as of the day before
  each episode in `event_replay.json` (history, Σ and long-history anchors cut at as_of via
  `PitFactorHistory` / `pit_long_history`; scenarios whose replay window ends on or after as_of drop out;
  MacroRAG `date_to`, XbrlRag facts `as_of` and filed fiscal years via `pit_arag_config`; the F7 Σ
  regime reads the COR3M level on or before as_of — `catalog.from_config` passes a history's `cutoff`
  to `resolve_regime(as_of=)` — and `PitFactorHistory` drops issuer credit factors whose DTCC history
  starts after as_of via `event_replay.pit_issuer_drops`, so their legs fall back to `credit`; spec
  `docs/specs/replay-pit-leaks.md`; each episode's `pit` block records the regime asof and the
  dropped issuer factors) and scores
  authored and (`--dual-rag`) enriched vectors against the realised moves (`event_replay.py`; no pricing).
  Report under `data/scenariogen/event_replay/`. MacroRAG requests are always capped at the run's as_of
  (`macro.models.point_in_time_filter`).
- **Issuer credit factors** (step 11a): `credit:ORCL` / `credit:META` are ordinary Σ factors from the
  pricer's factor history; in a vector an issuer key overrides `credit` for that issuer
  (`factor_vector.vector_to_transforms`), and plausibility bounds it as `credit`. Replays pick up their
  observed moves (the issuer's own full series, not the aligned panel).
- **Theme-relevant events** (step 9, composition rule `dualrag-v3`): `events.py` + `events.json`. Per
  scenario theme: which 8-K items count toward news (each with its own item weight), tone on/off, crowding
  on/off (reliable names only: `crowding_min_obs`, `crowding_max_staleness_days`), and trigger items/names
  that mark it live (replaces news.json `keyman_items`; hits go to `dualrag.events` provenance and the
  forward log's `news_items`). `ScenarioEvents` is built in `runner._build_dual_provider`; company legs
  take their theme multipliers via `dualrag._idio_parts(factor, name)`, and `anchoring.leg_transform`
  scales the company-specific part of every other single-name equity driver (`scale_driver_residuals`;
  ETFs in `excluded` and the severity lead never). No `events.json` → the legs-only v2 behaviour.
- **P5 Σ rate/correlation upgrades:** `implied_corr.py` (**F7** — reads CBOE `COR3M` implied-correlation
  percentile → maps to the Ledoit-Wolf `shrinkage`: elevated corr → low shrinkage/systemic, low →
  high/idiosyncratic; **zero** Σ dims; catalog stamps the regime into cov provenance; `enabled:false`
  → fixed shrinkage). **F6** rate-decomposition DRIVER factors live in `factor_history._rates_decomp()`
  (`real:DFII10`/`be:T10YIE`/`tp:ACMTP10`, driver-only under `include_style`) + `factor_keys.is_rates_decomp`
  + plausibility class `rates_decomp`. See design §11.10.
- **Historical replay**: `replay.py` (`episode_moves`, `trading_days`, `replay_drivers`, `vol_matched`;
  spec `docs/specs/scenario-historical-replay.md`). A `"replay": {start, end, idiosyncratic}` spec is
  resolved **in `ScenarioCatalog.from_config`** into `{"move": x}` drivers (Σ factors only) plus
  `horizon_days` = `eq:XLK` trading days. `catalog.scenario_config()` is the resolved view; pass it to
  the macro/ARag/dual-RAG providers (`scenario_config=`), which otherwise read the raw config and see
  no replay drivers. An uncovered window is stored in `catalog.errors` and raised by `catalog.spec(name)`.
  The `sensitivities` block resolves too but is not in `names()` (runner `--sensitivities`).
  `long_history.json` `"match"` scales a long series to a shorter target's volatility (credit:
  BAA10Y → IG OAS).
- Top-level: `runner.py`, `dualrag.py`, `scenario_gen.py`, `factor_keys.py` (canonical taxonomy;
  `RETURN_FLOOR=-0.95`), `covariance.py`, `catalog.py`, `plausibility.py`, `versioning.py`,
  `signoff*.py`, `preflight.py`, `narrative.py`, `attribution.py`, `stress.py`, `reverse.py`,
  `report.py`, `authoring.py`, `config.py`, `errors.py`.
- **P4 exit-value/funding post-proc** (mode-(iii), additive; `--no-exit-value` to skip): `liquidity.py`
  (**F1** `√(LH/base)` horizon scaler on *losses only* + bid-ask haircut from real option-chain
  `bid`/`ask`; signed-off LH table `liquidity.json` → `signoff`) and `collateral.py` (**F2** cash line:
  VM = `-Σ margined P&L` + IM-increase proxy + `SOFR−IORB` funding gauge; margined tag in
  `collateral.json`, **not** `portfolio.json`). Emit `liquidity`/`collateral` on `ScenarioStressResult`
  (`None` when disabled → report byte-identical). See design §11.9.

## Entry points

```powershell
python -m src.scenariogen.runner [--dual-rag|--macro] [--scenario X --severity mild|adverse|severe] [--sensitivities] [--no-capital] [--no-gate] [--require-signoff] [--no-narrative] [--reverse-stress --target-loss USD]
python -m src.scenariogen.llm.runner --mode hypothetical --themes "…"    # or --mode comprehensive --no-capital
python -m src.scenariogen.signoff_runner --list | --approve X | --reject X --version <hash>
python -m src.scenariogen.authoring apply <doc>.json [--replace-set]      # rewrites the 3 scenario configs atomically
python -m src.scenariogen.coverage_runner [--report <stress json>] [--tier mild] [--workers N]   # set coverage/diversity (G3)
python -m src.scenariogen.decision_impact_runner [--stress-report <json>] [--ablation-report <json>]  # sizing with/without stress-loss constraint (G13; analysis only)
python -m src.scenariogen.stability_runner [--repetitions 3] [--scenario X] [--severity T ...]   # run-to-run spread of dual-RAG legs and P&L (G9; MacroRAG no_cache)
```

Outputs: `data/pricer/scenario/<date>/scenario_<run>.json` (+ `reverse_*`, `coverage_*`, `stability_*`, `trace/*.jsonl`),
`data/pricer/store/scenario*`, `data/pricer/scenario_llm/<run>/` (LLM path), `data/scenariogen/`.

Each stress report carries `held_out` (tiers the plausibility gate stopped, with findings) and
`signal_health` (step 15d, `signal_health.py`, limits in `signal_health.json`): one status per source
(MacroRAG, XbrlRag, news, crowding, options, GPR, throughput) from coverage, staleness and holds, rolled
up worst-first. `runner._signal_health` collects it from what the run resolved, logs each degraded/down
source and sets the gauge `scenariogen_signal_status{source}` (0 ok, 1 degraded, 2 down, -1 unused).
News and crowding resolve only on the dual-RAG path, so a static run reports them unused.

The ablation report carries `layer_cost` (step 15e, `layer_cost.py`): per layer its calls, latency and
reported cost from the run's own trace, next to its Shapley contribution, with contribution per second
and per dollar. A traced REST call takes its cost from the response's `usage.cost_usd`
(`CallRecord.cost_reported`); MacroRAG and XbrlRag send none yet, so their cost reads "not reported"
(tracked in the usage-cost hand-offs, kept outside this repo).

`report_views.py` (step 15f, spec `docs/specs/ablation-waterfall-view.md`) holds the pure views the BFF
serves over these reports: `select_report` (newest by `generated_at`, ties to the greatest `run_id`),
`waterfall` / `ablation_view` (the six ablation layers grouped into four §7.1 steps, `WATERFALL_STEPS`:
ladder bars, grouped Shapley, floor hits, calls / latency / cost per step), `signal_health_view` and
`definition_view` (drivers + horizon to show for a tier: report > version store > config > unresolved
replay; restates `to_canonical`'s unit rule). It imports only the stdlib and `ablation.py` — keep it so
(`tests/bff/test_bff_quantlib_free.py`); never import `scenario_gen` there. The Measurement views (spec
`docs/specs/measurement-views.md`) live there too: `coverage_view` (directions in `index` order,
`uncovered_share`, diversity vs target, losses largest first), `decision_view` (stress-set books in
`STANDARD_BOOKS` order then by name; per-source binding change and mean size ratio, `None` ratios left
out) and `replay_view` (authored / enriched nearest scenario with its cosine, `gain`, point-in-time notes).

The stability runner (step 15c, `stability_runner.py`; pure logic in `stability.py`) resolves the dual-RAG
services `--repetitions` times (default 3) at one as_of, each repetition with a fresh provider
(`_build_dual_provider(..., macro_no_cache=True)`: fresh caches and XbrlRag budget, its own preflight; MacroRAG
gets `no_cache` so it re-runs retrieval and synthesis). Everything else (engine, catalog and Σ, anchors, GPR,
throughput) is built once. It reports each leg's spread per source (`macrorag`, `xbrlrag` = calibrated
`company_drivers`, `xbrlrag_seed` = narrative seeds; an absent leg is missing, not zero) and each tier's P&L
range, plus `latency_by_repetition` from its own trace (`stability_<run>.json`). Numbers only: no threshold, no
forward-log rows; the stress and ablation runners keep `no_cache` off.

## Sibling services & config

ARag (the XbrlRag repo) and MacroRAG are **separate repos**; deterministic
path reaches them via REST, agentic path via stdio MCP subprocesses. Shared inference at
`RAGINFER_URL` (`127.0.0.1:8799`). Locations are env-overridable in one place (**F6**,
`${VAR:-default}`): `ARAG_BASE_URL`, `MACRORAG_BASE_URL`, `ARAG_HOME`, `MACRORAG_HOME`, `MCP_PYTHON`,
`RAGINFER_URL`. Config in `configs/scenariogen/` (`covariance/scenarios/plausibility/macro/arag/limits/liquidity/
collateral/news` + `llm/`; `covariance.json` carries the F7 `implied_corr_regime` block); `macro`/
`arag`/`mcp` go through env expansion. ARag base_url is deliberately `:8001`
(must not collide with MacroRAG `:8000`).

## Conventions & gotchas

- **`__init__.py` re-exports are lazy (PEP 562) — keep them that way.** `stress.py` reaches
  `src.pricer.engine` → QuantLib, and the BFF image ships **without** the QuantLib wheel
  (`infra/bff/Dockerfile`), so a module-level `from .x import y` here makes *every*
  `from src.scenariogen.<sub> import …` (e.g. the BFF's `config`/`news.*` imports) drag in the whole
  pricing stack and crash the container at startup. Add new exports to `_LAZY_ATTRS` + `__all__`,
  never as a top-level import. Guarded by `tests/bff/test_bff_quantlib_free.py`.
- **Don't cross fail-loud and graceful.** `AragBudgetError` must stay a `RuntimeError` (not an
  `AragTransportError`) or the per-scenario handler swallows it (`budget.py`).
- **ARag capability surface is single-sourced** in `arag/capabilities.py`; changing an endpoint/tool
  means updating the catalog (a drift test enforces `mcp.json` ↔ catalog).
- Edit the scenario set via `authoring.py apply`, not by hand-editing the three configs in lockstep.
- Config default `freshness_days` (macro) is 120 in code but **550** in deployed `macro.json` — config wins.
- **F1's liquidity-horizon table is a signed-off governance artifact** (`liquidity.json` → `signoff`,
  Basel/FRTB). It reprices every scenario's exit value, so changing a horizon is a desk decision, not a
  data tweak — update `signoff` (who/when/why) in lockstep. The margined/unmargined tag (F2) lives in
  `collateral.json`, deliberately **not** `portfolio.json`, to keep it out of the pricing/golden-PV fingerprint.
- Tests: `pytest tests/scenariogen` (`… /llm` for the agentic subset), `ruff check src/scenariogen`,
  `mypy`. Contract fixtures for ARag: `tests/scenariogen/fixtures/arag/` (F7).
