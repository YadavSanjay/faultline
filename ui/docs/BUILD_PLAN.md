# UI Build & Sequencing Plan — Canonical Execution Plan

> **This is the single plan to work from.** It consolidates the seven design docs (`UI_DESIGN.md` + `PRICING_UI.md`, `RISK_UI.md`, `SCENARIOGEN_UI.md`, `MARKETDATA_UI.md`, `CROSSCUTTING_UI.md`) into one sequenced roadmap: what ships in what order, across the three workstreams, with dependencies and exit criteria.
> **Scope:** V1 is a **read-mostly explorer** over stored runs. Writes (sign-off, limit edits, live re-price, run triggering) are explicitly post-V1.

---

## 1. How to read this plan

Work splits across **three workstreams** that advance together each phase:

| Workstream | What | Where |
|---|---|---|
| **FE** — Frontend app | Vite + React + TS UI | `ui/app/` |
| **BFF** — Backend-for-Frontend | FastAPI read layer over stores/configs/provenance | `ui/bff/` |
| **ENG** — Engine/persistence changes | the few changes to `src/` the UI needs (mostly: persist things the run already computes) | `src/pricer/`, `src/scenariogen/` |

Each phase lists its FE slices, BFF endpoints, ENG changes, new shared components, a **demoable milestone**, and **exit criteria**. Phases are sequential for *focus*, but FE/BFF/ENG within a phase run in parallel.

---

## 2. Locked decisions (single source of truth)

- **Stack:** Vite + React + TS · React Router · TanStack Query + Table · ECharts · React Flow · Tailwind + shadcn/ui · typed clients from BFF OpenAPI. Node v24.14.0.
- **BFF:** FastAPI (reuses Python/DuckDB/pydantic/provenance). Proxies the existing service + adds read endpoints.
- **V1 = stored-only** across all subsystems (read Parquet/DuckDB stores, run JSON, configs, provenance sidecars). Live re-price deferred.
- **Pricing intermediates:** computed in an engine **"explain" pass** and **persisted to the results store during batch** (not on-request).
- **Lineage rollout:** **swap + cash equity first**, then options/bonds/CDS/FX.
- **Run Trace:** **manifest-level in V1**; per-stage spans a **fast follow** via a log sink.
- **Technical-lens landing:** thin **Run History (S2) + Control Room (G1)** board ships early as the default Technical landing; **Cockpit** is the Business landing.
- **No auth today** → pluggable token/header at the BFF; **required before any non-local deploy** (ties to production-hardening items 9–10).

---

## 3. Backend (ENG) changes — the whole list (small, important)

Almost everything is read-only BFF. These are the only `src/` changes, all "persist/expose what's already computed":

| # | Change | For | Phase | Size |
|---|---|---|---|---|
| ENG-1 | Pricing engine **explain pass**: capture zero/DF/forward at used tenors + the cashflow table; **persist to results store** during batch | Pricing lineage (PR-B3), Lineage L1 | 1 | M |
| ENG-2 | Risk run: **persist `VarEngine.pnl_series`** (the historical P&L vector) in the risk-run JSON | VaR histogram (RK-B3b) | 2 | S |
| ENG-3 | Expose `FactorCovariance.correlation()` via BFF (already exists on the dataclass — no engine change, BFF-only) | Shock-derivation heatmap (SC-B2) | 4 | XS |
| ENG-4 | **Log sink**: parse the JSON log file (`observe_duration` `duration_ms` + `run_id`-bound records) into a queryable form | Span-level Run Trace (S3) | 5 (fast follow) | M |
| ENG-5 | BFF auth (token/header) | any non-local deploy | pre-deploy | M |

Everything else below is BFF read + FE.

---

## 4. Phase 0 — Foundations (shell, spine, BFF skeleton, early board)

**Goal:** the app exists, the shared kit and provenance spine are in place, and there's a useful landing on day one.

