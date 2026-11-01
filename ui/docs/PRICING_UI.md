# Pricing (Valuation) — UI Deep Dive

> **Parent:** `ui/docs/UI_DESIGN.md` (§6 Pricing). This document is the detailed, build-ready spec for the Pricing workspace.
> **North star:** a non-quant desk user, a model owner, and an auditor should each be able to answer their question in ≤3 clicks — *What is it worth? · What went into it? · How was it computed?* — without reading code.
> **Grounded in:** `PricingResult` (`src/pricer/ql/common/pricing_result.py`), the asset-class pricers under `src/pricer/ql/`, `portfolio.json`, and the service endpoints in `src/pricer/service.py`.

---

## 1. UX philosophy — "three questions, one ladder"

Every pricing screen answers one of three questions, and they nest as a ladder:

| Altitude | Question | Page | Who |
|---|---|---|---|
| **L1 Book** | *What is the book worth?* | Book Valuation | desk, PM |
| **L2 Trade** | *What is this trade worth, and is it trustworthy?* | Trade Valuation | desk, risk |
| **L3 Lineage** | *Why exactly is it worth that?* | Calculation Lineage | model owner, audit, curious desk |

Three design commitments keep it **intuitive**:

1. **Plain-English first, numbers second.** Every measure carries a one-line human caption ("DV01 — you gain/lose this much per 1bp rate move"). Jargon is always hover-to-explain.
2. **One altitude per screen.** A page shows a single level of detail and obvious affordances to climb up or descend. Nothing tries to show the whole story at once.
3. **The lineage is the explanation.** Pricing logic is not documented in prose the user must trust — it is *rendered* as the actual inputs → intermediates → result, with every leaf node traceable to a market-data point and its provenance hash.

---

## 2. Personas & jobs-to-be-done (pricing-specific)

- **Desk / PM (business end user):** "Show me today's book value and which trades moved. Is anything stale or blocked?" → lives on L1–L2, glances at warnings.
- **Risk / control analyst (business internal):** "Did this PV pass IPV and golden checks? What changed since yesterday?" → L2 + Governance + PV history.
- **Model owner / quant (technical):** "Walk me from PV down to the discount factors and the curve point." → L3 lineage + Technical lens.
- **Auditor:** "Reproduce this number: which snapshot, which code, which config." → provenance chip everywhere → reproducibility recipe.

---

## 3. Pricing workspace — navigation map

```
PRICING (Valuation)                              [Business ⇄ Technical]
│
├─ BUSINESS
│   ├─ PR-B1  Book Valuation        (L1)  total PV, by asset class, status, movers
│   ├─ PR-B2  Trade Valuation       (L2)  one trade: value card + measures + warnings
│   ├─ PR-B3  Calculation Lineage   (L3)  ★ inputs → intermediates → final value
│   ├─ PR-B4  PV History                 time series per trade, run-to-run diff
│   ├─ PR-B5  Model Inventory             governed model per trade type (read)
│   └─ PR-B6  Governance Verify           IPV + golden + coverage (pass/fail)
│
└─ TECHNICAL
    ├─ PR-T1  Engine Component Explorer   input → loader → ql, click to inspect
    ├─ PR-T2  Price Request Trace         one /price call, staged with timings
    └─ PR-T3  Curve Cache & Workers       bootstrap reuse, parallelism, latency
```

Cross-links: every trade row → PR-B2; PR-B2 "Explain" → PR-B3; PR-B3 market-data leaf → **Market Data ▸ Feed Detail**; PR-B2 "Risk" → **Risk ▸ Trade**; provenance chip → **System/Ops ▸ Run**.

---

## 4. Business pages — deep dive

### PR-B1 · Book Valuation (L1)

**Goal:** the 5-second answer to "what's the book worth and is it healthy?"

**Data:** `GET /results/book/{date}` (stored) or `POST /price-portfolio` (live re-price). Toggle **Stored ⇄ Live** is explicit (see §8 "live vs stored").

