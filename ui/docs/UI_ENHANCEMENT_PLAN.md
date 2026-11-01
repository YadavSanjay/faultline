# UI Enhancement Plan — Post-V1 Functional Review & Roadmap

> **Status:** Approved for execution (2026-07-14). Sequential Phase 1 → 5. Read-only
> stance retained (no gated writes / auth in this program — deferred to the existing
> BUILD_PLAN §11 "Phase 7 — gated writes & live").
>
> **Companion docs:** [`BUILD_PLAN.md`](./BUILD_PLAN.md) (V1 execution plan, now complete),
> [`UI_DESIGN.md`](./UI_DESIGN.md) (master design + principles), plus the per-subsystem
> deep dives (`PRICING_UI.md`, `RISK_UI.md`, `SCENARIOGEN_UI.md`, `MARKETDATA_UI.md`,
> `CROSSCUTTING_UI.md`).

---

## 0. Purpose

V1 delivered a read-mostly explorer across all four subsystems (Market Data, Pricing,
Risk, Stress Scenarios) plus cross-cutting System/Ops, Governance and Lineage, following
one drill-down ladder per subsystem over a provenance spine.

This program addresses three findings from a functional/usage review of that V1:

1. **Altitude gaps** — several screens stop at a single altitude (overview *or* detail)
   instead of the full **overview → drilldown → atomic source** ladder the design mandates.
2. **Unsurfaced backend capabilities** — a few complete engine features have **no UI at
   all** (reverse stress, LLM scenario-gen artifact tree, aggregate LLM cost).
3. **No operational / LLM-cost surface** — cost, token, budget-breaker and RAG-health data
   is computed and stored but discarded at the BFF layer.

**Governing principle (applies to every item, existing and new):** each screen must present
a **high-level overview** (aggregate KPIs + status band) *and* a path down to **atomic
detail** (entity → inputs/method → source, provenance-stamped). Thin screens get their
missing altitude; new screens are born with both.

**Delivery model:** the established **ENG → BFF → FE** three-workstream pattern, shipped one
increment at a time to `main`, ruff + mypy + pytest green per increment.

---

## 1. Review summary — current state & gaps (per module)

### 1.1 Market Data — *solid, no top-level overview*
Present: Feed Catalog, rich Feed Detail (provenance, DQ, consumption, series viewer,
paginated grid), Coverage, DQ Dashboard.
- **G-MD1** No overview dashboard: no all-feeds freshness heatmap, no DQ pass-rate trend, no
  consumption summary ("16 PV / 13 risk / 20 unused") at a glance.
- **G-MD2** Coverage is asset-class-level only — no feed→trade dependency matrix
  ("which trades break if this feed goes stale").
- **G-MD3** DQ / freshness are point-in-time only — no historical DQ trend.

### 1.2 Pricing — *strong drilldown, weak overview*
Present: Book Valuation → Trade Valuation → Calculation Lineage (centerpiece, all 11
instrument types) + PV History + Model Inventory.
- **G-PR1** Only subsystem with no tab-shell / landing overview; Model Inventory & pricing
  docs are button-only.
- **G-PR2** PV History is single-trade only — no book-level or asset-class PV/P&L time-series
  anywhere in the app.
- **G-PR3** No book-level analytics overview (composition, top movers, PV waterfall).

### 1.3 Risk — *dense dashboard, two thin tabs, under-rendered depth*
Present: Dashboard, Drill (risk/asset/factor/trade), Methodology (histogram), P&L Explain.
- **G-RK1** Backtest tab is a single card (delta-gamma only); the stored full-reval /
  Christoffersen backtest and any exception timeline are not surfaced.
- **G-RK2** Limits tab shows nothing when there are zero breaches — no headroom/utilization.
- **G-RK3** Filtered-HS, stressed-VaR and the full diversification-benefit / component-ES
  decomposition are computed & stored but under-rendered (no VaR-family comparison view).