- **FE:** app shell (top bar: portfolio · valuation date · snapshot · **Business⇄Technical** toggle), left nav (the area map), routing, theming (Tailwind + shadcn). Mock-data harness behind the typed API client.
- **Shared kit (introduced):** `LadderShell`, `ProvenanceBadge` (+ the **spine identifier model**: run_id/snapshot_id/factor_snapshot_id/portfolio_version/cov_id/git_sha/config_hash), `MetricCell`/`MetricGrid`, `Zone`, `DqBadge`, `WarningBanner`, `LiveStoredToggle` (locked to "Stored"), `RunTrace` (manifest-level), `ModuleMap` (stub), `SeriesChart`, `CurveChart`.
- **BFF skeleton:** FastAPI app, OpenAPI emit, proxy `GET /health` + `/metrics`, and **`GET /system/runs`** (union of `data/*/store/runs/` via DuckDB).
- **Early board (cheap win):** thin **System ▸ Run History (S2)** + **Governance ▸ Control Room (G1)** "is everything OK today?" — the **default Technical-lens landing**. G1 runs off `GET /governance/posture` (rollup of existing `/governance/verify` + limits + sign-off + drift).
- **Demoable:** open the app → Technical landing shows every module's latest runs (status/counts/duration) + a green/amber/red control posture. Business landing shows a stub Cockpit.
- **Exit:** nav + lens toggle work; spine ids render and cross-link; Run History + Control Room read real data.

---

## 5. Phase 1 — Pricing (business) + Calculation Lineage ★

**Goal:** the centerpiece — value the book, drill to a trade, and *explain* a price down to its sources. Highest user value, mostly on existing endpoints.

- **FE slices:** PR-B2 Valuation Card (instrument-adaptive) → PR-B1 Book Valuation + movers → **PR-B3 Calculation Lineage** (swap + cash equity first) → PR-B4 PV History → PR-B5 Model Inventory + PR-B6 Governance Verify (read).
- **New components:** `ValuationCard`, `LegBreakdown`, `CashflowLadder`, `LineageGraph` (+ `LineageInspector`), `ReconcileCheck`.
- **BFF:** proxy `/price`, `/price-portfolio`, `/results/{pv-history,book,run}`; **new `GET /lineage/{trade_id}`** (serves the persisted explain-pass output); proxy `/governance/model-inventory`, `/governance/verify`.
- **ENG:** **ENG-1** (explain pass + persist intermediates).
- **Demoable:** book PV → click a swap → "Explain" → graph from PV → cashflow ladder + SOFR curve → market-data feed (with sha) and the trade ticket, every sum reconciling with ✓.
- **Exit:** PR-B1/B2/B3 work for swap + cash equity end-to-end on stored data; lineage leaves link into (stub) Market Data; provenance pins every number.

---

## 6. Phase 2 — Risk (business)

**Goal:** headline risk/capital, drill to source, and explain the methodology — reusing Pricing's lineage component.

- **FE slices:** RK-B1 Risk Dashboard (VaR matrix, book sensitivities, backtest zone, limits) → RK-B2 Drill (by asset class / factor / trade) → **RK-B3 Methodology** (a: sensitivity lineage via `LineageGraph` `?with=risk`; b: `VarHistogram`) → RK-B4 P&L Explain → RK-B5 Backtest → RK-B6 Limits (read).
- **New components:** `VarMatrix`, `SensitivityTable`, `ContributionBar`, `VarHistogram`, `PnlExplainWaterfall`, `BacktestTimeline`, `LimitTable`, `MissingFactorBanner`.
- **BFF:** `GET /risk/report`, `GET /risk/var-construction`, `/risk/pnl-explain`, `/risk/backtest`, `/risk/limits`; extend `/lineage/{id}?with=risk`.
- **ENG:** **ENG-2** (persist `pnl_series`).
- **Demoable:** dashboard → click `IR_KR:5Y` → every trade's exposure → one trade's bump-and-revalue in the lineage graph; VaR shown as a histogram with VaR/ES cut-lines; missing-factor banner if any.
- **Exit:** RK-B1/B2/B3 work on the stored RiskReport; sensitivity lineage shares the Pricing component; trust signals (missing factors, zone, breaches) surfaced.

---

## 7. Phase 3 — Market Data (business)

**Goal:** the clean source→feed→data→trade ladder. All-greenfield BFF, no engine changes; pairs naturally with Pricing lineage (its input nodes link here).

