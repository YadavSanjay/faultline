# QuantLib Portfolio Platform — UI Design & Analysis

> **Status:** Analysis & planning (no implementation yet)
> **Scope:** React UI for the four subsystems — Market Data, Pricing, Risk, Stress Scenario Generation
> **Two lenses per subsystem:** (1) Business — end-user + internal-maintenance usage; (2) Technical/System — component exploration + end-to-end request tracing
> **Special requirement:** Calculation lineage for Pricing and Risk (all market & static inputs → intermediate values → final values)
>
> **Deep dives:** `PRICING_UI.md` · `RISK_UI.md` · `SCENARIOGEN_UI.md` · `MARKETDATA_UI.md` · `CROSSCUTTING_UI.md` (System/Ops + Lineage + Governance).
> **➡ Execution: `BUILD_PLAN.md` is the canonical, sequenced build plan to work from (phases, workstreams, dependencies, decisions).**

---

## 1. Design Goals & Principles

1. **One logical drill-down ladder per subsystem.** Every subsystem is navigated top→down: *aggregate number → grouping → entity → inputs → intermediate calc → atomic source*. The same "ladder" metaphor is reused everywhere so users learn it once.
2. **Two lenses, one spine.** Business and Technical views share the same entities (trade, feed, run, scenario) but answer different questions. A persistent **Business ⇄ Technical** toggle keeps the user oriented without duplicating navigation.
3. **Lineage is a first-class object, not a tooltip.** For Pricing and Risk, "why is this number what it is?" is a full-screen, expandable graph that bottoms out at a market-data point with its provenance hash.
4. **Provenance everywhere.** Every number is stamped with `run_id`, `snapshot_id`, `git_sha`, `config_hash`. These are always one click from reproducibility detail.
5. **Read-mostly, safe-by-default.** The UI is primarily an *explorer/observability* tool. The few write actions (trigger a run, record a sign-off, edit a limit) are gated, audited, and visually distinct.
6. **Elaborate but not overwhelming.** Progressive disclosure: each page shows ~1 altitude of detail with clear affordances to descend. No page tries to show everything.

---

## 2. Personas → what they use

| Persona | Primary lens | Cares about |
|---|---|---|
| **Portfolio / desk user** (business end user) | Business | Book value, risk/capital headline, scenario P&L, "what's my exposure" |
| **Risk / control analyst** (business internal) | Business | VaR/ES, limit breaches, backtest zone, scenario sign-off, DQ status |
| **Market-data steward** (business internal) | Business | Feed freshness, source health, DQ gate, feed↔trade coverage |
| **Quant / model owner** (technical) | Technical | Calculation lineage, methodology, intermediates, governance (IPV/golden) |
| **Platform / SRE** (technical) | Technical | Service health, run history, request traces, provenance, config, module map |

---

## 3. Global Information Architecture

Top-level navigation has **4 subsystem workspaces** + **4 cross-cutting areas**. Every subsystem workspace carries the **Business ⇄ Technical** toggle.

```
┌─────────────────────────────────────────────────────────────────────┐
│  TOP BAR:  [Portfolio: AI-Themed]  [Valuation date ▼]  [Snapshot ▼]  │
│            [Business ⇄ Technical]   [run_id badge]   [search]         │
├──────────────┬──────────────────────────────────────────────────────┤
│  LEFT NAV    │   MAIN CANVAS (drill-down ladder)        RIGHT RAIL    │
│              │                                          (context:     │
│  ◇ Cockpit   │                                           provenance,  │
│  ─ SUBSYSTEMS│                                           lineage      │
│  ▸ Market    │                                           shortcut,    │
│    Data      │                                           warnings)    │
│  ▸ Pricing   │                                                        │
│  ▸ Risk      │                                                        │
│  ▸ Scenarios │                                                        │
│  ─ CROSS-CUT │                                                        │
│  ▸ Lineage   │                                                        │
│  ▸ System/Ops│                                                        │
│  ▸ Governance│                                                        │
│  ▸ Admin     │                                                        │
└──────────────┴──────────────────────────────────────────────────────┘
```

### 3.1 Area map (main areas → sub-areas)

```
0. COCKPIT (landing)                         — portfolio-wide snapshot across all 4 subsystems

1. MARKET DATA
   Business:  Feed Catalog → Feed Detail → Data Viewer ; Feed↔Trade Coverage ; DQ Dashboard
   Technical: Source/Adapter Explorer ; Ingestion Run Trace ; Provenance & PIT Explorer

2. PRICING (Valuation)
   Business:  Book Valuation → Trade Valuation → **Calculation Lineage** ; PV History ;
              Internal: Model Inventory, Governance (IPV/Golden)
   Technical: Engine Component Explorer ; Price Request Trace ; Curve Cache / Workers

3. RISK
   Business:  Risk Dashboard → Asset-Class/Factor/Trade drill → **Methodology & Inputs** ;
              P&L Explain ; Backtest ; Internal: Limits
   Technical: Risk Engine Explorer ; Risk Run Trace ; Factor History / Full-reval Workers

4. STRESS SCENARIOS
   Business:  Scenario Catalog → Scenario Detail → Scenario Results ; Reverse Stress ;
              Internal: Sign-off, Plausibility, Versioning
   Technical: Generation-Pipeline Explorer ; Generation Trace ; MacroRAG Health ; Store/GC

CROSS-CUTTING
   L. Lineage Explorer        — unified trade lineage (pricing + risk), provenance graph
   S. System / Ops Console    — health, run history (all modules), end-to-end trace, module map, config
   G. Governance & Controls   — model inventory, IPV, golden, scenario sign-off (consolidated)
   A. Admin / Config          — environments, settings, users, feature flags
```