### 1.4 Stress Scenarios — *richest area, two hidden capabilities*
Present: catalog, shock derivation, dual-RAG value-add, results, plausibility, LLM
call-trace, narrative, versions, sign-off.
- **G-SC1** (high value) Reverse stress testing — a complete solver
  (`src/scenariogen/reverse.py` + `runner.run_reverse_stress`) that finds the severity λ
  driving loss to a limit — is CLI-only, persists nothing, has no BFF/UI.
- **G-SC2** The LLM scenario-generation artifact tree (`data/pricer/scenario_llm/<run>/`:
  proposals, evidence, verify, discover phases) is unsurfaced except the final narrative;
  `LLMScenarioArtifactStore.list_runs()` has no consumer.
- **G-SC3** `GET /scenarios/macro/health` exists but is rendered on no screen (orphaned).

### 1.5 Portfolio Construction / Sizing — *a report, not a tool*
Present: Overview (4 profiles + revaluation + comparison table), Profile Detail, Methodology.
- **G-SZ1** No rebalancing / trade-blotter view — the engine computes current→target
  multipliers but the UI never frames them as executable trades.
- **G-SZ2** `expected_returns` derivation (method + detail per trade) is in the payload but
  not rendered.
- **G-SZ3** No risk-return scatter / frontier across mandates; trade covariance / factor
  panel shown only as counts. No book-variant download.

### 1.6 Operational Maintenance / LLM Cost — *does not exist*
All data exists in the backend and is discarded at the BFF layer:
- **G-OP1** `CostTracker.summary()` (per-model $), `RunTrace.summary()` (per-stage $), and
  the `max_total_cost_usd` budget breaker are written to
  `data/pricer/scenario_llm/<run_id>/manifest.json` but no endpoint aggregates them.
- **G-OP2** Cost is reachable only one scenario at a time — no cross-run total, per-model /
  per-stage rollup, budget-vs-actual, breaker status, or MacroRAG/ARag health+fallback view.

### 1.7 Cross-cutting platform
- **G-PL1** No shared DataTable — sort/filter/paginate exists only in the market-data grid;
  ~15 screens hand-roll tables.
- **G-PL2** Lens is cosmetic — it only changes the landing route; it does not tailor nav or
  emphasis.

---

## 2. Phased roadmap

### Phase 1 — Complete the overview↔drilldown ladder *(FE + light BFF, no ENG)* — ✅ DONE (2026-07-14)

Fills altitude gaps on existing screens. All aggregations are computable in the BFF from
already-stored data.

**Delivered:** new BFF routes `/pricing/book/history`, `/md/overview`, `/md/dependency`,
`/risk/limits` (all read-only, no engine change). FE: Pricing tab-shell + Overview (KPI band,
book PV trend total/by-asset-class toggle, composition, top movers); Market Data tab-shell +
Overview (freshness/DQ heatmap, consumption breakdown, per-asset-class table, feed→trade
dependency "blast radius"); Risk Backtest rebuilt (dual sensitivity vs full-reval + Kupiec/
Christoffersen/CC tests + observed-vs-expected chart); Risk Limits rebuilt (utilisation gauges
+ headroom); new Risk **VaR Family** tab (5-method comparison + component-ES/diversification
decomposition). Gates: `tsc -b` + `vite build` clean, ruff clean, no new mypy errors, every
new route HTTP-smoke-tested against stored data. **Known follow-on (needs ENG):** dated per-day
backtest exception timeline — the rolling backtest must persist exception dates (out of Phase 1's
no-ENG scope).

**BFF**
- `GET /pricing/book/history` — book-level & per-asset-class PV/P&L time-series (aggregate
  over `ResultStore` across dates). *(G-PR2)*
- `GET /md/overview` — all-feeds freshness + DQ-verdict matrix + consumption rollup. *(G-MD1)*
- `GET /md/dependency` — feed→trade dependency matrix from the consumption registry +
  portfolio index. *(G-MD2)*