**Layout**
- **Hero band:** total book PV (large), Δ vs prior valuation date, currency note (book is USD-reported; FX legs converted at spot).
- **By-asset-class strip:** rates · bonds · equity · options · credit · fx — each a card with subtotal PV, trade count, and a status dot. Click → filtered trade list.
- **Status ribbon:** `n_ok / n_blocked / n_failed`, total warnings, drift-alert count. Blocked/failed are never hidden — they show as a red count that filters the table.
- **Trade table** (TanStack Table): trade_id · description · asset_class · type · currency · **PV** · status · #warnings · last run. Sort by absolute PV move = "today's movers". Row click → PR-B2.

**Intuitive touches**
- Sign + magnitude coloring on PV and Δ (gain green / loss red, colorblind-safe palette + sign glyph).
- "Stale" and "blocked" trades carry an inline `DqBadge`; hovering explains why (e.g. "Pensford curve 6 days old > 7-day tolerance").
- Empty/blocked asset classes still render (greyed) so the book's full shape is always visible.

---

### PR-B2 · Trade Valuation (L2)

**Goal:** one trade's value, the handful of numbers that characterize it, and whether to trust it — **without** yet diving into the full calc.

**Data:** `POST /price` → `PricingResult.to_dict()`.

**Layout — the "Valuation Card"** (instrument-adaptive; see §5):
```
┌ TRADE_22 · 10Y SOFR payer IRS · USD ───────────────── prov: run·snap·sha ┐
│  PV   $ 125,000.42        ▲ +$4.2k vs 2026-05-28      [ Explain ▸ ] [Risk ▸]│
│  ─ Key measures ─────────────────────────────────────────────────────────  │
│   Fair rate 3.80%   Fixed 3.80%   DV01 $50,000/bp   Notional $50.0mm        │
│  ─ Legs ─────────────────────────────────────────────────────────────────  │
│   Fixed  +125,000.42      Float   ~0.00                                      │
│  ─ Diagnostics ──────────────────────────────────────────────────────────  │
│   engine DiscountingSwapEngine · payer · 2026-05-29 → 2036-05-29            │
│  ⚠ warnings (0)                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Sections (consistent across instruments):**
1. **Value** — `pv`, Δ vs prior, currency; the two primary CTAs **Explain ▸** (→ PR-B3) and **Risk ▸**.
2. **Key measures** — `measures{}` rendered as `MetricCell`s with captions and units (instrument-aware ordering — §5).
3. **Legs** — `leg_pvs{}` as a small bar/row (fixed vs float; per-option-leg; usd vs foreign).
4. **Diagnostics** — `diagnostics{}` as quiet key/value (engine, dates, direction, source).
5. **Warnings** — `warnings[]` promoted to a visible banner when non-empty (staleness/data-quality).
6. **Provenance chip** — run_id / snapshot_id / git_sha / config_hash → popover → "reproduce".

**Intuitive touches**
- Each measure name is a hover-glossary term ("Fair rate — the fixed rate that would make today's PV zero; compare to your contract's fixed rate to see if you're in/out of the money").
- "What changed?" inline: if a prior result exists, show per-measure deltas.

---

### PR-B3 · Calculation Lineage (L3) ★ the centerpiece

**Goal:** make the pricing logic *self-evident* by rendering the real computation top→down, every leaf traceable. This is the page that turns a black box into an explanation.

**Data:** `GET /lineage/{trade_id}` (new BFF endpoint; full contract in §9).

**Frame:** left = `LineageGraph` (React Flow DAG), right = `Inspector`. Three horizontal tiers, collapsible, top→down:

```
TRADE_22 · Lineage          Breadcrumb: Book › rates › TRADE_22 › Lineage
┌ GRAPH ───────────────────────────────────────────────┐ ┌ INSPECTOR ───────────────┐
│ ① FINAL                                               │ │ Discount factor          │
│    [ PV 125,000.42 ] = [fixed +125,000] − [float ~0]  │ │ pay 2027-05-29           │
│         │                                             │ │ DF 0.96312               │
│ ② INTERMEDIATES                                       │ │  = exp(−z·t), z=3.84%,    │
│    [ cashflow ladder ▸ ]  [ zero rates ]              │ │    t=1.003y               │
│    [ discount factors ]   [ forward rates ]           │ │ from curve USD-SOFR       │
│    [ fair rate 3.80% ]                                │ │ (DISCOUNT)                │
│         │                                             │ │  ▸ open curve             │
│ ③ INPUTS                                              │ │  ▸ open feed (Pensford)   │
│    market:  USD-SOFR DISCOUNT  ·  USD-SOFR FORECAST    │ │ as-of 2026-05-29 · 0d ✓   │
│    static:  notional 50mm · payer · fix 3.8% · Act/360│ │ sha 9f3a… fetched …       │
└───────────────────────────────────────────────────────┘ └───────────────────────────┘
[ plain-English: "PV is the discounted fixed coupons minus the projected floating
  payments. Both legs use the SOFR curve built from the Pensford forward snapshot." ]
```

**Tier 1 — Final values:** `pv` and the `leg_pvs` decomposition with the actual arithmetic relationship shown (PV = fixed − float). Selecting a leg highlights the flows that compose it.

**Tier 2 — Intermediates (the "how"):**
- **Cashflow ladder** (swaps/bonds/caps): a table — pay date · accrual factor · rate (fixed or projected forward) · notional · cashflow · **discount factor** · **PV of flow**. The column sum reconciles to the leg PV (shown explicitly). This single table *is* the pricing logic for linear instruments.
- **Curve panel:** `CurveChart` overlaying par / zero / discount-factor / forward by tenor, with the exact tenor points the trade touched marked. Toggling curves teaches the relationship (par → bootstrap → zero → DF → forward).
- **Instrument extras:** option d1/d2/IV per leg; CDS hazard & survival curve; swaption ATM/annuity/normal-vol; FX CIP forward & leg DFs (§5).

**Tier 3 — Inputs:**
- **Market data** — each resolved `MarketDataRequirement` as a node: kind · purpose · index · `as_of_date` · staleness badge · **link to Market Data feed detail** + provenance sha. This is where lineage crosses into the Market Data subsystem.
- **Static / reference** — trade economics from `portfolio.json` (notional, direction, coupon/strike, schedule) and conventions (day count, calendar, business-day rule, frequency). Rendered as a labeled "trade ticket".

**Interactions**
- Click any node → Inspector shows the **value, the formula/role in one line, and provenance** (for inputs).
- **Trace path:** selecting the final PV dims everything except the chain of nodes that fed it down to atomic sources — a literal "where did this number come from" highlight.
- **Plain-English caption** under the graph summarizes the instrument's pricing story in 1–2 sentences (templated per trade type).
- **Reconciliation checks** rendered as small ✓ where sums tie out (Σ flow PV = leg PV; fixed − float = PV), so the user *sees* the math close.

> This page fully satisfies the requirement: *all market data and static data inputs, key intermediate values, and final calculated values* — and makes them intuitive by showing the arithmetic, not just listing fields.

---

### PR-B4 · PV History

`SeriesChart` of PV per valuation date (`GET /results/pv-history/{trade_id}`): status overlay (ok/blocked/failed markers), hover → run_id, and a **run-to-run diff** drawer (pick two dates → measure-by-measure delta, plus which snapshot/curve changed). Answers "why did it move?" by linking each step to its run + snapshot.

### PR-B5 · Model Inventory (read)

`GET /governance/model-inventory`: per trade type → model, engine, **assumptions**, **limitations**, approved use, IPV controls. Coverage gaps (traded type with no governed entry) flagged red. Intuitive framing: "This is the model we use for this instrument, and what it can/can't do."

### PR-B6 · Governance Verify

`POST /governance/verify`: a pass/fail control room — **Golden** (expected vs actual vs tolerance, snapshot match), **IPV** (per-trade identity/method/sanity checks with the two compared values), **Inventory coverage**. Each failure links to the trade's PR-B2/PR-B3.

---

## 5. Instrument-adaptive valuation & lineage

The Valuation Card (PR-B2) and Lineage tiers (PR-B3) **reshape per `trade_type`** so each instrument reads naturally. The card chrome is constant; the contents below come from the actual pricer outputs.

| Instrument (trades) | Headline measures (ordered) | Legs | Lineage "intermediates" emphasis |
|---|---|---|---|
| **OIS swap** #22 | fair_rate, fixed_rate, **dv01**, notional | fixed / float | cashflow ladder + SOFR curve (zero/DF/forward); fair-rate vs fixed callout |
| **European swaption** #23 | premium (PV), ATM/strike, underlying fair_rate, **normal_vol_bp**, vega | — | underlying swap (fair rate, annuity), Bachelier inputs (vol, expiry), DFs |
| **Bermudan swaption** #24 | premium, exercise schedule, normal_vol, vega | — | exercise dates, underlying swap, vol; tree/engine note |
| **Cap** #25 | premium, ATM strike, normal_vol, vega | per-caplet | caplet ladder (each caplet: forward, vol, DF, value) |
| **Corp bond** #15–19 | clean_price, dirty_price, accrued, **ytm**, duration, dv01, z_spread | — | cashflow ladder; discount = SOFR + issuer spread (show the spread add-on) |
| **UST bond** #20–21 | clean/dirty, accrued, ytm, duration, dv01 | — | cashflow ladder on bootstrapped UST par curve |
| **Cash equity** #1–6 | **spot**, quantity, market value, dividend_yield | — | PV = quantity × spot (trivial, shown explicitly); spot source + as-of date |
| **Equity option** #7–14 | **delta_shares**, delta_cash, gamma, **vega_1volpt**, spot, div_yield, per-leg IV | per option leg | per-leg BSM: spot+div from chain put-call parity, per-leg IV from smile, d1/d2 |
| **CDS** #28–30 | fair_spread, par_spread, **cs01**, hazard, survival prob, recovery | premium / protection | hazard calibrated to par; survival curve; coupon vs default leg |
| **FX forward** #27 | spot, strike, **fair_forward** (CIP), fx_delta, usd_notional | — | CIP: USD DF, foreign DF, implied forward vs contract strike |
| **XCCY swap** #26 | spot, usd/foreign notionals, fixed rates, fx_delta, next coupon | usd / foreign | fixed-for-fixed legs, notional exchange, FX conversion at spot |

**Why this matters for intuitiveness:** a swap user sees a cashflow ladder (their mental model); an options user sees spot/vol/greeks; a CDS user sees hazard and survival. The UI speaks each instrument's native language instead of a generic field dump.

**Plain-English templates** (under the lineage graph), e.g.:
- *Swap:* "PV = discounted fixed coupons − projected floating coupons, both off the SOFR curve."
- *Cash equity:* "Value = your share quantity × the latest close. Nothing else moves it."
- *Option:* "Each leg is Black-Scholes priced off the chain's spot, dividend, and the implied vol at that strike/expiry; the trade value is the signed sum."
- *CDS:* "We solved for the hazard rate that reprices the quoted 5Y spread, then valued the premium vs protection legs off it."

---

## 6. Technical lens — deep dive

### PR-T1 · Engine Component Explorer
Interactive 3-layer diagram of the real architecture:
`input` (Trade, `market_data_requirements()`) → `loader` (MarketDataLoader → asset adapters → MarketDataSnapshot) → `ql` (factory → asset pricer → QL engine). Click a node → file path, responsibility, the data class it emits (`ParOISCurveData`, `EquityMarketData`, …), and its place in a real trade's flow. Doubles as living documentation.

### PR-T2 · Price Request Trace
`RunTrace` of a single `/price`: build ctx → pin snapshot → `market_data_requirements()` → `loader.resolve()` (per requirement, with staleness outcome) → `get_pricer()` → QL build (curve-cache **hit/miss**) → result + provenance stamp. Each stage shows duration and the object handed forward. This is the technical mirror of PR-B3 (same trade, mechanical view).

### PR-T3 · Curve Cache & Workers
Portfolio-run view: curve-cache stats (bootstraps run vs hits), `--workers` fan-out, and the `pricer_price_seconds` latency histogram by asset class. Answers "is it fast, and why."

---

## 7. Interaction & visual design principles (intuitive-first)

1. **Progressive disclosure:** card → expand legs → open lineage → drill to feed. Never more than one new layer per click.
2. **Captions on every metric:** a `MetricCell` always pairs value + unit + one-line plain-English caption (hover for the full glossary entry). New users learn the domain by using the app.
3. **Sign & unit clarity:** consistent gain/loss color + sign glyph; units always shown ($, bp, %, vol-pt, shares); large numbers humanized ($125.0k, $50.0mm) with exact value on hover.
4. **Show the arithmetic:** wherever a value is a sum or product of others (PV = fixed − float; Σ flow PV = leg PV; value = qty × spot), render the relation and a ✓ when it reconciles.
5. **Stale/blocked is loud, not hidden:** warnings and DQ issues surface as badges/banners at every altitude.
6. **Provenance is always one click away** but never in the way (compact chip → popover).
7. **Live vs stored is explicit:** a labeled toggle + timestamp; the user always knows whether they're looking at a fresh re-price or a stored run.
8. **Empty/error/loading states are designed:** blocked trade → "why" + remediation link; missing lineage → "this instrument's intermediates aren't exposed yet" rather than a blank graph.

---

## 8. Live vs stored, dates & snapshots

- **Stored** (default): reads the Parquet results store (`/results/*`) — fast, historical, exactly what a batch run produced.
- **Live**: calls `/price` / `/price-portfolio` against the service's startup snapshot — for "reprice now" / what-if framing.
- Top-bar **valuation date** and **snapshot** selectors drive both; PR-B2/PR-B3 always show which snapshot a number came from. Note the portfolio's instrument-specific default dates (e.g. equity options value at the 2026-06-17 chain snapshot, rates/bonds/cash-equity/FX at 2026-05-29) — the UI surfaces this so a user isn't confused by an "as-of" mismatch.

---

## 9. BFF contract — `GET /lineage/{trade_id}` (refined)

Extends Appendix A.1 of the parent doc with instrument variation. Query: `?valuation_date=&valuation_type=PV&with=risk`.

```jsonc
{
  "trade_id": "TRADE_22", "trade_type": "ois_swap", "currency": "USD",
  "valuation_date": "2026-05-29",
  "plain_english": "PV is discounted fixed coupons minus projected floating coupons…",

  "final": { "pv": 125000.42, "leg_pvs": {"fixed":125000.42,"float":-0.0},
             "reconciliation": [{"expr":"fixed − float","value":125000.42,"ok":true}],
             "measures": {"fair_rate":0.0380,"fixed_rate":0.038,"dv01":50000.0,"notional":50000000} },

  "intermediates": {
    "curves": { "USD-SOFR": { "tenors":["1M","3M","6M","1Y","2Y","5Y","10Y"],
        "par_rates":[…], "zero_rates":[…], "discount_factors":[…], "forward_rates":[…],
        "tenors_used":["1Y","2Y","5Y","10Y"] } },
    "cashflows": [ { "leg":"fixed","pay_date":"2027-05-29","accrual":1.0028,
        "rate":0.038,"notional":50000000,"amount":190532.0,
        "discount_factor":0.96312,"pv":183500.1 } /* … */ ],
    // instrument-specific blocks, present only when relevant:
    "option_legs": [ { "leg":"leg0_call_180","expiry":"…","strike":180,"iv":0.32,
                       "d1":…, "d2":…, "delta":…, "gamma":…, "per_share":…, "pv":… } ],
    "credit": { "hazard":0.0167, "recovery":0.40, "survival":[{"t":1,"p":0.983}, …] },
    "swaption": { "atm_rate":0.0395, "strike":null, "normal_vol_bp":95.0, "annuity":… },
    "fx": { "usd_df":…, "foreign_df":…, "fair_forward":157.1, "strike":157.5 }
  },

  "inputs": {
    "market_data": [ { "kind":"OIS_CURVE","purpose":"DISCOUNT","index":"USD-SOFR",
        "as_of_date":"2026-05-29","feed_id":"rates/pensford_sofr_forward.csv",
        "staleness_days":0,"warning":null,
        "provenance":{"content_sha256":"9f3a…","fetched_at":"…"} } ],
    "static": { "notional":50000000,"direction":"payer","fixed_rate":0.038,"swap_tenor":"10Y",
        "conventions":{"day_count":"Actual/360","calendar":"US.SOFR",
                       "bdc":"ModifiedFollowing","frequency":"Annual"} }
  },

  "sensitivity_lineage": [ /* present when ?with=risk; powers Risk RK-B3 */ ],
  "warnings": [],
  "provenance": { "run_id":"…","snapshot_id":"…","git_sha":"…","config_hash":"…",
                  "pricer_class":"OISSwapPricer","priced_at":"…" }
}
```

**Backend work this implies (one focused engine change):** an opt-in "explain" pass that captures, at the tenors/dates the trade actually uses, the zero/discount/forward values and the cashflow table the pricers already build internally but currently discard. Everything else in the payload already exists on `PricingResult` + the resolved `MarketDataSet` + `portfolio.json`.

---

## 10. Frontend specifics

- **Routes:** `/pricing` (PR-B1), `/pricing/trade/:id` (PR-B2), `/pricing/trade/:id/lineage` (PR-B3), `/pricing/trade/:id/history` (PR-B4), `/pricing/inventory` (PR-B5), `/pricing/governance` (PR-B6), `/pricing/tech/*` (PR-T1–3). Lens toggle persists in URL/query.
- **Query keys:** `[pricing, book, date, snapshot]`, `[pricing, trade, id, date, snapshot]`, `[lineage, id, date]` — keyed by snapshot so cache is provenance-correct.
- **Pricing-specific components:** `ValuationCard`, `MeasureGrid`, `LegBreakdown`, `CashflowLadder`, `CurveChart`, `LineageGraph` (+ `LineageInspector`), `ReconcileCheck`, `ProvenanceBadge`, `LiveStoredToggle`, `WarningBanner`.
- **States:** loading skeletons per card; blocked-trade and missing-lineage empty states with remediation links; error toasts that preserve the provenance context.

---

## 11. Build order (Pricing slice = overall Phase 1)

1. `ValuationCard` + `MeasureGrid` on `/price` → **PR-B2** (immediate value, existing endpoint).
2. **PR-B1** book view on `/results/book` + movers table.
3. **PR-B3 Lineage** — ship the `/lineage` BFF + `LineageGraph`/`CashflowLadder`/`CurveChart`. The centerpiece; do swap + cash-equity first (simplest, most legible), then options/bonds/CDS/FX.
4. **PR-B4** history + run diff.
5. **PR-B5/B6** governance (read) — reuse existing endpoints.
6. Technical lens **PR-T1–T3**.

---

## 12. Decisions (resolved) & open items

**Resolved:**
1. **Lineage rollout order — DECIDED:** ship **swap + cash equity first** (cleanest stories), then iterate to options/bonds/CDS/FX.
2. **Live re-price — DECIDED:** **stored-only in V1.** PR-B1/B2/B3 read the Parquet results store (`/results/*`); on-demand `/price` what-if is deferred to a later version. The `LiveStoredToggle` ships locked to "Stored" (live path stubbed behind a flag).
3. **Curve math — DECIDED:** compute zero/DF/forward in the **engine "explain" pass** (authoritative); the BFF passes them through, it does not re-derive.

**Open:**
4. **Numbers formatting standard:** confirm humanized ($50.0mm) with exact-on-hover, bp for rates, vol-points for vega.

> **V1 implication of (2):** lineage is served from stored runs. The `/lineage/{trade_id}` BFF endpoint reads the latest stored result for the trade+date and the engine explain-pass output persisted alongside it — it does not trigger a live re-price. The explain-pass intermediates must therefore be **written to the results store during the batch run**, not computed on request.
```
