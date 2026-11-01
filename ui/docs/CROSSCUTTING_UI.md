# Cross-Cutting Areas — UI Deep Dive (System/Ops · Lineage · Governance)

> **Parent:** `ui/docs/UI_DESIGN.md` (§9). Siblings: `PRICING_UI.md`, `RISK_UI.md`, `SCENARIOGEN_UI.md`, `MARKETDATA_UI.md`.
> **Scope:** the three areas that sit *across* the four subsystems — **System/Ops Console** (the technical-user home: health, run history, end-to-end request tracing, module map, config), **Lineage Explorer** (unify pricing + risk + market-data + scenario lineage into one drill-down), **Governance & Controls** (the trust posture: IPV, golden, model inventory, scenario sign-off, drift).
> **Grounded in:** `src/common/obs/` (`log.py`, `tracing.py`, `metrics.py`, `run_manifest.py`), `src/pricer/obs/` (richer pricer manifest, `drift.py`), `src/common/provenance.py` + `snapshot.py`, `src/pricer/governance/` (`ipv.py`, `golden.py`, `model_inventory.py`), and the service's `/health` `/metrics` `/runs` `/portfolio/versions` `/governance/*`.

---

## 1. Why these three are one family — the provenance spine

The four subsystems *produce* numbers. These three areas answer the three questions you ask *about* any number, and they all pivot on the **same set of identifiers**:

| Area | Question | Verb |
|---|---|---|
| **System/Ops** | *Did the run that produced this happen cleanly, and how did the request flow through the system?* | **operate** |
| **Lineage** | *How was this number built — from final value down to source data?* | **explain** |
| **Governance** | *Should I trust this number — did it pass the controls?* | **assure** |

They are unified by the **correlation spine** — the content-addressed identifiers stamped on every artifact:

| Identifier | Source | Pins |
|---|---|---|
| `run_id` | `RunProvenance` (UUID per run) | one execution (pricing / risk / scenariogen / marketdata) |
| `snapshot_id` | `MarketDataSnapshot` / `FileSnapshot` | the exact market-data input bytes |
| `factor_snapshot_id` | `FactorHistory.snapshot()` | the VaR/covariance history inputs |
| `portfolio_version` | `PortfolioStore` | the exact trade economics |
| `cov_id` | `FactorCovariance` | the exact Σ |
| `git_sha` | `RunProvenance` | the code version |
| `config_hash` | `RunProvenance` | the effective config |

Every page in all three areas treats these as **clickable, cross-linking primitives** (the shared `ProvenanceBadge`). Click `run_id` anywhere → System/Ops run trace; click `snapshot_id` → Market Data snapshot; the same `run_id` ties a governance verdict to the run that earned it. **This spine is the reason the three areas feel like one tool.**

---

## 2. Personas

- **Platform / SRE (technical):** lives in System/Ops — "did everything run, is it healthy, trace this slow request."
- **Quant / model owner (technical):** lives in Lineage — "walk this number to its sources."
- **Risk controller / validator / auditor (business internal):** lives in Governance — "is the book's pricing under control; reproduce this for the regulator."

All three personas cross into the others via the spine: an SRE's failed run → the governance verdict it skipped; a validator's IPV break → the lineage of the offending trade.

---

# AREA S — System / Ops Console

> The technical user's home. Satisfies the master requirement: *"explore system parts/components and see tracing of an actual request flowing through the system end-to-end."*

## S.1 Philosophy

Two trace granularities, both reconstructed from the spine:
1. **Intra-run trace** — one request/run flowing through its *stages* (a `/price` call: ctx → snapshot → resolve → price → result; a batch run: bootstrap → snapshot → load → price → drift → govern → persist → manifest). Built from the run manifest + structured logs (`observe_duration` spans carry `duration_ms`).
2. **Cross-module run lineage** — the *pipeline DAG*: which market-data snapshot fed which pricing run, which fed which risk/scenario run. Linked by `snapshot_id` / `factor_snapshot_id`, not a single request id.

## S.2 Pages