- Extend `/risk/report` consumption (no schema change): surface the existing
  `backtest_full_reval`, `filtered`, `stressed`, and `var.decomposition` blocks.

**FE**
- **Pricing Overview** (new tab-shell + landing): PV-by-asset-class, top movers, valuation-
  status band, book & asset-class PV/P&L trend; drill → Book → Trade → Lineage. *(G-PR1/2/3)*
- **Market Data Overview** (new landing tab): freshness heatmap, DQ pass-rate, consumption
  summary; feed→trade dependency matrix; drill → Feed Detail. *(G-MD1/2)*
- **Risk › Backtest** rebuilt: dual (sensitivity + full-reval) backtest + exception timeline. *(G-RK1)*
- **Risk › Limits** rebuilt: utilization gauges / headroom + trend even at zero breaches. *(G-RK2)*
- **Risk › VaR Family** (new sub-view): HS / filtered-HS / stressed / parametric / full-reval
  side-by-side + diversification-benefit / component-ES decomposition. *(G-RK3)*

### Phase 2 — LLM Cost & Operational Maintenance console *(new BFF + FE, no ENG)* — ✅ DONE (2026-07-14)

New top-level **Operations** section. Data already exists in `scenario_llm/*/manifest.json`
and the trace JSONL; this phase only aggregates and renders it.

**Delivered:** new BFF `ui/bff/app/llmops.py` + routes `/llmops/summary`, `/llmops/runs`,
`/llmops/runs/{run_id}`, `/llmops/traces`, `/llmops/traces/{run_id}`, `/llmops/health` —
aggregating **both** the (currently empty) LLM manifest tree and the dual-RAG call traces
(real data today: 1 trace, 15 calls, $0.018, llm/macrorag/arag split). FE: new **Operations**
nav section + tab-shell with **LLM Cost** (KPI band, per-run budget gauge, cost/activity by
service, per-stage LLM table + model tiers, spend-over-time), **Runs & Traces** (master-detail:
LLM runs → per-stage cost drilldown; dual-RAG traces → per-call list), **RAG Health** (MacroRAG
+ ARag config/breaker posture). Tests: `tests/bff/test_llmops.py` (5 tests). Full suite **498
passed**; ruff clean; FE build clean. **Note:** per-model cost isn't persisted (only per-stage),
and live RAG health needs the services running — both flagged in-UI; fallback/breaker counters
are on `/api/system/metrics`.

**BFF — new `ui/bff/app/llmops.py`**
- `GET /llmops/summary` — total spend, spend-over-time, per-model & per-service (LLM vs RAG)
  breakdown, budget-vs-actual, breaker status. *(G-OP1)*
- `GET /llmops/runs` — LLM scenario-gen run index (from `list_runs()`). *(G-OP2)*
- `GET /llmops/runs/{run_id}` — per-run: per-stage cost/tokens/latency rollup + phase
  artifacts (proposals/evidence/verify/discover). *(G-SC2/G-OP2)*
- `GET /llmops/health` — MacroRAG/ARag health, circuit-breaker state, fallback metrics
  (wraps the orphaned `scenarios/macro/health` + ARag equivalent). *(G-SC3/G-OP2)*

**FE — new "Operations" nav section (technical-lens anchored)**
- **LLM Cost Overview**: total spend + trend, per-model / per-service donuts, budget gauge +
  breaker status band, cost-per-scenario.
- **LLM Run Explorer**: run index → per-run → per-stage → per-call token/cost/latency
  (reuse the existing scenario Call-Trace viewer for the per-call level).
- **RAG Health**: MacroRAG/ARag health, breaker, fallback-reason breakdown.

### Phase 3 — Portfolio Construction / Rebalancing upgrade *(FE + BFF, small ENG)* — ✅ DONE (2026-07-14)

Reframe sizing from a report into an analysis tool. **Read-only:** show target trades and
allow book-variant *download*, but no apply/execute (deferred).

