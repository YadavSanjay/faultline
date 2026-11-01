# Stress Scenario Generation — UI Deep Dive & Build Plan

> **Parent:** `ui/docs/UI_DESIGN.md` (§8 Scenarios). Siblings: `PRICING_UI.md`, `RISK_UI.md` (shared lineage component, stored-only V1, BFF discipline).
> **North star:** a desk user, a risk controller, and a model validator should each answer their question in ≤3 clicks — *What scenarios do we run? · What does this one test and how were its shocks derived? · What does it do to us?* — and the hardest-to-explain step (correlated propagation from a few drivers to ~38 factors) should be made **visual**, not taken on faith.
> **Grounded in:** the full S0–S5 pipeline under `src/scenariogen/` — `covariance.py`, `scenario_gen.py`, `factor_vector.py`, `catalog.py`, `stress.py`, `plausibility.py`, `versioning.py`, `signoff.py`, `reverse.py`, `dashboard.py`, `macro/`, `runner.py`.
> **Backend reality:** scenariogen is **CLI/library-only** — the FastAPI service exposes only the thin P2 `/risk/scenario`, *not* the rich generator. This subsystem has the **largest backend gap**, so its build plan (§11) leads with BFF endpoints.

---

## 1. UX philosophy — "three questions, one ladder" (scenario variant)

| Altitude | Question | Page | Who |
|---|---|---|---|
| **L1 Catalog** | *What scenarios do we run, and which are concerning?* | Scenario Catalog | desk, controller |
| **L2 Definition** | *What does this test, and how were its shocks derived?* | Scenario Detail (+ Shock Derivation) | controller, validator |
| **L3 Impact** | *What does it do to the book — P&L and capital?* | Scenario Results | desk, controller |

Three commitments keep the most abstract subsystem **intuitive**:

1. **Make correlation visible.** The conditional-covariance step ("you assert 2 drivers, the model fills in the rest of the factor panel") is scenariogen's black box. The **Shock Derivation** view (§5) renders it: narrative drivers → the correlation that propagates them → the full materialized vector, every factor tagged by *origin* (driver / covariance-derived / overlay / macro). This is the scenariogen analogue of pricing's lineage page.
2. **Narrate, then quantify.** Each scenario leads with its plain-English story ("Taiwan export-control escalation + foundry disruption") and *what it tests*, before any factor table or P&L number.
3. **Governance is first-class.** Plausibility status (pass/flag/fail), sign-off (approved/rejected/pending), and the macro freshness gate are surfaced as badges everywhere — a controller sees a scenario's standing before trusting its number.

---

## 2. Personas & jobs-to-be-done

- **Desk / PM (business end user):** "Which scenario hurts most, and by how much?" → L1 catalog ranked by loss, L3 results.
- **Risk controller (business internal):** "Is this scenario plausible and signed off? How much capital does it require? Did we breach a limit?" → L1 status, L2 governance, L3 capital + reverse stress.
- **Model owner / validator (technical):** "Show me how the shocks were derived — which Σ, which drivers, what the macro source cited." → L2 Shock Derivation + Technical lens.

---

## 3. Scenarios workspace — navigation map

```
STRESS SCENARIOS                                  [Business ⇄ Technical]
│
├─ BUSINESS
│   ├─ SC-B1  Scenario Catalog       (L1)  named scenarios × tiers, status, headline loss/capital
│   ├─ SC-B2  Scenario Detail        (L2)  ★ narrative · what it tests · Shock Derivation · metadata
│   ├─ SC-B3  Scenario Results       (L3)  total P&L · capital (ES-under-shock) · by-AC · per-trade
│   ├─ SC-B4  Reverse Stress              "what would break us" — severity λ to the loss limit
│   ├─ SC-B5  Sign-off                    approve/reject a scenario_version (audit log)
│   ├─ SC-B6  Plausibility                bound/envelope gate findings per scenario × tier
│   └─ SC-B7  Versioning                  immutable version history, covariance config
│
└─ TECHNICAL
    ├─ SC-T1  Pipeline Explorer           S0 Σ → S1 materialize → S2 tiers → S3 gate → S4 macro → stress/capital → reverse
    ├─ SC-T2  Generation Trace            one stress run: per scenario×tier outcome (run/held_gate/held_signoff)
    ├─ SC-T3  MacroRAG Health             client status, circuit breaker, fallback reasons
    └─ SC-T4  Store / Retention / GC      version store, results store, sign-off store, prune policy
```