```
SYSTEM / OPS CONSOLE
├─ S1  Health & Metrics       /health + /metrics rendered as status + gauges
├─ S2  Run History            all modules unioned: data/*/store/runs/ + /runs
├─ S3  Run Trace          ★   one run, stage-by-stage with timings (the end-to-end request flow)
├─ S4  Pipeline DAG           cross-module run lineage by snapshot id
├─ S5  Module / Architecture Map   interactive src/ package + data-flow diagram
├─ S6  Config Explorer        effective config per module, env overlay, PRICER_* settings (masked)
└─ S7  Provenance / Reproducibility   given a run, the "how to reproduce" recipe
```

### S1 · Health & Metrics
`/health` → status, `snapshot_id`, QuantLib version. `/metrics` (Prometheus exposition) rendered as gauges/counters grouped by module: `pricer_trades_total{status}`, `pricer_price_seconds`, `pricer_data_quality_total{severity}`, `pricer_book_pv`, `risk_factor_missing_total`, `risk_limit_breach_total`, `scenariogen_runs_total`, `scenariogen_macro_fallback_total{reason}`, `marketdata_dq_total{severity}`. A single "system green/amber/red" rollup at top.

### S2 · Run History (all modules)
The generic `RunManifest` is unioned across `data/*/store/runs/` (DuckDB `union_by_name`). A timeline/table of every run: `module` · `run_id` · `partition` (date) · `started/finished` · `duration_s` · `status` (ok/partial/failed) · `git_sha` · `config_hash` · the JSON `counts` (n_trades, n_ok/blocked/failed, n_held_gate, …). Filter by module/date/status. Row → S3.