**Delivered:** BFF `sizing.rebalance()` (route `/sizing/rebalance/{profile}` — current→target
per-trade deltas, direction, % change, turnover, risk share) + `sizing.book_variant()` (route
`/sizing/{profile}/variant` — reads the generated `src/pricer/portfolio/portfolio_<profile>.json`
for preview + client-side download; no ENG change needed, the runner already writes these files).
FE: two new Sizing tabs — **Rebalance** blotter (profile selector, turnover/binding-constraint
KPIs, current→target trade list sorted by size, book-variant JSON download via Blob) and
**Frontier** (risk-return scatter across the 4 mandates, per-profile return/VaR tiles,
expected-return driver table, trade-covariance panel). Tests: `tests/bff/test_bff_sizing.py`
(5). Full suite **503 passed**; ruff + FE build clean. Applying a rebalance / book variant
stays a gated write (post-V1).

**ENG (small)**
- Expose the generated `portfolio_<profile>.json` book variant for download via the store
  (already written by `book_variants.py`; just needs a read path). *(G-SZ3)*

**BFF**
- `GET /sizing/rebalance/{profile}` — current→target per-trade deltas (Δ notional, direction,
  binding constraint, Euler risk contribution). *(G-SZ1)*
- `GET /sizing/{profile}/variant` — serve the book-variant JSON for download. *(G-SZ3)*

**FE**
- **Rebalancing Blotter** (new): current→target as an executable-style trade list with
  binding-constraint tags; book-variant download. *(G-SZ1/3)*
- **Risk-Return / Frontier** view across the four mandates; render `expected_returns`
  method+detail; visualize the trade covariance / factor panel. *(G-SZ2/3)*

### Phase 4 — Reverse Stress + Scenario-Gen Explorer *(ENG + BFF + FE)* — ✅ DONE (2026-07-14)

**Delivered:** ENG — `ReverseStressReport` + `write_reverse_stress_report()` in
`src/scenariogen/report.py` (atomic write, refactored the existing stress-report writer to
share `_atomic_write`); `run_reverse_stress()` now captures run provenance and persists a
`reverse_<run_id>.json` per run (was CLI-only, persisted nothing). BFF — `scenarios.reverse()`
+ `reverse_detail()` (routes `/scenarios/reverse`, `/scenarios/reverse/{name}`), ranked
most-fragile-first. FE — new **Reverse Stress** tab in the Scenarios workspace: fragility
ranking (severity λ to breach the loss limit, reachable/not) → breaking-shock drilldown
(driver legs flagged in the propagated factor vector). Ran the solver to generate real data
(4 scenarios all breach $50mm at λ 0.30–0.61×). The **Scenario-Gen run explorer** was already
delivered in Phase 2 (Operations → Runs & Traces reads the LLM manifests). Tests:
`tests/scenariogen/test_reverse.py` persistence test + `tests/bff/test_bff_reverse.py` (4).
Full suite **507 passed**; ruff + FE build clean. (Also hardened the BFF test harness: shared
builders moved to `tests/bff/bff_helpers.py` so `from bff_helpers import` is import-order-safe
across packages.)

**ENG**
- Persist `run_reverse_stress` output: write a `ReverseStressReport` artifact + store entry
  (mirror the StressReport persistence pattern). *(G-SC1)*

**BFF**
- `GET /scenarios/reverse` (+ `/{name}`) — reverse-stress results: target limit, severity λ,
  and the breaking shock vector. *(G-SC1)*
- *(Scenario-gen run explorer BFF was delivered in Phase 2 `/llmops/runs`.)*

**FE**
- **Reverse Stress** (new): overview (which limits, λ-to-breach ranking) → drill (breaking
  shock vector + shock-derivation lineage). *(G-SC1)*
- **Scenario-Gen Run Explorer** wired to `/llmops/runs`: proposals → evidence → verify →
  narrate phase drilldown. *(G-SC2)*

### Phase 5 — Platform hardening *(FE, no ENG)* — ✅ DONE (2026-07-14)