- **FE slices:** MD-B1 Feed Catalog → MD-B2 Feed Detail → MD-B3 Data Viewer (reuses `CurveChart`/`SeriesChart`) → MD-B4 Feed↔Trade Coverage → MD-B5 DQ Dashboard.
- **New components:** `FeedCatalogGrid`, `FeedDetail`, `CoverageMatrix`, `DqDashboard`.
- **BFF:** `GET /md/feeds`, `/md/feeds/{id}`, `/md/feeds/{id}/series`, `/md/dq`, `/md/coverage`, `/md/snapshots[/{id}|/diff]`.
- **ENG:** none.
- **Demoable:** catalog grouped by asset class with freshness/DQ → feed detail with provenance + consuming trades → click a consumer → Pricing lineage; coverage matrix flags a trade with a stale primary feed.
- **Exit:** Pricing lineage's market-data leaves now resolve to real MD-B2 pages; coverage is bidirectional.

---

## 8. Phase 4 — Stress Scenarios (business)

**Goal:** the heaviest BFF slice — catalog → detail with the **Shock Derivation** centerpiece → impact. De-risks the biggest unknown.

- **FE slices:** SC-B1 Catalog (scenario×tier grid) → **SC-B2 Detail + Shock Derivation** (drivers→propagation→overlays→vector + correlation heatmap + origin-tagged vector + macro provenance) → SC-B3 Results (P&L + ES-under-shock capital + per-AC/per-trade) → SC-B6 Plausibility + SC-B7 Versioning + SC-B5 Sign-off (read) + SC-B4 Reverse Stress (stored).
- **New components:** `ScenarioGrid`, `ShockDerivationFlow`, `CorrelationHeatmap`, `FactorVectorTable` (origin-tagged), `MacroProvenanceDrawer`, `CapitalExplainer`, `ReverseStressCurve`.
- **BFF:** `GET /scenarios`, `/scenarios/{name}`, `/scenarios/covariance`, `/scenarios/{name}/results`, `/scenarios/{name}/plausibility`, `/scenarios/versions`, `/scenarios/signoff`, `/scenarios/{name}/reverse`, `/scenarios/macro/health`.
- **ENG:** **ENG-3** (surface `correlation()` — BFF-only).
- **Demoable:** catalog ranked by loss → open Taiwan/adverse → see 2 drivers propagate to 38 factors via the heatmap, each factor origin-tagged, macro citations in a drawer → results show P&L + capital Δ.
- **Exit:** the "how were the shocks derived" requirement is visually answered; all business pages on stored runs.

---

## 9. Phase 5 — Technical lenses + System/Ops + Lineage Explorer

**Goal:** the technical-user depth — component explorers per subsystem, full System/Ops, and the unified Lineage Explorer.

- **FE slices:** per-subsystem Technical lenses (PR-T1–3, RK-T1–3, SC-T1–4, MD-T1–3); System/Ops S3 Run Trace (manifest-level) → S4 Pipeline DAG → S5 Module Map → S6 Config → S7 Reproducibility; Lineage Explorer L1 (unified trade lineage) → L2 (any number) → L3 (provenance graph).
- **New components:** `PipelineDag`, full `ModuleMap`, `ConfigExplorer`, `ReproRecipe`, `ProvenanceGraph`, per-subsystem `ComponentExplorer`.
- **BFF:** `GET /system/runs/{id}`, `/system/pipeline`, `/system/modules`, `/system/config`, `/system/runs/{id}/reproduce`, `/lineage/provenance`.
- **ENG:** **ENG-4** (log sink — enables span-level S3; manifest-level ships first regardless).
- **Demoable:** pick a run → stage-by-stage trace → expand a trade into its lineage; pipeline DAG shows which snapshot fed which run.
- **Exit:** "explore components + trace a request end-to-end" requirement satisfied; Lineage Explorer composes all subsystem lineages.

---

## 10. Phase 6 — Governance consolidation + Admin + polish

**Goal:** the full trust control room and the finishing layer.

- **FE slices:** G1 Control Room (full) → G2 Model Inventory → G3 IPV → G4 Golden → G5 Sign-off (read) → G6 Drift; Admin (env switch, prefs, user/role context); empty/error/loading states, a11y, perf passes; the full **Cockpit** business landing.
- **BFF:** `GET /governance/posture`, `/governance/drift`; proxies already in place.
- **Demoable:** Control Room green/amber/red → drill a golden break → trade lineage → snapshot diff.
- **Exit:** V1 complete — read-mostly explorer across all four subsystems + three cross-cutting areas.

---

## 11. Phase 7 — Post-V1 (gated writes & live)