Cross-links: catalog row → SC-B2; SC-B2 factor leaf → **Market Data ▸ Feed Detail** (the history behind Σ); SC-B3 per-trade → **Pricing ▸ Trade**; capital (ES-under-shock) → **Risk ▸ full-reval VaR**; macro citation → MacroRAG source; provenance chip → **System/Ops ▸ Run**.

---

## 4. Business pages — deep dive

### SC-B1 · Scenario Catalog (L1)

**Goal:** the standing set of scenarios and which are concerning — at a glance.

**Data (stored-only V1):** `ScenarioResultStore` latest run (`GET /scenarios?valuation_date=`) joined with the version manifest + sign-off store.

**Layout**
- **Scenario × tier grid:** rows = named scenarios (`taiwan_geopolitical`, `ai_bust`, `rate_capital`, `keyman_nvda`), columns = severity tiers (mild / adverse / severe). Each cell shows **total P&L** (loss colored) and a small **capital Δ**; cell badges for **plausibility** (pass ✓ / flag ⚑ / fail ✗) and **sign-off** (approved ● / pending ○).
- **Ranking strip:** worst-loss-first list (the dashboard's `ranking()` / `worst_scenarios`), so "what hurts most" is immediate.
- **Per-scenario meta:** short narrative, horizon (days), `scenario_version` (16-hex), `cov_id`, last run / generation time, `used_macro` flag.
- Click a cell → SC-B2 for that scenario × tier.

**Intuitive touches**
- Held-out scenarios (gate fail / not signed off) render greyed with the reason on hover — the catalog shows the *full* standing set, not just what ran.
- The tier columns visually escalate (mild→severe) so severity reads left-to-right.

---

### SC-B2 · Scenario Detail (L2) ★

**Goal:** answer *what does this test* and *how were its shocks derived* — the user's explicit requirement.

**Data:** the run's `materialized` block (the `MaterializedScenario.to_dict()` provenance) + the version spec + sign-off/plausibility.

**Layout (top → down):**
1. **Narrative & trigger** — the story and *what it tests* (`narrative`, `trigger`), horizon, method (`conditional_covariance`), severity tier (×scale). Governance badges: plausibility status, sign-off status (with approver + note), macro used?
2. **Shock Derivation** (the centerpiece — see §5): drivers → covariance propagation → overlays → floor → materialized vector, every factor origin-tagged.
3. **Materialized factor vector** — the full ~38-factor table in human units (IR per tenor in bp, equity per name in %, vol in pts, credit in bp, FX in %), each tagged *driver / derived / overlay / macro*.
4. **Metadata panel** — `scenario_version`, `cov_id` (+ window: full / ewma:λ / crisis, `n_obs`), `severity_scale`, **generation time** (run `generated_at`), `snapshot_id` + `factor_snapshot_id`. (Satisfies "metadata like generation time etc.")
5. **Macro provenance** (if `used_macro`) — `macro_summary`, `world_view_id` (corpus snapshot), per-leg sources: which drivers came from MacroRAG with their **citations** (chunk publisher · publication · release_date · rerank_score) and rationale, plus the freshness/citation **gate** result. If it fell back to static, show *why* (synthesis_failed / unavailable / gate_failed).

---

### SC-B3 · Scenario Results (L3)

**Goal:** what the scenario does to the book.

**Data:** the run's `ScenarioStressResult` (`GET /scenarios/{name}/results?severity=&valuation_date=`).

**Layout**
- **Headline:** **total P&L** (full revaluation) and **capital Δ** = ES-under-shock (`es_shocked − es_base` at 97.5%), each with its plain-English line ("this scenario would cost ~$12.5m and require ~$4.2m more capital").
- **By asset class:** `pnl_by_asset_class` bars (equity / rates / credit / fx / …).
- **Per-trade table:** each `ScenarioPnl` (trade_id, asset_class, base_pv, scenario_pv, pnl), sortable by impact; row → Pricing ▸ Trade.
- **Capital explainer:** ES base vs ES shocked side by side, with the FRTB-97.5% note and the "measured around the shocked state" framing.
- **Plausibility findings** inline if status is flag/fail (which factors exceeded bound/envelope).

**Intuitive touch:** the capital number is framed as *forward-looking required capital*, distinct from the *immediate P&L* — two different questions, side by side, each captioned.

---

### SC-B4 · Reverse Stress ("what would break us")

**Data:** `ReverseStressResult` (`POST /scenarios/{name}/reverse` with target loss; or stored).
**Layout:** per scenario shape, a loss-vs-severity (λ) curve with the **loss limit** line and the solved **λ\*** marked; verdict in plain English ("a Taiwan shock at ~1.7× — between adverse 1.5 and severe 2.0 — breaches the $50m loss limit" / "not reachable within 10×"). Shows `reachable`, `severity_scale`, `achieved_loss`, `iterations`.

### SC-B5 · Sign-off (internal)
`SignoffStore` history: per `scenario_version`, the approve/reject log (approver, recorded_at, note, seq; latest wins). Approve/reject is a **gated write** (RBAC + audit); read-only first, write behind a flag.

### SC-B6 · Plausibility (internal)
Per scenario × tier, the gate findings: **bound** (hard cap per factor class → fail) and **envelope** (n-sigma historical band → flag, or fail if the desk configured it). Shows `status`, `promotable`, and each finding (factor, check, observed vs limit). Explains *why* a scenario was held out of the run.

### SC-B7 · Versioning (internal)
`ScenarioStore` manifest: immutable version history (scenario_version, created_at, name, method, n_drivers, n_overlays, horizon_days). Identity triple **{scenario_version, severity, cov_id}** explained as "what reproduces the exact factor vector."

---

## 5. The Shock Derivation visualization (the centerpiece)

This is what makes "how the shocks were arrived at" intuitive. It renders the real S1 pipeline as a left→right flow:

```
SC-B2 ▸ Shock Derivation · taiwan_geopolitical · adverse (×1.5)   method: conditional_covariance
┌ ① DRIVERS (narrative-set) ┐   ┌ ② PROPAGATION (Σ) ┐   ┌ ③ OVERLAYS ┐   ┌ ④ MATERIALIZED VECTOR ┐
│ fx:TWD   +8.0%  ×1.5=+12% │──▶│ conditional mean   │──▶│ eq:TSM −10%│──▶│ 38 factors, origin-tag │
│ eq:TSM  −25.0%  ×1.5=−37% │   │ over Σ (ewma, n=250│   │ eqvol +5pt │   │ ir:DGS10 +18bp (derived)│
│ (driver origin)           │   │ cov_id 9f1c…)      │   │ (idiosyncr)│   │ credit  +42bp  (derived)│
└───────────────────────────┘   │ correlation heatmap│   └────────────┘   │ eq:NVDA −31%  (derived) │
                                 │ driver → others ▸  │                    │ floor clamp ✓ (≥ −95%)  │
                                 └────────────────────┘                    └─────────────────────────┘
plain-English: "You asserted TWD +8% and TSM −25%. Given history, that implies NVDA −31%, credit
+42bp, rates −18bp … (market-consistent co-moves). The −10% TSM and +5pt vol overlays are added on top."
```

- **① Drivers:** the narrative factors with their native + severity-scaled values; tagged as the only *asserted* inputs.
- **② Propagation:** the conditional-covariance step. A **correlation heatmap** (from `FactorCovariance.correlation()`) with the driver row highlighted shows *why* each other factor moves; window (full/ewma/crisis), `n_obs`, `cov_id` shown for provenance. Hover a derived factor → "moved because it correlates ρ=0.7 with TSM."
- **③ Overlays:** idiosyncratic add-ons covariance can't see, applied after propagation.
- **④ Materialized vector + floor:** the complete vector; equity/FX moves clamped at the limited-liability floor (−95%) are flagged (and plausibility marks them beyond-historical).
- **Macro variant:** when `used_macro`, a driver node shows a MacroRAG badge; clicking it reveals the cited chunks (publisher, release_date, rerank_score) and rationale, the `world_view_id`, and the gate verdict.

**Why intuitive:** the user sees that a scenario is *two or three honest assertions + historically-grounded correlation + named overlays* — not a mysterious 38-number vector. The heatmap turns "trust the covariance" into "see the covariance."

---

## 6. Scenario-adaptive presentation

| Concept | Source | Headline | Plain-English |
|---|---|---|---|
| **Named scenario** | `ScenarioDefinition` | narrative + drivers | "What event, asserted as which moves" |
| **Severity tier** | `severity_tiers` (×mult) | mild/adverse/severe | "Same shape, scaled — propagation scales coherently" |
| **Materialized vector** | `MaterializedScenario` | 38-factor table | "The full correlated shock applied to the book" |
| **P&L** | `ScenarioStressResult.total_pnl` | loss | "Immediate full-reval cost" |
| **Capital Δ** | `ScenarioCapital` | ES_shocked − ES_base | "Extra capital the scenario requires (FRTB 97.5% ES)" |
| **Plausibility** | `PlausibilityResult` | pass/flag/fail | "Is the scenario within sane / historical bounds?" |
| **Reverse stress** | `ReverseStressResult` | λ\* to limit | "How severe a move of this shape breaks the limit" |
| **Macro sourcing** | `DriverResolution` | static vs macrorag | "Where each driver came from, with citations" |

**Units (stated once):** IR/credit in bp, equity/FX in %, equity vol in pts, all from canonical decimal moves; loss shown positive; capital Δ positive = more capital required.

---

## 7. Technical lens — deep dive

- **SC-T1 Pipeline Explorer:** the S0→S5 chain as clickable stages — `covariance` (Σ builder, window policies) → `scenario_gen` (conditional mean) → `catalog`/tiers → `plausibility` gate → `macro` provider → `stress` (P&L + ES-under-shock) → `reverse` / `dashboard` / `signoff`. Each node: file, inputs/outputs, config.
- **SC-T2 Generation Trace:** one `run_stress` execution — per scenario: register version → sign-off gate → per tier: materialize (+macro) → plausibility gate → stress. Show the `scenarios_total{outcome}` tally (run / held_gate / held_signoff), durations, `cov_id`, `factor_snapshot_id`, limit breaches.
- **SC-T3 MacroRAG Health:** client `healthz`, circuit-breaker state, retry/backoff config, and `scenariogen_macro_fallback_total{reason}` (synthesis_failed / unavailable / gate_failed) — the resilience picture. Last synthesis calls with question + filters.
- **SC-T4 Store / Retention / GC:** version store, results store, sign-off store sizes; the prune-by-age / prune-by-count retention policy.

---

## 8. Interaction & visual principles (intuitive-first)

1. **Narrative before numbers** on every scenario.
2. **Make correlation visible** (heatmap + origin tags) — the one thing users can't otherwise trust.
3. **Governance badges everywhere:** plausibility, sign-off, macro gate.
4. **Two impact numbers, clearly separated:** immediate P&L vs forward-looking capital.
5. **Held-out is shown, not hidden:** gate/sign-off failures appear greyed with reasons.
6. **Provenance triple** ({scenario_version, severity, cov_id}) one click from any number.

---

## 9. Live vs stored, dates & snapshots

Consistent with Pricing/Risk: **stored-only in V1.** The workspace reads the persisted `StressReport` JSON (`data/pricer/scenario/<date>/scenario_<run_id>.json`) and the `ScenarioResultStore` Parquet. Reverse stress and sign-off writes are deferred/gated. Each scenario shows `snapshot_id` (pricing inputs) + `factor_snapshot_id` (Σ/history) + `cov_id`.

---

## 10. BFF contracts (Scenarios) — the biggest greenfield

Nothing here is on the live service today; the BFF reads the stores + run JSON.

| Need | Endpoint | Backed by |
|---|---|---|
| Catalog (SC-B1) | `GET /scenarios?valuation_date=` | `ScenarioResultStore.latest_run` + version manifest + sign-off |
| Detail + derivation (SC-B2) | `GET /scenarios/{name}?severity=&valuation_date=` | run `materialized` block (drivers/overlays/covariance/macro) |
| Correlation heatmap | `GET /scenarios/covariance?cov_id=` | `FactorCovariance.correlation()` |
| Results (SC-B3) | `GET /scenarios/{name}/results?severity=&valuation_date=` | `ScenarioStressResult` |
| Reverse stress (SC-B4) | `GET /scenarios/{name}/reverse?valuation_date=` (stored) | `ReverseStressResult` |
| Sign-off (SC-B5) | `GET /scenarios/signoff?scenario_version=` (+ gated `POST`) | `SignoffStore` |
| Plausibility (SC-B6) | `GET /scenarios/{name}/plausibility?severity=` | `PlausibilityResult` |
| Versions (SC-B7) | `GET /scenarios/versions` | `ScenarioStore.versions` |
| MacroRAG health (SC-T3) | `GET /scenarios/macro/health` | `MacroRAGClient` + fallback metric |

```jsonc
// GET /scenarios/{name}?severity=adverse → SC-B2 (drives Shock Derivation)
{ "name":"taiwan_geopolitical","severity":"adverse","scenario_version":"a1b2c3d4…",
  "narrative":"Export-control escalation + foundry disruption","trigger":"…",
  "horizon_days":10,"method":"conditional_covariance",
  "generated_at":"2026-06-20T…","snapshot_id":"56a863cf…","factor_snapshot_id":"e1f5a9c3…",
  "covariance":{ "window":"ewma:0.94","n_obs":250,"cov_id":"9f1c…" },
  "drivers":{ "fx:TWD":0.12, "eq:TSM":-0.375 },      // severity-scaled, canonical
  "overlays":{ "eq:TSM":-0.10, "eqvol":0.05 },
  "vector":[ { "factor":"eq:NVDA","move":-0.31,"origin":"derived","unit":"pct","display":-31.0 },
             { "factor":"ir:DGS10","move":0.0018,"origin":"derived","unit":"bp","display":18.0 },
             { "factor":"fx:TWD","move":0.12,"origin":"driver","unit":"pct","display":12.0 },
             { "factor":"credit","move":0.0042,"origin":"derived","unit":"bp","display":42.0 } ],
  "floor_clamped":[],
  "plausibility":{ "status":"flag","promotable":true,
                   "findings":[{"factor":"eq:TSM","check":"envelope","observed":-0.47,"limit":0.30,"severity":"flag"}] },
  "signoff":{ "status":"approved","approver":"S.Yadav","recorded_at":"…","note":"Q3 review" },
  "macro":{ "used_macro":true,"world_view_id":"wv_7c2","macro_summary":"…",
            "gate":{"passed":true,"findings":[]},
            "sources":{ "fx:TWD":{"origin":"macrorag","rationale":"…",
                                   "chunks":[{"publisher":"…","release_date":"2026-05-30","rerank_score":0.82}]} } } }
```

---

## 11. Build plan — tackling Scenarios first

Scenariogen is the **highest-effort** slice because nothing is on the live API. Sequencing accordingly:

**Pre-req (shared, do once):** the BFF skeleton (FastAPI) + the frontend shell, shared kit (`ProvenanceBadge`, `MetricGrid`, `LineageGraph`/heatmap, `Zone`, `LiveStoredToggle`). If Pricing/Risk slices haven't built these yet, this slice builds them — the **correlation heatmap** and **origin-tagged vector** are new shared components this slice introduces.

**Step 1 — BFF read layer (largest task).** Stand up `GET /scenarios`, `/scenarios/{name}`, `/scenarios/{name}/results`, `/scenarios/covariance`, `/scenarios/versions`, `/scenarios/{name}/plausibility`, `/scenarios/signoff` over the existing stores + run JSON. No engine changes needed — the run already persists everything (`StressReport`, `ScenarioResultStore`, `ScenarioStore`, `SignoffStore`). Only `correlation()` needs surfacing (it already exists on `FactorCovariance`).

**Step 2 — SC-B1 Catalog.** Scenario×tier grid + ranking + status badges. Immediate controller value on stored runs.

**Step 3 — SC-B2 Detail + Shock Derivation ★.** The centerpiece: narrative/trigger, the drivers→propagation→overlays→vector flow, the correlation heatmap, origin-tagged vector, metadata, macro provenance. This is where the subsystem earns its keep.

**Step 4 — SC-B3 Results.** P&L + capital (ES-under-shock) + per-AC + per-trade + plausibility findings.

**Step 5 — Governance reads:** SC-B5 sign-off (read), SC-B6 plausibility, SC-B7 versions. SC-B4 reverse stress (stored read).

**Step 6 — Technical lens:** SC-T1 pipeline explorer, SC-T2 generation trace, SC-T3 MacroRAG health, SC-T4 store/GC.

**Step 7 (post-V1, gated writes):** sign-off approve/reject, on-demand reverse-stress solve, run triggering.

**Sequencing note vs the global roadmap:** doing Scenarios first front-loads the hardest BFF work and introduces the heatmap/origin-tag components. If the goal is fastest *visible* value, Pricing-lineage-first is cheaper (more existing endpoints); if the goal is to de-risk the biggest unknown early, Scenarios-first is the right call. Either way, the shared kit built here is reused by all subsystems.

---

## 12. Open questions / decisions for Scenarios

1. **Sequencing confirm:** Scenarios genuinely first (de-risk the big BFF gap), or after the Pricing/Risk shared kit exists? (Recommend: build shared kit + BFF skeleton, then Scenarios.)
2. **Correlation heatmap scope:** full 38×38 with driver-row highlight, or a focused "driver → top-N correlated factors" view for legibility? (Recommend focused, with "show full matrix" toggle.)
3. **Reverse stress in V1:** stored results only, or allow on-demand solve (it's a heavy bisection × full-reval)? (Recommend stored-only in V1, consistent with the stored-only decision.)
4. **Macro detail depth:** show full chunk citations inline, or summary + "view sources" drawer? (Recommend drawer.)
5. **Sign-off writes:** read-only in V1 with gated write later — confirm.