---

## 4. Cross-cutting concepts (reused components)

These are the shared building blocks every subsystem composes. Designing them once keeps the app coherent.

| Concept | Component | Used by |
|---|---|---|
| **Drill-down ladder** | `LadderShell` (breadcrumb + level header + descend affordances) | all 4 subsystems |
| **Provenance chip** | `ProvenanceBadge` (run_id / snapshot_id / git_sha / config_hash → popover) | everywhere |
| **Lineage graph** | `LineageGraph` (React Flow DAG: final value → inputs → sources) | Pricing, Risk |
| **Number-with-context** | `MetricCell` (value + sign color + unit + warning + drill link) | Pricing, Risk |
| **Time series** | `SeriesChart` (PV history, curve evolution, vol index, VaR backtest) | Market Data, Pricing, Risk |
| **Curve viewer** | `CurveChart` (tenor × rate; zero/discount/forward overlays) | Market Data, Pricing lineage |
| **Run trace timeline** | `RunTrace` (request → stages → timings, correlated by run_id) | all Technical lenses |
| **DQ status** | `DqBadge` (ok / warn / block; staleness, nulls, rows) | Market Data, Pricing |
| **Status traffic-light** | `Zone` (green/amber/red) | Risk backtest, limits, plausibility |
| **Component/architecture map** | `ModuleMap` (interactive package + data-flow diagram) | System/Ops, all Technical lenses |

**The "ladder" pattern in one sentence:** each subsystem's Business view is the same shape — *headline aggregate → one or two grouping levels → a single entity → that entity's inputs → the calculation → the atomic, provenance-stamped source*.

---

## 5. Subsystem 1 — MARKET DATA

> **Deep dive:** see `ui/docs/MARKETDATA_UI.md` for the full Market Data UI plan (feed catalog → detail → data viewer, feed↔trade coverage, DQ, the greenfield BFF read layer, and build plan).

*Backend reality:* market data is **library-only today** (no REST). UI requires new BFF read endpoints (see §10). Rich domain already exists: 8 asset classes, per-source downloaders, per-CSV provenance sidecars, content-addressed snapshots, a DQ gate, and explicit `portfolio[]` linkage in each asset-class config.

### 5.1 Business lens

**Ladder:** Feed Catalog → Feed Detail → Data Viewer

#### Page MD-B1 — Feed Catalog (L1: "what feeds exist, linked to sources")
- Grid grouped by **asset class** (Rates, Credit, Equity, FX, Options, Bonds, Stress, Reference).
- Per feed row: name, **source** (NY Fed / FRED / Pensford / DTCC / ECB / CBOE / Yahoo / EIA…), enabled flag, **last-fetch** timestamp, **freshness** (days vs valuation date), row count, date range, **DQ badge**.
- Filters: asset class, source, status (fresh/stale/blocked), "used by trade".
- Click feed → MD-B2.