Deferred by the stored-only decision: gated **scenario sign-off** approve/reject, **limit editing**, **on-demand reverse-stress solve**, **live re-price** (`/price` what-if), **run triggering**, span-level traces if ENG-4 slipped, and **BFF auth (ENG-5)** before any non-local deploy. Each needs RBAC + audit.

---

## 12. Dependency graph

```
Phase 0 Foundations ──┬─────────────────────────────────────────────► (everything)
  (shell, spine,      │
   shared kit,        ├─► Phase 1 Pricing + Lineage ──┐
   BFF skeleton,      │      (ENG-1 explain pass)      ├─► Phase 3 Market Data
   S2+G1 board)       │                                │     (lineage leaves resolve here)
                      │      Phase 1 ──► Phase 2 Risk ──┘     (ENG-2 pnl_series)
                      │                  (reuses LineageGraph)
                      ├─► Phase 4 Scenarios (heaviest BFF; reuses heatmap/spine)
                      │
                      └─► Phase 5 Technical+Ops+Lineage ──► Phase 6 Governance+polish ──► Phase 7 writes
```
Hard edges: **0 → all**; **1 → 2** (Risk reuses the lineage component) and **1 → 3** (MD pages are where Pricing's lineage leaves land); **L1 (Phase 5) needs 1+2+3+4** (it composes their lineages). Scenarios (4) depends only on Phase 0 — it can move earlier (see §14).

---

## 13. Master endpoint & component ledgers

**BFF endpoints by phase:** P0 `/health`,`/metrics`,`/system/runs`,`/governance/posture` · P1 `/price*`,`/results/*`,`/lineage/{id}`,`/governance/{model-inventory,verify}` · P2 `/risk/{report,var-construction,pnl-explain,backtest,limits}` · P3 `/md/*` · P4 `/scenarios/*` · P5 `/system/{runs/{id},pipeline,modules,config,reproduce}`,`/lineage/provenance` · P6 `/governance/{posture,drift}`.

**Shared (reused everywhere), introduced P0:** `LadderShell`, `ProvenanceBadge`+spine, `MetricGrid`, `Zone`, `DqBadge`, `WarningBanner`, `RunTrace`, `ModuleMap`, `SeriesChart`, `CurveChart`, `LiveStoredToggle`, `LineageGraph` foundation. **Phase-specific** components are listed per phase above.

---

## 14. Sequencing rationale & the one alternative

**Recommended order (above)** front-loads the highest business value (Pricing lineage) on the cheapest backend, gets the reusable lineage component in early (Risk reuses it), then Market Data (no ENG), then the heavy Scenarios slice, then technical/governance depth.

**Alternative — Scenarios earlier (after Phase 0):** if the priority is to *de-risk the biggest BFF unknown first*, Scenarios can slot in right after Foundations (it depends only on Phase 0 and introduces the heatmap/origin-vector kit). Cost: it delays visible business value and doesn't reuse Pricing's lineage component (different visual). **Recommendation: keep Pricing first** unless de-risking Scenarios is the explicit priority — Phase 0 already de-risks the BFF shape, and Pricing-first yields a demoable centerpiece soonest.

---

## 15. V1 definition of done

A read-mostly explorer where, for each of the four subsystems, a user moves high→low (aggregate → entity → inputs/method → atomic source) in both Business and Technical lenses; Pricing and Risk expose full calculation lineage; the three cross-cutting areas (System/Ops, Lineage, Governance) tie every number to its run/snapshot via the provenance spine. All on **stored** data, **unauthenticated/local**. Writes, live re-price, and deploy-grade auth are Phase 7.

---

## 16. Still-open decisions (carried from the deep dives)

- Numbers-formatting standard (Pricing §12.4).
- Dashboard scenario count: top-N vs all (Risk §12.2); full-reval-VaR graceful absence (Risk §12.3).
- Heatmap scope: focused vs full-matrix toggle (Scenarios §12.2); macro detail depth (Scenarios §12.4).
- Feed identity key + series down-sampling (Market Data §8.1–8.2); coverage source of truth (§8.4).
- Pipeline DAG scope (Cross-cutting §5.2); metrics source live-vs-textfile (§5.5).
- Governance gated-writes confirm (Cross-cutting §5.3).

These are page-level details, not blockers — each has a recommended default in its doc and can be resolved as its phase begins.
