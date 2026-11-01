# Risk — UI Deep Dive

> **Parent:** `ui/docs/UI_DESIGN.md` (§7 Risk). Sibling: `ui/docs/PRICING_UI.md` (the lineage component and stored-only/engine-explain decisions are shared).
> **North star:** a desk user, a risk controller, and a model validator should each answer their question in ≤3 clicks — *How much can we lose? · Where is it coming from? · How is it computed, and can we trust it?* — and every risk number should be traceable down to a sensitivity bump and a market-data history series.
> **Grounded in:** `sensitivities.py`, `var.py`, `historical_var.py`, `pnl_explain.py`, `backtest.py`, `limits.py`, `report.py`, `scenarios.py` under `src/pricer/risk/`, and `/risk/*` in `src/pricer/service.py`.

---

## 1. UX philosophy — "three questions, one ladder" (risk variant)

The Risk workspace mirrors Pricing's ladder so users carry one mental model across the app:

| Altitude | Question | Page | Who |
|---|---|---|---|
| **L1 Book** | *How much can we lose? Are we within mandate?* | Risk Dashboard | desk, PM, controller |
| **L2 Source** | *Where is the risk coming from?* (asset class / factor / trade) | Risk Drill | controller, desk |
| **L3 Method** | *How is the number computed, and can I trust it?* | Methodology & Inputs | model owner, validator |

Three commitments make risk **intuitive** despite being the most abstract subsystem:

1. **Every risk number has a plain-English translation.** "VaR 99% = $0.51m" reads as *"On a bad day (1-in-100), we'd expect to lose about $0.51m or more."* "CS01 = −$236k" reads as *"If credit spreads widen 1bp across the book, we lose $236k."*
2. **Risk reuses the Pricing lineage.** A sensitivity is literally *base PV vs +bump PV vs −bump PV* from the same pricer — so RK-B3 renders the bump-and-revalue with the **same `LineageGraph` component** as PR-B3 (via `/lineage?with=risk`). VaR gets its own visual: sensitivity × historical factor moves → a P&L distribution with the VaR/ES cut lines drawn on it.
3. **Honesty is a feature.** `missing_factors` (exposure with no history → understated VaR), P&L-explain **coverage %**, and the backtest **traffic-light zone** are surfaced prominently — the UI tells you when *not* to trust the number.

---

## 2. Personas & jobs-to-be-done (risk-specific)

- **Desk / PM (business end user):** "What's my VaR and biggest exposures today? Did anything breach?" → L1, glances at limits + movers.
- **Risk controller (business internal):** "Are we within limits? Is the VaR model still valid (backtest)? Which factors drive the tail?" → L1 + RK-B5 backtest + RK-B6 limits + RK-B2 by-factor.
- **Model owner / validator (technical):** "Show me the bump-and-revalue and the historical-sim construction. What's the residual in P&L-explain?" → RK-B3 + RK-B4 + Technical lens.

---

## 3. Risk workspace — navigation map

```
RISK                                             [Business ⇄ Technical]
│
├─ BUSINESS
│   ├─ RK-B1  Risk Dashboard         (L1)  VaR/ES · book sensitivities · limits · backtest zone · scenarios
│   ├─ RK-B2  Risk Drill             (L2)  pivot: by asset class · by factor · by trade
│   ├─ RK-B3  Methodology & Inputs   (L3)  ★ sensitivity lineage + VaR construction + factor inputs
│   ├─ RK-B4  P&L Explain                 actual vs explained vs residual, coverage %
│   ├─ RK-B5  Backtest                    exceptions, traffic-light zone, Kupiec
│   └─ RK-B6  Limits                      configured ceilings, breaches, near-misses
│
└─ TECHNICAL
    ├─ RK-T1  Risk Engine Explorer        sensitivities · var · historical_var · scenarios · pnl_explain · backtest · limits
    ├─ RK-T2  Risk Run Trace              one risk run, staged with timings + factor-history load
    └─ RK-T3  Factor History / Workers    factor alignment matrix, snapshot id, full-reval fan-out
```

Cross-links: every factor/trade in RK-B1/B2 → RK-B3; RK-B3 factor leaf → **Market Data ▸ Feed Detail**; RK-B3 sensitivity → **Pricing ▸ Lineage** (same trade); scenario row → **Scenarios ▸ Detail**; provenance chip → **System/Ops ▸ Run**.