#### Page MD-B2 — Feed Detail (L2: "everything about one feed")
- **Source linkage panel:** source URL, auth type, timeout, rate-limit/retry policy, output file(s).
- **Provenance panel:** `fetched_at`, `git_sha`, `content_sha256`, `row_count`, `columns`, `date_min/date_max` (from `<file>.provenance.json`).
- **DQ panel:** latest CheckResults (present/readable/min_rows/required_columns/staleness/null_fraction) with severity.
- **Consumers panel:** trades that consume this feed (link to Feed↔Trade coverage and to each trade's pricing lineage).
- **History:** ingestion runs that touched this feed (link to Technical ingestion trace).
- Click "view data" → MD-B3.

#### Page MD-B3 — Data Viewer (L3: "the actual values")
- Tabular + chart view of the CSV contents (e.g., SOFR rates over time, Pensford forward curve, VIX history, FX series).
- For curves: `CurveChart` (par rates by tenor). For series: `SeriesChart` with date range.
- "As-of" selector that mirrors the pricing PIT selection (snapshot pick).

#### Page MD-B4 — Feed ↔ Trade Coverage ("relationship with portfolio/trades")
- Bi-directional matrix: rows = trades (1–38), cols = feeds; cell = primary/secondary dependency.
- Select a trade → highlight its feed dependencies (primary vs fallback). Select a feed → highlight dependent trades.
- This is the bridge to the Pricing lineage (clicking a cell jumps into PR-B3 market-data inputs).

#### Page MD-B5 — DQ Dashboard (internal maintenance)
- Portfolio-wide DQ posture: counts by severity, stale feeds, blocking gaps.
- Per-file detail (staleness days, null %, missing columns), filterable by asset class/date.
- Link each finding to the feed detail and the ingestion run that produced it.

### 5.2 Technical lens

#### Page MD-T1 — Source / Adapter Explorer
- Component tree of `src/marketdata/<asset_class>/` downloaders + `src/pricer/loader/<asset>_adapter.py`.
- For each: source config, transport (retry/backoff/rate-limit), output mapping, the loader adapter that consumes it, the domain data class it produces (`ParOISCurveData`, `EquitySpotData`, `CdsSpreadData`, …).

#### Page MD-T2 — Ingestion Run Trace
- Pick an ingestion run → `RunTrace`: pipeline start → per-source fetch (status, rows, duration) → DQ gate → snapshot fingerprint → manifest write.
- Surfaces structured-log context (run_id, source, asset_class) and Prometheus counters (`marketdata_dq_total`).

#### Page MD-T3 — Provenance & PIT Explorer
- Browse snapshot manifests (`<snapshot_id>.json`): per-file `sha256`, size, mtime, missing files.
- "Diff two snapshots" to see exactly which feeds changed between two pricing runs.

---

## 6. Subsystem 2 — PRICING (Valuation)

> **Deep dive:** see `ui/docs/PRICING_UI.md` for the full build-ready Pricing spec (instrument-adaptive valuation cards, the lineage page in detail, wireframes, BFF `/lineage` contract per instrument, and build order).

*Backend reality:* `/price`, `/price-portfolio`, `/results/*` exist. **No lineage endpoint** — but all inputs (declared requirements, resolved curve/vol data), intermediates (zero rates, discount factors, forwards, cashflow schedules) and outputs (`pv`, `leg_pvs`, `measures`, `diagnostics`, `provenance`) are computable. BFF `/lineage/{trade_id}` is the key new endpoint (§10).

### 6.1 Business lens

**Ladder:** Book Valuation → Trade Valuation → **Calculation Lineage** (the headline feature)

#### Page PR-B1 — Book Valuation (L1)
- Headline total book PV (from `/results/book/{date}` or `/price-portfolio`), with **by-asset-class** breakdown (rates, bonds, equity, options, credit, fx) and trade counts.
- Status chips: n_ok / n_blocked / n_failed, warnings, drift alerts.
- Click asset class → filtered trade list; click trade → PR-B2.

#### Page PR-B2 — Trade Valuation (L2)
- For one trade: `pv`, `leg_pvs` (e.g. fixed/float), `measures` (fair_rate, dv01, vega, delta, clean/dirty price, ytm, hazard, fx_delta — instrument-specific), `diagnostics` (engine, dates, direction), `warnings`.
- `ProvenanceBadge` (run_id, snapshot_id, git_sha, config_hash, pricer_class, priced_at).
- Two descend buttons: **"Explain this value" → PR-B3 (Lineage)** and **"PV history" → PR-B4**.

#### Page PR-B3 — **Calculation Lineage** (L3 — the centerpiece)
A full-screen, expandable graph + inspector. Three stacked tiers, top→down:

1. **Final values** — `pv`, `leg_pvs` (each leg expandable).
2. **Intermediate values** — per leg: cashflow schedule (dates, accrual factors, coupon, projected rate, **discount factor**, PV per flow); curve-derived **zero rates / discount factors / forward rates** at the tenors used; for options: per-leg implied vol, d1/d2, delta/gamma; for CDS: hazard rate, survival prob; for FX: CIP-implied forward, foreign/USD discount factors.
3. **Inputs** split into:
   - **Market data inputs** — the resolved `MarketDataRequirement`s (OIS curve, normal vol, equity spot, CDS spread, FX): each shows source feed, `as_of_date`, staleness warning, and a link straight to **Market Data → Feed Detail (MD-B2)** and the underlying value.
   - **Static / reference inputs** — trade economics (notional, direction, coupon, schedule, strike), conventions (day count, calendar, business-day rule, frequency), from `portfolio.json` + conventions.

Interaction: click any node → right-rail inspector with the exact number, its formula/role, and (for inputs) provenance. The whole graph is one `LineageGraph` instance; selecting the final PV traces the full path down to atomic sources.

> This page satisfies: *"all market data and static data inputs, key intermediate values and final calculated values."*

#### Page PR-B4 — PV History
- `SeriesChart` of PV per valuation date (`/results/pv-history/{trade_id}`) with status overlay and run links.

#### Internal maintenance (business-internal)
- **PR-B5 Model Inventory** — `/governance/model-inventory`: per trade type the model, engine, assumptions, limitations, approved use, IPV controls; coverage gaps highlighted.
- **PR-B6 Governance Verify** — `/governance/verify`: golden regression breaks (expected vs actual vs tol), IPV controls (identity/method/sanity pass/fail), inventory coverage. Pass/fail banner.

### 6.2 Technical lens

#### Page PR-T1 — Engine Component Explorer
- Interactive 3-layer diagram: **input** (Trade, requirements) → **loader** (MarketDataLoader → per-asset adapters → snapshot) → **ql** (factory → asset pricers → engines). Click a node to see its file, responsibility, and the data class it emits.

#### Page PR-T2 — Price Request Trace
- `RunTrace` of one `/price` call: build ctx → snapshot pin → `market_data_requirements()` → `loader.resolve()` (per-requirement, with staleness) → `get_pricer()` → QL build (curve-cache hit/miss) → result + provenance stamp. Per-stage timings.

#### Page PR-T3 — Curve Cache & Workers
- Curve-cache stats (bootstraps run vs cache hits) for portfolio runs; `--workers` parallelism view; latency histograms (`pricer_price_seconds`).

---

## 7. Subsystem 3 — RISK

> **Deep dive:** see `ui/docs/RISK_UI.md` for the full build-ready Risk spec (VaR/sensitivity/backtest/limits pages, the sensitivity-lineage reuse of the Pricing graph, the VaR-construction histogram, wireframes, BFF contracts, and build order).

*Backend reality:* `/risk/sensitivities` (single trade), `/risk/var` (book), `/risk/scenario` exist. The full `RiskReport` (sensitivities + scenarios + VaR + full-reval VaR + P&L-explain + limits + backtest) is produced by the **CLI runner** and written to JSON — UI needs a `/risk/report` BFF endpoint to surface it (§10). ~38 risk factors; one consistent bump-and-revalue methodology.

### 7.1 Business lens

**Ladder:** Risk Dashboard → drill (asset class / factor / trade) → **Methodology & Inputs**

#### Page RK-B1 — Risk Dashboard (L1: "the headline risk/capital number")
- Headline **VaR & ES** by confidence level (0.95/0.975/0.99), portfolio.
- **Book sensitivities** headline factors (IR_PARALLEL, CS01, VEGA_RATES, EQUITY_DELTA, FX_DELTA).
- **Limit status** banner (breaches, near-misses), **backtest zone** traffic-light (green/amber/red, exceptions, Kupiec LR).
- **Scenario P&L** mini-grid (worst scenarios).
- `ProvenanceBadge` with `run_id`, `snapshot_id`, `factor_snapshot_id`.

#### Page RK-B2 — Drill: by Asset Class / by Factor / by Trade (L2)
- Three pivoting views of the same book:
  - **By asset class** — sensitivities & scenario P&L aggregated per class.
  - **By factor** — every trade's exposure to a chosen factor (e.g. `IR_KR:5Y`, `EQUITY_DELTA:AAPL`), `factor_stdev`, contribution to VaR.
  - **By trade** — one trade's full sensitivity vector and its contribution to book VaR.

#### Page RK-B3 — **Methodology & Inputs** (L3: "understand the calculation")
- **VaR methodology card:** method (sensitivity-based historical sim vs full-revaluation), lookback_days, horizon, confidence levels, n_scenarios, **missing_factors** (explicitly flags understated risk).
- **Sensitivity lineage:** for a chosen factor, show bump-and-revalue mechanics — base PV, +bump PV, −bump PV, central difference, bump unit. (This is Risk's analogue of pricing lineage; reuses `LineageGraph`.)
- **Factor inputs:** factor → source feed (FRED tenor, VIX, MOVE, SKEW, FX), historical window, alignment.
- Link each factor back to **Market Data** feed detail.

#### Page RK-B4 — P&L Explain
- For a scenario: **actual** (full-reval) vs **explained** (Σ sensitivity×shock) vs **residual**, with coverage %; per-factor and per-trade attribution bars.

#### Page RK-B5 — Backtest
- Rolling exception timeline, expected vs realized exceptions, zone, Kupiec LR; clean/exception days marked.

#### Internal maintenance
- **RK-B6 Limits** — view/edit configured VaR & sensitivity limits; breach history; near-miss alerts.

### 7.2 Technical lens

- **RK-T1 Risk Engine Explorer** — components: `sensitivities`, `bumps`, `var`, `historical_var`, `scenarios`, `pnl_explain`, `backtest`, `limits`, `factor_history`; how each consumes the pricing engine.
- **RK-T2 Risk Run Trace** — `RunTrace` of a risk run; factor-history snapshot load; full-reval VaR worker fan-out (per-chunk repricing).
- **RK-T3 Factor History / Snapshots** — factor alignment matrix, `factor_snapshot_id`, per-factor data coverage & gaps.

---

## 8. Subsystem 4 — STRESS SCENARIOS

> **Deep dive:** see `ui/docs/SCENARIOGEN_UI.md` for the full Scenarios spec (catalog → detail → results, the Shock Derivation / correlation-heatmap centerpiece, reverse stress, sign-off/plausibility/versioning, MacroRAG provenance, BFF contracts, and a build plan for tackling this slice first).

*Backend reality:* only a thin `/risk/scenario` exists. The rich scenariogen (covariance Σ, conditional-covariance materialization, severity tiers, ES-under-shock capital, plausibility gate, versioning, **MacroRAG** driver sourcing, reverse stress, sign-off, dashboard) is **CLI/library-only** — UI needs several BFF endpoints (§10).

### 8.1 Business lens

**Ladder:** Scenario Catalog → Scenario Detail → Scenario Results

#### Page SC-B1 — Scenario Catalog (L1: "overall scenarios")
- Grid of named scenarios × severity tiers (mild/adverse/severe): `taiwan_geopolitical`, `ai_bust`, `rate_capital`, `keyman_nvda` …
- Per row: short narrative, horizon (days), **plausibility status** (pass/flag/fail), **sign-off status** (approved/rejected/pending), `scenario_version`, last run time, **total P&L** & **capital delta** headline.

#### Page SC-B2 — Scenario Detail (L2: "brief description, what it tests, how shocks were arrived at")
- **Narrative & trigger:** what the scenario represents and tests.
- **Drivers & overlays:** explicit driver shocks (e.g. `fx:TWD +8%`, `eq:TSM −25%`) + idiosyncratic overlays.
- **How shocks were derived (methodology):**
  - **Conditional-covariance propagation** — Σ window (full/ewma/crisis), `cov_id`, n_obs; show how driver shocks propagate to all ~38 factors via the Gaussian conditional mean.
  - **MacroRAG sourcing** (if used) — synthesis question, retrieved chunks with publisher/date/rerank score, per-driver **citations & rationale**, `world_view_id`, freshness/citation gate result, or fallback-to-static reason.
- **Materialized factor vector:** full shocked-factor table (native units).
- **Metadata:** `scenario_version`, `cov_id`, severity_scale, generation time, plausibility findings.

#### Page SC-B3 — Scenario Results (L3)
- **Total P&L** and **capital delta** (ES_shocked − ES_base, level 0.975), per-asset-class P&L, per-trade P&L (delta/gamma/vega/theta decomposition where available).
- Limit-breach flags. Links to the Risk dashboard and to each trade's pricing.

#### Page SC-B4 — Reverse Stress ("what would break us")
- For a scenario shape, solve severity λ that hits the loss limit (bisection); show reachable?, λ*, achieved loss, iterations.

#### Internal maintenance
- **SC-B5 Sign-off** — approve/reject a `scenario_version` with approver + note; latest-wins history (audit log).
- **SC-B6 Plausibility** — bound (hard) and envelope (n-sigma soft) findings per scenario/tier; promotable flag.
- **SC-B7 Versioning** — version history (immutable specs), covariance config.

### 8.2 Technical lens

- **SC-T1 Generation-Pipeline Explorer** — S0 covariance → S1 materialize → S2 tiers → S3 plausibility → S4 MacroRAG → stress/capital → reverse. Click a stage for its component + config.
- **SC-T2 Generation Trace** — `RunTrace` of a scenario run: counts (n_run, n_held_gate, n_held_signoff, n_breaches), durations.
- **SC-T3 MacroRAG Health** — client status (healthz, circuit-breaker state, retries), `scenariogen_macro_fallback_total{reason}`, last synthesis calls.
- **SC-T4 Store / Retention / GC** — scenario versions, results store, sign-off store, retention policy (prune by age/count).

---

## 9. Cross-cutting areas

> **Deep dive:** see `ui/docs/CROSSCUTTING_UI.md` for the full spec of all three areas (the provenance "correlation spine", System/Ops run-trace + pipeline DAG + module map, the unified Lineage Explorer, the Governance Control Room, BFF contracts, and build plan).

### 9.1 Lineage Explorer (L)
A unified entry point that, given a trade, assembles **pricing lineage (PR-B3)** + **risk sensitivity lineage (RK-B3)** + **market-data provenance (MD-T3)** into one navigable graph, and lets you pivot by `run_id` / `snapshot_id`. This is where the "high→low" promise is fully realized across subsystems.

### 9.2 System / Ops Console (S) — the heart of the Technical lens
- **Health** — `/health` (snapshot_id, QuantLib version), `/metrics` (Prometheus) rendered as gauges.
- **Run History (all modules)** — `/runs` plus risk & scenariogen manifests: timeline of every run with status, counts, duration, provenance. This is the cross-module "what happened" view.
- **End-to-end Request Trace** — pick a `run_id` → reconstruct the trace from structured logs + results store: stages, timings, per-trade outcomes, warnings, drift alerts. **This is the "trace an actual request flowing through parts of the system end-to-end" requirement.**
- **Module / Architecture Map** — interactive `ModuleMap` of `src/` packages and the marketdata→pricing→risk→scenariogen data flow; click a node to jump into that subsystem's Technical explorer.
- **Provenance / Reproducibility** — given a run, show snapshot_id, portfolio_version, git_sha, config_hash, and a "how to reproduce" recipe.
- **Config Explorer** — effective config per module, env overlay (dev/uat/prod), `PRICER_*` settings (secrets masked).

### 9.3 Governance & Controls (G)
Consolidates model inventory, IPV, golden gate (Pricing) and scenario sign-off (Scenarios) into one control room with an overall pass/fail posture.

### 9.4 Admin / Config (A)
Environment switch, feature flags, user/role context, UI preferences.

---

## 10. API gap analysis & BFF plan

> **Decision (locked):** the UI is backed by a thin **Backend-for-Frontend (BFF)** under `ui/bff/`. It (a) proxies the existing FastAPI service and (b) adds read endpoints over the Parquet/DuckDB stores, config JSON, and provenance sidecar files. Recommended implementation: **FastAPI** (reuses the existing Python stack, DuckDB, pydantic models, and provenance/store code directly — no re-implementation), exposing an **OpenAPI** spec the frontend generates typed clients from.

The existing service is pricing/risk-centric and lacks the read surface the UI needs, so the BFF fills the gap.

### Existing endpoints (reuse directly)
`GET /health` · `POST /price` · `POST /price-portfolio` · `GET /results/pv-history/{trade_id}` · `GET /results/book/{date}` · `GET /results/run/{date}` · `POST /risk/sensitivities` · `POST /risk/scenario` · `POST /risk/var` · `GET /metrics` · `GET /runs` · `GET /portfolio/versions` · `GET /governance/model-inventory` · `POST /governance/verify`

### New endpoints needed (BFF)
| Need | Proposed endpoint | Backed by |
|---|---|---|
| Market-data feed catalog | `GET /md/feeds` | asset-class configs + provenance sidecars |
| Feed detail + provenance | `GET /md/feeds/{id}` | `<file>.provenance.json` |
| Feed time-series / curve | `GET /md/feeds/{id}/series` | the CSV itself |
| DQ results | `GET /md/dq` | DQ gate run output |
| Feed↔trade coverage | `GET /md/coverage` | `portfolio[]` in configs |
| Snapshot manifest / diff | `GET /md/snapshots`, `/md/snapshots/{id}` | snapshot manifests |
| **Calculation lineage** | `GET /lineage/{trade_id}` | requirements + resolved data + intermediates (new engine hook) |
| Full risk report | `GET /risk/report` | risk-run JSON (`RiskReport`) |
| Backtest / limits / pnl-explain | `GET /risk/backtest`, `/risk/limits`, `/risk/pnl-explain` | risk-run JSON |
| Scenario catalog | `GET /scenarios` | `scenarios.json` + scenario store manifest |
| Scenario detail (materialized) | `GET /scenarios/{name}` | materialized scenario + provenance |
| Scenario results | `GET /scenarios/{name}/results` | scenario results store |
| Reverse stress | `POST /scenarios/{name}/reverse` | reverse solver |
| Sign-off list/record | `GET/POST /scenarios/signoff` | sign-off store |
| MacroRAG health | `GET /scenarios/macro/health` | MacroRAG client |
| Run detail / trace | `GET /runs/{run_id}` | run manifest + logs + results |
| Config explorer | `GET /system/config` | merged config + settings (masked) |
| Module map | `GET /system/modules` | static metadata |

> The single highest-value backend addition is **`GET /lineage/{trade_id}`** — it unlocks both the Pricing centerpiece (PR-B3) and the Risk methodology view (RK-B3).

---

## 11. Frontend technical architecture (locked)

> **Decision (locked):** the stack below is committed.

- **Toolchain:** Node v24.14.0; **Vite + React + TypeScript**.
- **Routing:** React Router (nested routes mirror the area map; lens is a route segment or persisted toggle).
- **Server state:** TanStack Query (caching, background refresh, run/snapshot keying).
- **Tables/grids:** TanStack Table (financial grids: sensitivities, cashflows, DQ).
- **Charts:** ECharts for series/curves/backtest; performant for large series.
- **Graphs/diagrams:** React Flow for `LineageGraph`, `ModuleMap`, pipeline explorers.
- **Design system:** Tailwind + shadcn/ui — consistent, accessible primitives.
- **API clients:** typed clients generated from the BFF's OpenAPI spec.
- **Auth:** none today; design for a pluggable token/header at the BFF (the service is currently unauthenticated — see §12).

### Proposed `ui/` folder layout
```
ui/
  docs/UI_DESIGN.md            ← this document
  bff/                         ← Backend-for-Frontend (FastAPI or Node) — new read endpoints
  app/
    src/
      shared/                  ← LadderShell, ProvenanceBadge, LineageGraph, MetricCell,
                                  SeriesChart, CurveChart, RunTrace, DqBadge, Zone, ModuleMap
      api/                     ← typed clients (generated from OpenAPI where possible)
      features/
        cockpit/
        marketdata/   { business/, technical/ }
        pricing/      { business/, technical/ }   ← lineage lives here
        risk/         { business/, technical/ }
        scenarios/    { business/, technical/ }
        lineage/      ← cross-cutting explorer
        system/       ← ops console
        governance/
        admin/
      routes/
      app.tsx
    index.html  vite.config.ts  package.json  tsconfig.json
```

---

## 12. Risks / open questions for implementation

1. **No auth on the service.** UI exposes pricing/risk/governance broadly; before any non-local deployment, add auth at the BFF (ties into backlog items 9–10).
2. **Lineage hook is new work.** The engine computes intermediates but doesn't currently emit them; PR-B3 depends on the `/lineage` endpoint exposing zero rates / discount factors / forwards / cashflow schedules.
3. **Scenariogen has no API.** Most of the Scenarios workspace needs new BFF endpoints over the CLI/library + stores.
4. **Live vs batch.** `/price` etc. are live against the startup snapshot; most history comes from Parquet stores. UI must clearly label "live re-price" vs "stored run."
5. **Write actions** (sign-off, limit edits, trigger runs) need audit + RBAC — keep them few and explicit.

---

## 13. Suggested phasing (so it stays buildable, not overwhelming)

- **Phase 0 — Skeleton & shared kit:** app shell, nav, lens toggle, `ProvenanceBadge`, `MetricCell`, mock API.
- **Phase 1 — Pricing business + Lineage:** Book→Trade→**Lineage** (PR-B1/2/3) on `/price` + new `/lineage`. Highest user value.
- **Phase 2 — Risk business:** Dashboard→drill→methodology (RK-B1/2/3) + `/risk/report` BFF.
- **Phase 3 — Market Data business:** Feed Catalog→Detail→Viewer + coverage + DQ (needs MD BFF).
- **Phase 4 — Scenarios business:** Catalog→Detail→Results + sign-off (needs scenario BFF).
- **Phase 5 — Technical lenses + System/Ops Console:** component explorers, run traces, module map, end-to-end trace.
- **Phase 6 — Governance consolidation + Admin + polish.**
```

---

## Appendix A — BFF endpoint contracts (key shapes)

Illustrative response shapes for the highest-value new endpoints. Field names mirror existing domain models so the BFF can serialize them with minimal glue. (Sketches, not final OpenAPI.)

### A.1 `GET /lineage/{trade_id}?valuation_date=&valuation_type=PV` — the centerpiece
Drives Pricing PR-B3 and (its sensitivity section) Risk RK-B3.
```jsonc
{
  "trade_id": "TRADE_22",
  "trade_type": "ois_swap",
  "currency": "USD",
  "valuation_date": "2026-05-29",

  "final": {                                   // tier 1
    "pv": 125000.42,
    "leg_pvs": { "fixed": 125000.42, "float": -0.0 },
    "measures": { "fair_rate": 0.0380, "dv01": 50000.0 }
  },

  "intermediates": {                           // tier 2
    "curves": {
      "USD-SOFR": {
        "tenors":          ["1M","3M","6M","1Y","2Y","5Y","10Y"],
        "par_rates":       [/* ... */],
        "zero_rates":      [/* derived */],
        "discount_factors":[/* derived */],
        "forward_rates":   [/* derived */]
      }
    },
    "cashflows": [                              // per leg, per flow
      { "leg":"fixed","pay_date":"2027-05-29","accrual":1.0028,
        "rate":0.038,"amount":190532.0,"discount_factor":0.9631,"pv":183500.1 }
      // ...
    ]
  },

  "inputs": {                                  // tier 3
    "market_data": [
      { "kind":"OIS_CURVE","purpose":"DISCOUNT","index":"USD-SOFR",
        "as_of_date":"2026-05-29","feed_id":"rates/pensford_sofr_forward.csv",
        "staleness_days":0,"warning":null,
        "provenance":{ "content_sha256":"…","fetched_at":"…" } }
    ],
    "static": {
      "notional":50000000,"direction":"payer","fixed_rate":0.038,"swap_tenor":"10Y",
      "conventions":{ "day_count":"Actual/360","calendar":"US.SOFR",
                      "bdc":"ModifiedFollowing","frequency":"Annual" }
    }
  },

  "sensitivity_lineage": [                      // optional ?with=risk → powers RK-B3
    { "factor":"IR_PARALLEL","base_pv":125000.42,"up_pv":125049.9,
      "down_pv":124950.9,"value":49.5,"bump_unit":"+1bp",
      "method":"central_bump_revalue" }
  ],

  "provenance": { "run_id":"…","snapshot_id":"…","git_sha":"…",
                  "config_hash":"…","pricer_class":"OISSwapPricer","priced_at":"…" }
}
```
*Backend work:* the pricers compute `intermediates` already (curve handles, schedules) but don't emit them — add an opt-in "explain" path on the engine that captures zero/discount/forward at the tenors used and the cashflow table.

### A.2 `GET /md/feeds` and `/md/feeds/{id}` — market data
```jsonc
// GET /md/feeds  → catalog (MD-B1)
{ "feeds": [
  { "id":"rates/pensford_sofr_forward.csv","asset_class":"rates","name":"Pensford SOFR Forward",
    "source":"Pensford","enabled":true,"last_fetch":"…","date_min":"…","date_max":"…",
    "row_count":312,"freshness_days":0,"dq":"ok","used_by_trades":["TRADE_22","TRADE_23"] }
]}
// GET /md/feeds/{id} → detail (MD-B2): + source config (url/auth/timeout/retry),
//   full provenance sidecar, latest Dq CheckResults[], ingestion-run history[]
```

### A.3 `GET /risk/report?valuation_date=` — full RiskReport (RK-B1..B5)
Surfaces the CLI-produced `RiskReport` JSON: `book_sensitivities`, `sensitivities[]`, `scenarios[]`, `var`, `full_reval_var`, `pnl_explain`, `limit_breaches[]`, `backtest`, plus `run_id`/`snapshot_id`/`factor_snapshot_id`.

### A.4 `GET /scenarios` and `/scenarios/{name}` — scenarios
```jsonc
// GET /scenarios → catalog (SC-B1): name × severity tiers with status
{ "scenarios": [
  { "name":"taiwan_geopolitical","severity":"adverse","scenario_version":"a1b2…",
    "narrative":"Export-control escalation + foundry disruption","horizon_days":10,
    "plausibility":"flag","signoff":"approved","total_pnl":-12500000,"capital_delta":4200000,
    "last_run":"…","used_macro":true } ] }
// GET /scenarios/{name} → detail (SC-B2): drivers{}, overlays{}, covariance{window,cov_id,n_obs},
//   materialized factor vector{}, macro{ summary, world_view_id, drivers:[{leg,origin,citations[],rationale}], gate },
//   plausibility findings[]
```

### A.5 `GET /runs/{run_id}` — end-to-end trace (System/Ops)
Run manifest + ordered stages (with durations) + per-trade outcomes + warnings + drift alerts, correlated by `run_id` across pricing/risk/scenariogen.

---

## Appendix B — Wireframes (centerpiece pages)

ASCII sketches to anchor layout; not pixel-final.

### B.1 Cockpit (landing)
```
┌ AI-Themed Portfolio ───────────── val 2026-05-29 · snapshot 56a863cf · run 15054ab1 ┐
│ ┌ Book Value ───────┐ ┌ Risk / Capital ──────┐ ┌ Data Health ────┐ ┌ Scenarios ───┐ │
│ │ PV  $ 312.4m      │ │ VaR99  $0.51m         │ │ 38 feeds        │ │ 4×3 tiers    │ │
│ │ ▲ rates  bonds    │ │ ES99   $0.62m         │ │ ✓34 ⚠3 ✗1       │ │ worst: AI    │ │
│ │ equity fx credit  │ │ backtest ● green      │ │ stalest: CBC 6d │ │ bust -$28m   │ │
│ │ 38 trades · 1 ⚠   │ │ limits: 0 breaches    │ │                 │ │ 1 pending ✍  │ │
│ └───────────────────┘ └──────────────────────┘ └─────────────────┘ └──────────────┘ │
│ Recent runs ▸ pricing 2026-05-29 ok · risk ok · scenariogen 11 run / 1 held-gate     │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### B.2 Pricing — Calculation Lineage (PR-B3)
```
TRADE_22 · 10Y SOFR payer IRS            [Business ⇄ Technical]   prov: run · snap · sha
Breadcrumb: Book › rates › TRADE_22 › Lineage
┌ LINEAGE GRAPH (React Flow) ─────────────────────────────┐ ┌ INSPECTOR ──────────────┐
│  ① FINAL                                                │ │ node: discount_factor   │
│     [PV 125,000.42]                                      │ │ date 2027-05-29         │
│        ├─[leg fixed 125,000.42]                          │ │ df = 0.9631             │
│        └─[leg float ~0]                                  │ │ from zero 3.84% @ curve │
│  ② INTERMEDIATES                                         │ │ USD-SOFR (DISCOUNT)     │
│     [cashflow schedule ▸] [zero rates] [disc factors]    │ │                         │
│     [forward rates]                                      │ │ drill ▸ feed detail     │
│  ③ INPUTS                                                │ │ Pensford as-of 05-29    │
│     market: (OIS DISCOUNT)→Pensford  (OIS FORECAST)→…    │ │ sha 9f3a… fetched …     │
│     static: notional 50m · payer · fixed 3.8% · Act/360  │ │ staleness 0d ✓          │
└─────────────────────────────────────────────────────────┘ └─────────────────────────┘
```

### B.3 Risk — Dashboard (RK-B1)
```
Risk Dashboard · val 2026-05-29   prov: run 15054ab1 · snap 56a863cf · factors e1f5a9c3
┌ VaR / ES ────────────┐ ┌ Book sensitivities ─────────┐ ┌ Controls ─────────────────┐
│ 95%  0.245m / 0.288m │ │ IR_PARALLEL  -288,017       │ │ backtest ● green (3 exc)  │
│ 97.5 0.385m / 0.421m │ │ CS01         -236,373       │ │ Kupiec LR 0.15            │
│ 99%  0.512m / 0.624m │ │ EQUITY_DELTA  1,184,897     │ │ limits  0 breaches        │
│ method: hist-sim     │ │ VEGA_RATES    45,652        │ │ missing factors: none     │
│ lookback 250 · 1d    │ │ FX_DELTA      971,929 ▸     │ │                           │
└──────────────────────┘ └─────────────────────────────┘ └───────────────────────────┘
┌ Worst scenarios ─────────────────────────────────────────────────────────────────────┐
│ ai_bust -28.0m │ taiwan_geopolitical -12.5m │ rate_capital -4.1m │ keyman_nvda -3.2m  │
└───────────────────────────────────────────────────────────────────────────────────────┘
[ drill: by asset class · by factor · by trade ]   [ methodology & inputs ▸ ]
```

### B.4 Scenarios — Scenario Detail (SC-B2)
```
taiwan_geopolitical · adverse (×1.5)   ver a1b2c3d4 · cov ewma 9f1c · plausibility ⚑flag
┌ Narrative & trigger ─────────────────────────┐ ┌ How shocks were derived ───────────────┐
│ Export-control escalation + foundry          │ │ drivers: fx:TWD +8% · eq:TSM -25%      │
│ disruption. Tests Taiwan/semis concentration │ │ → conditional-covariance propagation   │
│ and FX/credit spillover. Horizon 10d.        │ │   fills 38 factors (Σ ewma, n=250)     │
│                                              │ │ overlays: eq:TSM -10% · eqvol +5pt     │
│ sign-off: ● approved (S.Yadav, "Q3 review")  │ │ macro: MacroRAG ✓ world_view wv_7c2    │
│                                              │ │   USD/TWD cites 3 chunks (≤120d) ▸     │
└──────────────────────────────────────────────┘ └────────────────────────────────────────┘
┌ Materialized factors ▸ ┐ ┌ Results ▸ total -12.5m · capital Δ +4.2m · by-AC equity -10m ┐
└────────────────────────┘ └─────────────────────────────────────────────────────────────┘
```