- **Shared `DataTable`** primitive (sort / filter / paginate / CSV export), retrofitted to
  the heavy tables (book trades, IPV, JTD, attribution, sizing comparison). *(G-PL1)*
- Make the **lens** actually tailor nav emphasis and terminology (business vs technical),
  not just the landing route. *(G-PL2)*
- *(Optional)* consolidated **Portfolio Analytics** cross-subsystem overview above Cockpit.

**Delivered:** `src/components/shared/table/` — a pure `tableModel.ts` (sort/filter/paginate/
CSV, blanks-last, direction-independent) + `DataTable.tsx` (dependency-free grid: sortable
headers, per-column filters, pagination, CSV export). Retrofitted the flagship **Book**
valuation grid and the **Rebalance** blotter onto it. Lens-aware **SideNav**: business-facing
areas (Cockpit, subsystems, Governance) stay primary while the technical tools (LLM Cost/Ops,
System/Ops, Lineage, Admin) collapse into a group that is collapsed in the Business lens and
expanded in the Technical lens. **FE test tooling introduced** (vitest): `tableModel.test.ts`
(16 tests). Gates: vitest 16 passed, `tsc -b` + `vite build` clean, Python suite unaffected
(507). *(The optional cross-subsystem overview above Cockpit was not built — the Cockpit already
serves that role; deferred as non-essential.)*

---

## Program complete

All five phases are on `main`.
Follow-ons since the program (both DONE + pushed, 2026-07-14):
- **DataTable rollout completed** — IPV/golden, JTD, per-trade attribution, sizing comparison
  all retrofitted onto `DataTable` (+ a `rowClassName` prop).
- **Dated backtest-exception timeline** — ENG now persists a per-test-day `timeline`
  (date, P&L, rolling VaR limit, exception flag) on both the sensitivity and full-reval
  backtests (`src/pricer/risk/backtest.py` + `historical_var.py` dates); the Risk Backtest
  view plots realized P&L vs the VaR limit with exceptions marked and a method toggle.

**Gated-writes / auth** workstream (BUILD_PLAN §11) — started 2026-07-14:
- **Write layer + first action DONE + pushed**: BFF gains a pluggable write gate
  (`security.py` — `QLP_WRITE_TOKEN` header check, else local-trust; captures the actor from
  `X-QLP-Actor`), an append-only **audit trail** (`audit.py` → `data/pricer/audit/audit.jsonl`,
  `GET /audit`), and the first gated write: **`POST /scenarios/signoff`** (approve/reject a
  scenario version → sign-off store + audit). FE: gated Approve/Reject on the Versions tab with
  a confirm dialog (operator + note), an audit-trail panel on the Sign-off tab, and Admin
  write-access settings (operator name + optional token, `GET /write/mode` indicator). Tests:
  `tests/bff/test_bff_writes.py` (6 — token gate, audit, sign-off write/latest-wins/validation).
  Full suite **515 passed**.
- **Edit limits DONE + pushed** (2026-07-14): second gated write — `POST /risk/limits`
  (`risk.update_limit` edits an existing ceiling in `configs/pricer/pricer_config.json`
  `risk.limits.<bucket>.<key>`, edit-only/validated/atomic; config reads made BOM-tolerant) +
  audit. FE: an **Edit ceiling** action on each Limits gauge with a confirm dialog (new value +
  live utilisation preview + note). Tests: 3 in `test_risk_limits.py`. Full suite **518 passed**.
- **Apply-rebalance / activate book variant DONE + pushed** (2026-07-14): third gated write —
  `POST /sizing/activate` (`sizing.activate_variant` atomically promotes
  `src/pricer/portfolio/portfolio_<profile>.json` → the active `portfolio.json`; verbatim copy,
  validated) + `GET /sizing/active` (current active-book identity) + audit. FE: an active-book
  badge + gated **Activate this book** action on the Rebalance blotter (confirm dialog warning
  it changes what every run prices). Tests: 3 function-level + 2 TestClient route tests
  (routing/Depends/audit + token gate). Full suite **523 passed**.