---

## 4. Business pages — deep dive

### RK-B1 · Risk Dashboard (L1)

**Goal:** the headline risk/capital picture + a one-glance trust signal.

**Data (stored-only, V1):** the persisted `RiskReport` JSON (`data/pricer/risk/<date>/risk_<run_id>.json`) via new BFF `GET /risk/report?valuation_date=`. (The live `/risk/var` & `/risk/sensitivities` endpoints exist but are deferred behind the same Stored/Live toggle as Pricing — see §8.)

**Layout**
- **VaR / ES card:** matrix of confidence level (0.95 / 0.975 / 0.99) × {VaR, ES}, losses shown as positive numbers. Subtitle states method (sensitivity-based historical simulation), `lookback_days`, `horizon_days`, `n_scenarios`. **ES at 97.5% is badged as the FRTB measure.** A **"missing factors"** flag appears here when `missing_factors[]` is non-empty (red: "VaR understated — N factors have exposure but no history").
- **Book sensitivities card:** headline factors (IR_PARALLEL, CS01, VEGA_RATES, EQUITY_DELTA, FX_DELTA) as `MetricCell`s with captions; expandable to per-exposure keys (EQUITY_DELTA:NVDA, FX_DELTA:TWD…). Sorted by absolute contribution.
- **Controls card:** **backtest zone** traffic-light (green/amber/red, `n_exceptions`/`n_days`, expected, Kupiec LR) + **limits** status (breach count; green buffer / amber near-miss / red breach).
- **Scenarios strip:** worst named scenarios by total P&L (from the report's `scenarios[]`), link to Scenarios subsystem.
- **Provenance chip:** `run_id` · `snapshot_id` · `factor_snapshot_id` (note the two snapshots: pricing inputs vs factor history).

**Intuitive touches**
- Each headline number has its plain-English line directly beneath it.
- The trust signals (missing factors, backtest zone, limit breaches) are visually loud — a controller sees "can I trust today's VaR?" without drilling.
- Loss sign convention stated once, in-context ("VaR/ES shown as positive losses").

---

### RK-B2 · Risk Drill (L2)

**Goal:** "where is the risk coming from?" Three pivots on the same book, one tab bar:

- **By asset class:** sensitivities and scenario P&L aggregated per class (rates/bonds/equity/options/credit/fx); bar + table. Click a class → filtered trade list.
- **By factor:** pick a factor (e.g. `IR_KR:5Y`, `EQUITY_DELTA:AAPL`, `CS01`) → every trade's exposure to it, the factor's `factor_stdev` (daily vol from history), and its share of book VaR. This is the "what drives the tail" view.
- **By trade:** one trade's full sensitivity vector (all factors it generated) and its contribution to book VaR; CTA to RK-B3 (methodology for that trade) and to **Pricing ▸ Lineage**.

**Intuitive touch:** a small "contribution to VaR" bar next to each factor/trade so the user sees concentration at a glance (the few factors that dominate).

---

### RK-B3 · Methodology & Inputs (L3) ★

**Goal:** make each risk number's computation self-evident. Two reusable "explanations":

#### (a) Sensitivity lineage — *reuses the Pricing `LineageGraph`*
A sensitivity is `(PV(+bump) − PV(−bump)) / 2` from the same pricer. Render it as a compact graph (served by `/lineage/{trade_id}?with=risk`):
```
EQUITY_DELTA:NVDA  (TRADE_1)            method: central bump-and-revalue · +1% underlier
┌───────────────────────────────────────────────────────────────────────┐
│  base PV   $50,000,000     spot $208.33                                 │
│     ├─ +1% spot →  PV(up)   $50,500,000                                 │
│     └─ −1% spot →  PV(dn)   $49,500,000                                 │
│  EQUITY_DELTA = (up − dn)/2 = $500,000  per +1% move   ✓                │
│  source: equity spot NVDA · prices_daily.csv · as-of 2026-05-29         │
└───────────────────────────────────────────────────────────────────────┘
```
The bump unit, the central-difference arithmetic, and the **market-data series the bump was applied to** are all shown — and the source node links to Market Data. Which sensitivities a trade has is *inferred from the data it uses* (swap → IR + key-rates; bond → +CS01; option → +vega/delta/gamma; FX → +spot/rho), so the page only shows applicable factors.

#### (b) VaR construction — the histogram story
Sensitivity-based historical-sim VaR is: **book sensitivity × each historical day's factor move → daily P&L vector → loss quantile**. Render it as:
```
VaR construction · sensitivity-based historical simulation
┌ inputs ──────────────┐  ┌ historical P&L distribution (lookback 250d) ───────────┐
│ book sens × factor   │  │            ▁▂▅█▇▅▂▁                                     │
│ moves, date-aligned  │  │     loss ◄──┼─────────┼──► gain                        │
│ factors: 38          │  │           ES99│ VaR99                                  │
│ horizon 1d (√t)      │  │   VaR99 0.512m · ES99 0.624m                            │
└──────────────────────┘  └────────────────────────────────────────────────────────┘
factor table: name · maps_to · factor_stdev · contribution · source series  [→ feed]
missing factors: none        method: quantile of −P&L, ES = mean tail beyond VaR
```
- The **factor table** lists each configured factor: `maps_to` sensitivity, `factor_stdev`, contribution, and its **source history series** (file/column) linking to Market Data. This is where VaR's inputs become legible.
- **Full-reval VaR** (when present in the report): same histogram framing but labeled "full revaluation — captures gamma/vega convexity exactly," plus `worst_day` / `worst_pnl` callout. ES-under-shock (`method` ends `@shocked`) is explained as the forward-looking capital measure.

**Intuitive touch:** the histogram makes "VaR is a quantile of a loss distribution" obvious without statistics jargon; the cut lines are labeled in plain English.

---

### RK-B4 · P&L Explain

**Goal:** validate that the Greeks actually capture the trade's risk.

**Data:** report's `pnl_explain` (scenario-driven: `actual` = scenario full-reval; `explained` = base Greeks × scenario shocks across ir/credit/rates_vol/equity_vol/equity_delta/equity_gamma/fx).

**Layout:** a waterfall per scenario — **actual P&L** vs the stacked **explained-by-factor** bars vs the **residual**, with **coverage %** as the headline ("first-order Greeks explain 94% of the move"). Per-trade table with each trade's actual / explained_total / residual. Large residual is flagged (missing factor or higher-order/cross-gamma).

**Intuitive touch:** coverage shown as a single progress bar; a high residual row is colored to say "the simple risk view misses something here."

---

### RK-B5 · Backtest

**Goal:** is the VaR model still valid? (model-validation control)

**Data:** report's `backtest` (`BacktestResult`).

**Layout:** a **traffic-light** hero (green/amber/red) with the plain-English verdict ("Green — the VaR model is forecasting risk adequately"); an exceptions timeline (realized loss vs rolling VaR, exception days marked); the stats row (`n_exceptions` vs `expected_exceptions`, `exception_rate`, Kupiec LR, level, window). Amber/red carry a "what this means / next step" note (model review).

---

### RK-B6 · Limits (internal maintenance)

**Goal:** the mandate control room.

**Data:** report's `limit_breaches` + the configured `risk.limits` (var per level, sensitivities per factor).

**Layout:** a table of every limit: factor/level · observed · limit · headroom (% used) · status (green buffer / amber near-miss / red breach). Breaches are sorted to the top and link to the driving factor (RK-B2 by-factor) and trades. (Editing limits is a gated write action — deferred past V1; read-only first.)

---

## 5. Risk-measure-adaptive presentation

Like Pricing's instrument-adaptive cards, each risk **measure** gets a presentation tuned to how it's read:

| Measure | Source | Headline | Plain-English caption |
|---|---|---|---|
| **Sensitivities** | `RiskResult.sensitivities[]` | per-factor PV/bump-unit | "Lose/gain X per +1bp / +1% / +1 vol-pt move" |
| **Sens-VaR / ES** | `VarResult` | loss by confidence level | "1-in-N bad day loss; ES = average loss when it's worse" |
| **Full-reval VaR** | `FullRevalVarResult` | loss + worst day | "Same idea, fully repriced — captures non-linearity exactly" |
| **ES-under-shock** | `FullRevalVarResult` (`@shocked`) | capital delta | "Extra capital this scenario would require" |
| **P&L explain** | `PnlExplainReport` | coverage % | "How much of the move the Greeks explain" |
| **Backtest** | `BacktestResult` | zone | "Is the VaR model under-forecasting risk?" |
| **Limits** | `LimitBreach[]` | headroom / breach | "How close are we to the mandate ceiling?" |

**Sign & unit conventions (stated once, applied everywhere):** VaR/ES/losses are **positive numbers** (a loss); sensitivities keep their natural sign with gain/loss color; bump units are explicit (bp for IR/credit/rates-vol, % for equity/FX spot, vol-points for equity vega, skew/term tilt units for the slope factors).

---

## 6. Technical lens — deep dive

- **RK-T1 Risk Engine Explorer:** component map — `RiskEngine` (bump-and-revalue) → `VarEngine` (sensitivity hist-sim) / `FullRevalVarEngine` (full-reval, workers) → `ScenarioEngine` → `PnlExplainEngine` → `VarBacktester` → `LimitMonitor` → `RiskReport`. Each node shows its file, what it consumes from the pricing engine, and its output dataclass.
- **RK-T2 Risk Run Trace:** `RunTrace` of a risk run: resolve base data → per-trade sensitivities (bump count) → factor-history load (`factor_snapshot_id`) → VaR panel build → scenarios → backtest → report write. Stage timings; the heavy full-reval stage shows the `workers` fan-out (chunks × days × trades).
- **RK-T3 Factor History / Workers:** the date-aligned factor matrix (which factors, how many aligned days, gaps), `factor_snapshot_id`, and the full-reval worker chunking. Surfaces `risk_factor_missing_total` / `risk_limit_breach_total` metrics.

---

## 7. Interaction & visual principles (intuitive-first)

1. **Translate before you tabulate:** every abstract number gets its one-line meaning before any grid.
2. **Reuse the lineage metaphor:** sensitivities render in the same `LineageGraph` as pricing; VaR gets the histogram — both make "where did this come from" visual.
3. **Trust signals are loud:** missing factors, P&L-explain coverage, backtest zone, limit breaches are first-class, not buried.
4. **Concentration at a glance:** contribution bars on factors/trades so the dominant risks pop.
5. **Consistent sign/units:** losses positive; sensitivities signed+colored; units always labeled; numbers humanized with exact-on-hover.
6. **Two snapshots, clearly:** show both `snapshot_id` (pricing inputs) and `factor_snapshot_id` (history) — risk reproducibility needs both.

---

## 8. Live vs stored, dates & snapshots

Consistent with the Pricing decision: **stored-only in V1.** The Risk workspace reads persisted `RiskReport` JSON via `/risk/report`; the live `/risk/var`, `/risk/sensitivities`, `/risk/scenario` endpoints sit behind a locked Stored/Live toggle for a later version. The top-bar valuation-date selector drives which report loads. Every risk number shows both snapshots in its provenance.

---

## 9. BFF contracts (Risk)

The risk-run JSON already contains everything; the BFF mostly serves and reshapes it.

| Need | Endpoint | Backed by |
|---|---|---|
| Full risk report (RK-B1/B2) | `GET /risk/report?valuation_date=` | `risk_<run_id>.json` |
| Sensitivity lineage (RK-B3a) | `GET /lineage/{trade_id}?with=risk` | engine explain-pass + bump-and-revalue |
| VaR construction (RK-B3b) | `GET /risk/var-construction?valuation_date=` | `var` block + factor history series (P&L vector for the histogram, factor table with `factor_stdev`/source) |
| P&L explain (RK-B4) | `GET /risk/pnl-explain?valuation_date=&scenario=` | `pnl_explain` block |
| Backtest (RK-B5) | `GET /risk/backtest?valuation_date=` | `backtest` block (+ exceptions timeline if persisted) |
| Limits (RK-B6) | `GET /risk/limits?valuation_date=` | `limit_breaches` + `risk.limits` config |

```jsonc
// GET /risk/report → drives RK-B1/B2 (shape mirrors RiskReport.to_dict())
{ "valuation_date":"2026-05-29","run_id":"15054ab1…","snapshot_id":"56a863cf…",
  "factor_snapshot_id":"e1f5a9c3…","generated_at":"…",
  "book_sensitivities":{ "IR_PARALLEL":-288017.06,"CS01":-236372.70,
                          "EQUITY_DELTA:NVDA":500000.0,"FX_DELTA:TWD":546929.12, … },
  "var":{ "horizon_days":1,"lookback_days":250,"n_scenarios":247,
          "var":{"0.95":245000,"0.975":385000,"0.99":512000},
          "es":{"0.99":624000, … },"factor_stdev":{ … },"missing_factors":[] },
  "full_reval_var":{ "base_pv":…, "var":{ … },"es":{ … },"worst_day":"2020-03-16","worst_pnl":-1.8e6 },
  "scenarios":[ { "scenario":"ai_bust","total_pnl":-28000000,"pnl_by_asset_class":{ … } } ],
  "pnl_explain":{ "scenario":"taiwan_geopolitical","coverage":0.94,"actual_total":…,
                  "explained_by_factor":{ "equity_delta":…, "fx":… },"residual_total":… },
  "backtest":{ "level":0.99,"n_days":250,"n_exceptions":3,"expected_exceptions":2.5,
               "exception_rate":0.012,"zone":"green","kupiec_lr":0.15 },
  "limit_breaches":[] }

// GET /risk/var-construction → the histogram + factor table (RK-B3b)
{ "pnl_vector":[ -120000, 45000, … ],          // historical daily hypothetical P&L (n_scenarios)
  "var":{"0.99":512000}, "es":{"0.99":624000},
  "factors":[ { "name":"EQ_NVDA","maps_to":"EQUITY_DELTA:NVDA","factor_stdev":0.0145,
                "contribution":210000,"file":"equity/prices_daily.csv","column":"close" } ],
  "missing_factors":[] }
```

> No new heavy backend work: the risk run already writes the full report; `var-construction` additionally needs the **P&L vector** persisted (the `VarEngine.pnl_series` it already computes) so the UI can draw the distribution rather than just the quantiles.

---

## 10. Frontend specifics

- **Routes:** `/risk` (RK-B1), `/risk/drill` (RK-B2, with `?by=asset|factor|trade`), `/risk/method` (RK-B3), `/risk/pnl-explain`, `/risk/backtest`, `/risk/limits`, `/risk/tech/*`.
- **Query keys:** `[risk, report, date]`, `[risk, varconstruction, date]`, `[lineage, id, date, "risk"]` — keyed by date + both snapshot ids.
- **Components (reuse + new):** reuse `MetricGrid`, `LineageGraph`/`LineageInspector`, `ProvenanceBadge`, `Zone`, `LiveStoredToggle`. New: `VarMatrix`, `SensitivityTable`, `ContributionBar`, `VarHistogram`, `PnlExplainWaterfall`, `BacktestTimeline`, `LimitTable`, `MissingFactorBanner`.
- **States:** missing-report empty state ("no risk run for this date — run `risk_runner`"); missing-factors and amber/red zones as designed banners, never silent.

---

## 11. Build order (Risk slice = overall Phase 2)

1. `GET /risk/report` BFF + **RK-B1 dashboard** (VaR matrix, book sensitivities, zone, limits) — highest controller value on existing data.
2. **RK-B2 drill** (by asset class / factor / trade) — pure reshaping of the same report.
3. **RK-B3 methodology** — (a) sensitivity lineage reuses Pricing's `LineageGraph` (`?with=risk`); (b) `VarHistogram` from `/risk/var-construction` (persist the P&L vector).
4. **RK-B4 P&L explain** waterfall + coverage.
5. **RK-B5 backtest** + **RK-B6 limits** (read-only).
6. Technical lens **RK-T1–T3**.

---

## 12. Open questions / decisions for Risk

1. **P&L-vector persistence:** confirm we persist `VarEngine.pnl_series` output in the risk run so RK-B3b can draw the distribution (recommended; small change).
2. **Which scenarios in the dashboard:** show all named scenarios' total P&L, or top-N worst? (Recommend top-N worst with "see all".)
3. **Full-reval VaR availability:** it's an optional/heavy run (`--full-reval-var`). Dashboard should degrade gracefully when absent (show sens-VaR only, note full-reval not run).
4. **Limits editing:** read-only in V1 (consistent with stored-only), gated write later — confirm.