```
System ▸ Run History                                            modules: all ▾   status: all ▾
┌ module        run_id     date        dur    status   counts                         ▸ │
│ marketdata    a91c…      06-19       42s    ok       sources 9 · dq warn 3           │
│ pricer        15054ab…   05-29       8.1s   ok       38 trades · 37 ok · 1 ⚠         │
│ risk          7c2f…      05-29       3m12   ok       VaR99 0.51m · backtest green    │
│ scenariogen   3d8a…      05-29       1m05   partial  11 run · 1 held_gate · 1 breach │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### S3 · Run Trace ★ (the end-to-end request flow)
Pick a `run_id` → reconstruct its stages as a `RunTrace` timeline. For a **pricing batch run**:
```
Run 15054ab… · pricer · 2026-05-29 · 8.1s · ok          snapshot 56a863cf · git 9f3a · cfg 1c4e
┌ stage ───────────────────────── dur ── detail ─────────────────────────────────────────┐
│ ● bootstrap (config, provenance)   40ms  config_hash 1c4e · git 9f3a                    │
│ ● market-data snapshot             120ms snapshot 56a863cf · 38 files fingerprinted     │
│ ● portfolio load                    15ms portfolio_version 8ad1 · 38 trades             │
│ ● price trades (curve-cache)       6.9s  37 ok · 1 ⚠ blocked · cache 4 boots / 22 hits  │
│     ▸ per-trade spans (pricer_price_seconds) — expand                                   │
│ ● pv-drift detection               220ms 0 alerts                                       │
│ ● governance (IPV · golden)        540ms IPV 38/38 · golden ✓                           │
│ ● persist (json · parquet · manifest) 90ms                                              │
└──────────────────────────────────────────────────────────────────────────────────────┘
[ expand a trade → its market-data resolve → pricer → result  (links into Pricing ▸ Lineage) ]
```
For a **single `/price` request**, the same view shows ctx → snapshot pin → `market_data_requirements()` → `loader.resolve()` (per requirement, with staleness) → `get_pricer()` → QL build (cache hit/miss) → result. Expanding a trade jumps into **Pricing ▸ Lineage** (the analytical mirror of this mechanical trace). The richer pricer manifest supplies counts; the generic manifest covers the other modules.

> **Backend dependency (be honest):** stage/span fidelity depends on **capturing the structured logs** (the `observe_duration` `duration_ms` spans and the `run_id`-bound records). Today they stream to stdout/file. For full span-level traces the BFF must read a **log sink** (parse the JSON log file, or — better — ship logs to a queryable store). Without it, the trace degrades gracefully to manifest-level stages (still useful: counts, durations, status). This is the one real backend addition for the technical lens.

### S4 · Pipeline DAG (cross-module run lineage)
A graph of runs linked by the spine: a marketdata ingest run produces `snapshot 56a863cf` → the pricing run that consumed it → the risk run that consumed the pricing + `factor_snapshot e1f5` → the scenariogen run on the same factors. Answers "which data version flowed into today's risk number" and "if I change this feed, what re-runs." Built by joining run manifests on `snapshot_id` / `factor_snapshot_id` / `extra_json`.

### S5 · Module / Architecture Map
Interactive `ModuleMap` of `src/` packages (`common` · `marketdata` · `pricer{input,loader,ql,risk,governance,obs,output}` · `scenariogen`) and the marketdata→pricing→risk→scenariogen data flow. Click a node → its responsibility, files, and a jump into that subsystem's Technical lens (PR-T1 / RK-T1 / SC-T1 / MD-T1). This is the "explore system parts/components" requirement, and doubles as living architecture docs.

### S6 · Config Explorer
Effective merged config per module (`pricer_config.json` + env overlay), the validated pydantic view, and `PRICER_*` runtime settings (secrets masked as `SecretStr`). Shows `config_hash` and which overlay (dev/uat/prod) is active. Read-only.

### S7 · Provenance / Reproducibility
Given any run: the full `RunProvenance` (run_id, git_sha, quantlib/python versions, config_hash, snapshot/factor/portfolio ids, priced_at) and a generated **"reproduce this" recipe** (checkout git_sha → set env → run command with the pinned snapshot/portfolio). The auditor's one-click answer.

## S.3 BFF (System/Ops)
| Need | Endpoint | Backed by |
|---|---|---|
| Health/metrics | proxy `/health`, `/metrics` | service |
| Run history (all) | `GET /system/runs?module=&date=&status=` | union `data/*/store/runs/` |
| Run trace | `GET /system/runs/{run_id}` | manifest + results store + log sink |
| Pipeline DAG | `GET /system/pipeline?date=` | manifests joined on snapshot ids |
| Module map | `GET /system/modules` | static metadata |
| Config | `GET /system/config?module=` | merged config + settings (masked) |
| Reproduce | `GET /system/runs/{run_id}/reproduce` | RunProvenance |

---

# AREA L — Lineage Explorer

> The unified, cross-subsystem drill-down: *one place to go from any high-level number to the atomic, provenance-stamped source.* It **composes** the per-subsystem lineage views (PR-B3 pricing, RK-B3 risk sensitivity, SC-B2 shock derivation, MD provenance) rather than reinventing them.

## L.1 Philosophy
The per-subsystem docs each render *their* lineage. The Lineage Explorer adds two things they can't do alone:
1. **Cross-subsystem assembly** — for one trade, stitch *pricing lineage + risk sensitivity lineage + the market-data provenance behind both* into a single graph, so "explain everything about this trade's numbers" is one screen.
2. **Two entry points** — **trade-centric** ("explain this trade") and **run/number-centric** ("explain this number from this run", or "what did snapshot X feed").

## L.2 Pages
```
LINEAGE EXPLORER
├─ L1  Trade Lineage        one trade: pricing + risk + market-data, unified graph
├─ L2  Number Lineage       any single number (a PV, a VaR factor, a scenario P&L) → its build
├─ L3  Provenance Graph     pivot by run_id / snapshot_id — what shares this input
└─ L4  Reproducibility      the recipe (shared with System/Ops S7)
```

### L1 · Trade Lineage (unified)
One `LineageGraph` with three swimlanes for a chosen trade + date:
- **Valuation** (from `/lineage/{trade_id}`): PV → legs → intermediates (cashflow ladder, curves) → market & static inputs.
- **Risk** (`?with=risk`): each sensitivity's bump-and-revalue (base/up/down) hanging off the same PV node.
- **Market data**: the resolved feeds (with provenance sha) shared by both lanes — clicking a feed node opens **Market Data ▸ Feed Detail**.
The lanes share input nodes, so the user sees that the SOFR curve feeding the PV is the *same* object the DV01 bumped. Provenance chip pins the whole graph to its run.

```
Lineage ▸ TRADE_22                              run 15054ab… · snapshot 56a863cf · port 8ad1
 VALUATION   [PV 125,000] ─ fixed/float ─ [cashflows] ─ [SOFR curve] ┐
 RISK        [IR_PARALLEL 49.5/bp] base/up/down ───────────────────── ┤→ [Pensford feed ▸ MD]
 MARKET DATA                              [USD-SOFR DISCOUNT · as-of 05-29 · sha 9f3a ✓] ┘
```

### L2 · Number Lineage
Generalizes the drill: pick *any* surfaced number anywhere in the app — a book PV, a `VaR99`, a factor contribution, a scenario `total_pnl` — and the Explorer routes to the right builder (pricing explain-pass / VaR histogram construction / shock derivation) and shows it. The universal "why is this number this?" affordance.

### L3 · Provenance Graph
Pivot on an identifier instead of a trade: pick a `snapshot_id` → every run and result that consumed it; pick a `run_id` → everything it produced; pick a `portfolio_version` → its trade set and all runs over it. The bridge between Lineage (analytical) and System/Ops (operational) — same spine, different verb.

### L4 · Reproducibility
Shared with S7 (one component, two entry points): the pinned-inputs recipe for the run behind whatever you're looking at.

## L.3 BFF (Lineage)
Mostly composition of existing endpoints: `/lineage/{trade_id}?with=risk` (pricing+risk), `/md/feeds/{id}` (feed provenance), `/risk/var-construction`, `/scenarios/{name}`. New: `GET /lineage/provenance?snapshot_id=|run_id=|portfolio_version=` for L3.

---

# AREA G — Governance & Controls

> The trust posture, consolidated. Pricing's three gates (IPV, golden, model inventory) + scenario sign-off + drift, in one control room with an overall pass/fail.

## G.1 Philosophy
Each control already exists and runs in the batch path; Governance gives them a **home and a rollup** so a controller answers "is the book under control today?" in one glance, then drills to any failure and onward to its lineage.

## G.2 Pages
```
GOVERNANCE & CONTROLS
├─ G1  Control Room      ★  overall posture: IPV · golden · inventory · sign-off · drift
├─ G2  Model Inventory      governed model per trade type (assumptions, limitations, approved use)
├─ G3  IPV                   independent price verification controls (identity/method/sanity)
├─ G4  Golden Gate           regression vs frozen baseline (breaks, snapshot match)
├─ G5  Scenario Sign-off      approve/reject scenario versions (audit log)
└─ G6  Drift                  PV-drift alerts run-over-run
```

### G1 · Control Room ★
A single posture board (from `POST /governance/verify` + risk limits + sign-off + drift):
```
Governance ▸ Control Room                       val 2026-05-29 · run 15054ab… · snapshot 56a863cf
┌ Golden ─────────┐ ┌ IPV ──────────────┐ ┌ Model Inventory ┐ ┌ Limits ──────┐ ┌ Sign-off ──┐
│ ✓ PASS          │ │ ✓ 38/38 controls  │ │ ✓ all covered   │ │ ✓ 0 breaches │ │ 3/4 ✓      │
│ snapshot match  │ │ 0 failed          │ │ 0 gaps          │ │              │ │ 1 pending  │
│ 0 breaks        │ │                   │ │                 │ │              │ │            │
└─────────────────┘ └───────────────────┘ └─────────────────┘ └──────────────┘ └────────────┘
┌ Drift ── 0 alerts ──┐   OVERALL: ● GREEN — book pricing is under control
└─────────────────────┘   [ any red tile → drill to the failing control → trade lineage ]
```

### G2 · Model Inventory
`ModelEntry` per trade type: model · engine · **assumptions** · **limitations** · approved use · IPV controls. Coverage check highlights any traded type with no governed entry (a gap). Framed as "the model we use here and what it can/can't do."

### G3 · IPV
`IpvControl` table: per trade, the independent check (`control`), its `kind` (identity / independent_method / sanity), `primary` vs `independent` values, `abs_diff` / `rel_diff` / `tolerance`, pass/fail. Failures sorted up; each links to the trade's lineage. The "second opinion" on every price, in plain terms (e.g. "stock PV must equal quantity × spot — exact identity").

### G4 · Golden Gate
`GoldenResult`: snapshot-match flag, `n_checked`, breaks (trade_id, expected vs actual vs tol), missing. Plain-English verdict: "Prices match the frozen baseline" / "3 prices drifted — investigate or re-baseline (QL upgrade? data change?)."

### G5 · Scenario Sign-off
The scenariogen `SignoffStore` view (shared with SC-B5): per `scenario_version`, the approve/reject log (approver, recorded_at, note, latest-wins). Read-only in V1; gated approve/reject later.

### G6 · Drift
`pricer/obs/drift.py` alerts: PV moved beyond abs+rel tolerance run-over-run. Timeline of alerts; each → PV history (PR-B4) + the two runs' snapshots (what changed in the data).

## G.3 BFF (Governance)
| Need | Endpoint | Backed by |
|---|---|---|
| Control room rollup | `GET /governance/posture?valuation_date=` | `/governance/verify` + limits + sign-off + drift |
| Model inventory | proxy `/governance/model-inventory` | service |
| Verify (golden+ipv+inv) | proxy `/governance/verify` | service |
| Sign-off | `GET /scenarios/signoff` (+ gated POST) | SignoffStore |
| Drift | `GET /governance/drift?valuation_date=` | drift alerts / metrics |

---

## 3. How the three areas interlink (the spine in action)

A single thread a controller might walk:
1. **System/Ops S2** — scenariogen run is `partial` (1 held_gate). Click the run.
2. **System/Ops S3** — the trace shows which scenario×tier was held and why (plausibility fail).
3. **Governance G1** — overall posture still GREEN for pricing, but a **golden break** on TRADE_15 appears. Click it.
4. **Governance G4 → Lineage L1** — the break's trade lineage shows its discount curve changed.
5. **Lineage L3 / Market Data** — the `snapshot_id` diff confirms the FRED feed updated → re-baseline decision.

Every hop is a click on a spine identifier. That continuity is the whole point of treating these three as one family.

---

## 4. Build plan (cross-cutting)

These are mostly **Phase 5–6** in the global roadmap (after the subsystems give them something to point at), but two pieces are **Phase 0 foundations** because everything reuses them:

**Phase 0 (foundations, build with the shell):**
- `ProvenanceBadge` + the spine identifier model (run_id/snapshot/portfolio/factor/cov/git/config) — used by *every* page in the app.
- `RunTrace`, `ModuleMap`, `Zone` shared components.
- `GET /system/runs` (union of manifests) — cheap, high-value, no new backend.

**Phase 5 (System/Ops + Lineage):**
1. S1 Health/Metrics + S2 Run History (existing data).
2. S3 Run Trace (manifest-level first; span-level once the **log sink** is wired — the one backend dependency).
3. S5 Module Map + S6 Config + S7 Reproducibility.
4. S4 Pipeline DAG.
5. Lineage L1 (composes the per-subsystem lineage already built) → L2 → L3.

**Phase 6 (Governance):**
6. G1 Control Room (rollup over `/governance/verify` + limits + sign-off + drift) → G2–G6 drill-downs.

**Sequencing note:** the Control Room (G1) and Run History (S2) are unusually cheap and high-value — both run on data that already exists — so a thin version of each can ship early as a "is everything OK today?" landing even before the subsystems are fully built.

---

## 5. Decisions (resolved) & open items

**Resolved:**
1. **Run Trace fidelity — DECIDED:** **manifest-level traces in V1**; per-stage spans are a **fast follow** via a log sink (parse the JSON log file from the `observe_duration` `duration_ms` / `run_id`-bound records). S3 ships on the run manifest (+ pricer richer manifest) — counts, durations, status, per-trade outcomes — and gains span detail once the log-parse path lands.
4. **Technical-lens landing — DECIDED:** a thin **Run History (S2) + Control Room (G1)** "is everything OK today?" board ships early (both run on data that already exists) and is the **default landing for the Technical lens**; the **Cockpit** remains the Business-lens landing.

**Open:**
2. **Pipeline DAG scope:** today's runs only, or a historical DAG browser? (Recommend: latest-per-date first.)
3. **Governance writes:** scenario sign-off and re-baselining are gated writes — read-only V1, confirm.
5. **Metrics source:** scrape `/metrics` live, or read the textfile the runner writes? (Recommend live `/metrics` when the service is up, textfile fallback.)
```