- **Run-triggering + reverse-stress solve + per-user RBAC DONE + pushed** (2026-07-14) —
  completes the gated-writes workstream:
  - **Job runner** (`jobs.py`): launches a *whitelisted* runner (pricer / risk / scenariogen /
    reverse-stress solve / sizing / governance) as a background subprocess, persists status +
    log tail under `data/pricer/jobs/`, live-polled. Routes `GET /jobs/catalog`, `GET /jobs`,
    `POST /jobs` (gated), `GET /jobs/{id}`. FE: a **Jobs** tab in System/Ops (trigger + live
    status + log tail). This covers both run-triggering and on-demand reverse-stress solve.
  - **Per-user RBAC** (`security.py` + `configs/ui/roles.json`): roles viewer < approver <
    operator < admin; `require_write(action)` enforces the actor's role ≥ the action's required
    role (403 otherwise). All four write actions switched onto it. `GET /roles`; Admin shows the
    operator's effective role + per-action allowed/blocked. Permissive default (operator);
    honest caveat surfaced that under local-trust the actor is self-declared.
  - Tests: `test_bff_jobs.py` (4, fake Popen) + RBAC tests in `test_bff_writes.py`. Full suite
    **529 passed**.

**The gated-writes workstream (BUILD_PLAN §11) is complete** — five audited write actions
(sign-off, edit-limits, activate-book, run-trigger, reverse-stress solve) behind a
token+actor+audit+RBAC gate.

**Live re-price / what-if DONE + pushed** (2026-07-14): the last BUILD_PLAN §11 item besides
auth. BFF `pricing.live_price` proxies `POST /price` to the pricer service and returns the
fresh value (route `GET /pricing/live/{id}`), degrading gracefully when the service is down.
FE: the `LiveStoredToggle` is now interactive on the Trade page (backward-compatible — locked
elsewhere); toggling **Live** shows a `LiveReprice` panel — live PV, Δ vs the stored batch
value, live measures, snapshot id, and a service-down prompt with the start command. (The
service reprices the stored trade against its in-memory snapshot; arbitrary input-bump what-if
would need the engine to accept market-data overrides — a future ENG, noted.) Tests:
`test_bff_live.py` (4 — graceful-down, mocked success, 404, upstream error). Full suite
**533 passed**.

**Plan status: complete.** The only remaining BUILD_PLAN §11 item is real per-user
authentication (so the self-declared actor becomes a trusted identity) — deliberately deferred
as infrastructure. A short list of minor depth gaps remains (per-model LLM cost persistence,
live RAG health probe, scenario-gen phase-artifact drilldown, per-alert PV-drift detail) — most
blocked on live services or LLM-gen data existing rather than on code.

---

## 3. Sizing & sequencing

| Phase | Theme | ENG | BFF | FE | Size |
|---|---|---|---|---|---|
| 1 | Overview/drilldown completeness | none | light (aggregations) | 3 overviews + 3 risk views | M |
| 2 | **LLM Cost & Ops console** | none | new `llmops.py` (4 routes) | Operations section (3 screens) | M–L |
| 3 | Rebalancing / construction | small (variant read) | 2 routes | blotter + frontier | M |
| 4 | Reverse stress + gen explorer | yes (persist) | 1–2 routes | 2 screens | M |
| 5 | Platform (DataTable, lens) | none | none | shared kit retrofit | M |

Executed sequentially, Phase 1 → 5. Each phase lands in one or more increments to `main`,
gated on ruff + mypy + pytest.

## 4. Out of scope (this program)

- Gated writes: apply-rebalance, approve sign-off, edit limits, run triggering, live
  re-price. These remain in BUILD_PLAN §11 "Phase 7" and require RBAC + audit.
- BFF authentication (ENG-5) — required before any non-local deploy, tracked separately.
- New pricing/risk **engine** capabilities — this program surfaces existing outputs only
  (except the small reverse-stress persistence in Phase 4).
