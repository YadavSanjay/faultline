# Faultline scenario set and layer ablation: design

Status: **approved design** (2026-10-15). Delivered as test-first changes in the order of §9.

Faultline generates stress scenarios for an AI-themed book from four enrichment sources:

- **MacroRAG:** macro research and historical market episodes;
- **XbrlRag:** SEC 10-K/Q risk factors and XBRL facts;
- **market intelligence:** options, crowding, GPR/EPU, physical throughput, implied correlation;
- **key events:** SEC 8-K items and GDELT news.

This design does three things:
- fixes the named-scenario set at ten high-impact scenarios (§4);
- assigns each source a job in those scenarios (§5);
- adds the measurement layer that shows what each source contributes, and whether that contribution
  is worth having (§6–§7).

Evidence base:
- dual-RAG run `8da52756` (valuation date 2026-10-06, `core` book, 38 trades, MacroRAG world view
  `macro-2026-10-15-9a3daf60fdf8`);
- the configs in [`configs/scenariogen/`](../../configs/scenariogen/);
- market history under `data/marketdata/`;
- the code as of that run.

P&L is full revaluation from that run unless marked "linear" (§10).

## Contents

1. [Findings](#1-findings)
2. [How a named scenario is built today](#2-how-a-named-scenario-is-built-today)
3. [Severity](#3-severity)
4. [The scenario set](#4-the-scenario-set)
5. [The four sources: roles, coverage and gaps](#5-the-four-sources-roles-coverage-and-gaps)
6. [Measurement gaps](#6-measurement-gaps)
7. [Measurement design](#7-measurement-design)
8. [Settled parameters](#8-settled-parameters)
9. [Delivery plan](#9-delivery-plan)
10. [Appendix: method and data notes](#10-appendix-method-and-data-notes)

---

## 1. Findings

The four sources are wired, gated, cited and provenance-stamped. The current named-scenario set
doesn't show their value:

1. **Eight scenarios, about two risks.**
   - One per-trade P&L pattern explains 68% of the variation across the set.
   - `rate_capital` and `funding_basis_squeeze` are 0.98 similar.
   - NVDA is the worst trade in 7 of 8.
2. **Half the set uses no enrichment.** `momentum_crash`, `semis_dispersion`,
   `funding_basis_squeeze` and `real_rate_repricing` are purely authored.
3. **Enrichment reaches very little of the book.**
   - XbrlRag shapes two names (NVDA, AMD) out of the 12 it covers.
   - News and crowding scale only XbrlRag legs, so they also reach just those two names.
   - Options data shapes no shock: it feeds Σ shrinkage, plausibility flags and the liquidity
     haircut only.
4. **Severity isn't anchored, and the history is short.**
   - The mild tier moves NVDA −57% to −68% over 10 days, against a worst observed 10-day move of
     −32% since 2018.
   - Some authored drivers are beyond history on their own (XLK −30% against a worst of −22.4%).
   - Equity history starts in 2018 and rates in 2022: no dot-com or 2008 analog.
   - Ten days can't express the core risk, a multi-month AI deflation (NVDA −66% peak to trough in
     2022).
5. **The episodes that hurt this book most are missing.** Linear estimates on stock and FX legs:

   | Episode | Loss | What fails |
   |---|---|---|
   | DeepSeek day | −$66M in one day | power names lose −$24M |
   | Yen-carry unwind | −$127M | the JPY forward and the INTC/AMD pair |
   | July 2024 rotation | −$70M | SPX fell only 3.7%, so the index hedges barely pay |
   | Tariff week | — | equities, Treasuries and gold fell together |

   No scenario can express issuer credit stress.

---

## 2. How a named scenario is built today

```
scenarios.json (authored drivers + overlays, tier multiplier 1.0 / 1.5 / 2.0)
  │
  ├─ MacroRAG synthesis ──► replaces macro driver legs (UST, IG/HY OAS, VIX, MOVE, USD/TWD, USD/JPY)
  │                          gate: citations + freshness (episode cards exempt)
  ├─ XbrlRag (ARag) ──────► per-name company leg = seed × XBRL-fact calibration
  │     ├─ × news multiplier      (8-K items + GDELT tone, clamp [0.8, 1.5])
  │     └─ × crowding multiplier  (FINRA days-to-cover percentile, clamp [0.8, 1.4])
  │     added as an idiosyncratic overlay on top of the systematic move (additive split)
  ├─ state multiplier ────► GPR (taiwan only) × throughput (ai_bust only), each clamp [0.85, 1.4]
  │
  ▼
conditional-covariance propagation (Σ: 250d EWMA λ=0.94, shrinkage set by COR3M regime)
  ▼
full revaluation ─► plausibility (hard bounds gate; n-σ and option-implied checks flag only)
  ▼
liquidity exit value (F1) + collateral / cash (F2) ─► report, attribution, narrative
```

Code:
- [`dualrag.py`](../../src/scenariogen/dualrag.py): layer composition;
- [`runner.py`](../../src/scenariogen/runner.py): state multiplier and materialize;
- [`scenario_gen.py`](../../src/scenariogen/scenario_gen.py): propagation. Drivers are tier-scaled;
  authored overlays are not.

### 2.1 The current eight scenarios (run `8da52756`)

| Scenario | Mild / adverse / severe P&L ($M) | What drives the loss (mild) | Enrichment used |
|---|---|---|---|
| `taiwan_geopolitical` | −89.7 / −127.4 / −151.1 | NVDA −60.2, TSM −38.7 | MacroRAG (7 legs), XbrlRag NVDA/AMD (uncalibrated), news, crowding, GPR ×1.36 |
| `ai_bust` | −79.5 / −110.2 / −122.7 | NVDA −72.2 (91% of loss) | MacroRAG (5 legs), XbrlRag NVDA (Revenue calibration ×1.23) + AMD, news, crowding, throughput ×0.85 |
| `rate_capital` | −75.8 / −109.2 / −140.1 | equities −64.7 (via Σ), bonds −26.0 | MacroRAG (3 legs) |
| `keyman_nvda` | −78.5 / −115.0 / −137.5 | NVDA −60.1 | MacroRAG (IG OAS), XbrlRag NVDA, news, crowding |
| `momentum_crash` | −11.8 / −17.9 / −23.9 | spread thin across semis | none |
| `semis_dispersion` | −3.0 / −4.5 / −6.0 | MU, AVGO | none |
| `funding_basis_squeeze` | −73.5 / −104.7 / −133.9 | equities −68.7 (via Σ); credit **+1.3** | none |
| `real_rate_repricing` | −32.6 / −48.4 / −63.7 | equities −18.0, bonds −17.0 | none |

Cosine similarity of per-trade P&L vectors (mild tier):

|  | taiwan | ai_bust | rate_cap | keyman | momentum | semis_disp | funding | real_rate |
|---|---|---|---|---|---|---|---|---|
| taiwan | 1.00 | 0.90 | 0.79 | 0.87 | 0.68 | 0.07 | 0.75 | 0.60 |
| ai_bust | | 1.00 | 0.76 | **0.93** | 0.62 | 0.05 | 0.69 | 0.61 |
| rate_cap | | | 1.00 | 0.75 | 0.74 | −0.20 | **0.98** | **0.90** |
| keyman | | | | 1.00 | 0.43 | 0.02 | 0.70 | 0.63 |
| momentum | | | | | 1.00 | 0.06 | 0.67 | 0.63 |
| semis_disp | | | | | | 1.00 | −0.21 | −0.31 |
| funding | | | | | | | 1.00 | 0.85 |
| real_rate | | | | | | | | 1.00 |

Why the scenarios cluster:
- **The core book is directional.** Equity-driven scenarios all land on the long AI-beta vector.
- **Σ propagation pulls rate and credit scenarios onto the same vector.** Their drivers propagate
  into equity sell-offs, so `rate_capital` and `funding_basis_squeeze` lose mainly on equities.
  Their intended mechanisms (duration, swap-spread and CDS-bond basis) are a small part of the
  result; the credit sleeve *gains* $1.3M in `funding_basis_squeeze`.

---

## 3. Severity

### 3.1 Worst observed 10-day moves (2018-01 to 2026-10, adjusted closes)

| NVDA | TSM | AVGO | MU | XLK | SMH | VST | CEG |
|---|---|---|---|---|---|---|---|
| −32.3% | −22.3% | −41.2% | −37.3% | −22.4% | −27.2% | −39.6% | −32.9% |

### 3.2 How NVDA's mild-tier move is built today (`ai_bust`)

| Step | Factor | Running NVDA move |
|---|---|---|
| Authored seed | — | −35.0% |
| XbrlRag Revenue calibration | ×1.23 | −43.0% |
| Throughput (state) | ×0.85 | −36.6% |
| News (8-K / GDELT) | ×1.22 | −44.6% |
| Crowding (days-to-cover pct 1.00) | ×1.30 | −58.0% |
| + systematic co-move from the XLK driver | ≈ −10.5pp | **−68.5%** |

Four defects show in this chain:

- **The authored total move is counted twice.** The authored *total* move (−35%) becomes the
  XbrlRag *idiosyncratic* seed, and the systematic move is then added on top.
- **The systematic leg is understated in calm regimes.**
  - The run's Σ shrinkage was 0.62, because COR3M implied correlation was at its 11th percentile
    and the regime switch chose "idiosyncratic".
  - Shrinkage scales cross-covariances by 0.38, so NVDA's beta to XLK (about 1.0 over 250 days,
    1.5 since 2018) falls to about 0.38. That gives −25.5% × 0.38 ≈ −9.7pp, plus the other drivers.
  - The company-leg multipliers partly make up for this.
- **The multipliers are theme-agnostic and unchecked.**
  - The same news × crowding factor (×1.59) applies to NVDA in every scenario.
  - Nothing checks the compounded total against history: the equity hard bound is 96%, and the
    n-σ and option-implied checks only flag.
- **The tier multipliers (1.0 / 1.5 / 2.0) aren't tied to probability or history.**

### 3.3 Severity rule

Each tier is defined by **return period** at the scenario's horizon and anchored to an observable.
The enrichment sources move a scenario only **within** a band around the anchor, never through it.

| Tier | Definition | Anchor |
|---|---|---|
| mild | about a once-in-ten-years move | ~10-year return-period move at the horizon, from the longest history per factor (with today's data, about the worst move since 2018) |
| adverse | the worst move on record | the record over all available history, including dot-com (2000–02) and 2008 analogs from the long-history ingest |
| severe | a hypothetical beyond history | adverse × 1.5, capped by the hard plausibility bound |

Rules:
- **Horizon follows the scenario.** The AI deflation (#1) is anchored at a multi-month horizon:
  NVDA −66%, SMH −45%, XLK −34% from the 2021-11/12 peak to the 2022-10 trough. A multi-month move
  is applied as an instantaneous shock to today's book, as a bank's global market shock is. The
  report states that this ignores option expiries, carry and rebalancing over the window.
- **Authored drivers sit at the anchors.**
- **State multipliers are floored at 1.0** in reported tiers. Values below 1 are recorded as a
  "current conditions" view only.
- **The severe tier uses crisis Σ**, so calm-regime shrinkage can't damp it.
- **Compounded company-leg multipliers are capped** at a product of 1.5.
- **The seed is the idiosyncratic residual**, not the authored total move.
- **Long-history ingest** supplies the adverse anchors, mapped onto factors as proxies with each
  mapping written down:
  - NASDAQ Composite (FRED `NASDAQCOM`);
  - S&P 500;
  - the Philadelphia semiconductor index;
  - full-length FRED Treasury yields;
  - full-length ICE BofA OAS series.

---

## 4. The scenario set

Selection criteria:
- high loss on *this* book;
- a distinct P&L pattern after removing the book's AI-beta direction (§7.3);
- a real-world anchor;
- at least two enrichment sources with a defined job (§5.1).

### 4.1 Replay and enriched versions

Every scenario anchored to an episode runs in two versions:

- **Replay (baseline):** the observed episode moves, applied as they happened. No enrichment
  source plays a part, so this is the "no intelligence" reference.
- **Enriched:** the same narrative and episode, re-derived for today:
  - MacroRAG sizes the macro legs at the scenario horizon;
  - XbrlRag sets each name's exposure from current filings;
  - market intelligence sets severity and positioning from current state;
  - key events mark whether the scenario is live.

The gap between the two versions is what the intelligence contributes. The layer ablation (§7.1)
breaks that gap down by source.

One-off company moves inside an episode window are labelled idiosyncratic and kept out of the
systematic replay. For example, INTC's −42% in the yen-carry window was mostly its own earnings
miss (reported 1 Aug 2024), not the carry unwind.

### 4.2 The ten scenarios

Linear P&L covers stock and FX legs only (§10); full revaluation adds options, bonds, rates and
credit.

| # | Scenario | Real-world anchor | Linear P&L ($M) | What it tests |
|---|---|---|---|---|
| 1 | **AI deflation, multi-month** (replaces `ai_bust`) | 2022: NVDA −66%, SMH −45%, XLK −34% peak to trough; dot-com 2000–02 and 2018-Q4 (NVDA −56%) as adverse analogs | — | The thesis unwinding over months across the whole AI chain |
| 2 | **AI efficiency shock** | DeepSeek, 27 Jan 2025: VST −28%, CEG −21%, AVGO −17%, NVDA −17%; META +2%, AAPL +3% | −66 (one day) | Power "diversifiers" losing more than chips; platforms flat |
| 3 | **Taiwan / export-control escalation** (reworks `taiwan_geopolitical`) | BIS export rules; GPR-TWN spikes | — | TSM/TWD hedge, foundry concentration |
| 4 | **Tariff / trade-war shock** | 2–8 Apr 2025: SPX −12%, VIX 22→52, HY +115bp, 10Y +6bp, 30Y +17bp, GLD −4.5% | −72 | Equities, Treasuries and gold falling together; USD weaker, JPY stronger |
| 5 | **Crowded unwind / yen carry** (absorbs `momentum_crash`) | 10 Jul–5 Aug 2024: USD/JPY −11%, VIX 13→39, semis −25% to −40% | −127 | JPY forward, crowding, the INTC/AMD pair |
| 6 | **Inflation and real-rate repricing** (reworks `real_rate_repricing`) | Aug–Oct 2022: 2Y +123bp, 10Y +118bp, NVDA −41%, SMH −29%, GLD −7.5% | −96 | Bonds, equities and gold down together |
| 7 | **Fiscal / term-premium bear steepener** (reworks `rate_capital`; takes the swap-spread leg) | 2023-Q3 term-premium rise | — | Long-end bonds, payer swaption, swap-spread basis |
| 8 | **AI-capex credit repricing** (takes the CDS-bond leg) | Debt-funded hyperscaler capex; calibrated from DTCC single-name history and IG/HY OAS | — | ORCL/META 2053–54 bonds ($226M), CDX protection sold, issuer differentiation |
| 9 | **AI-to-broad-market rotation** (absorbs `semis_dispersion`) | 10–24 Jul 2024: SMH −14%, QQQ −8%, SPX −3.7% | −70 | Index hedges barely paying while AI longs and power fall |
| 10 | **Largest-name event** (reworks `keyman_nvda`) | Earnings or guidance miss; 8-K items 5.02 / 4.02 / 2.02 | — | Concentration, sized by the option-implied event move |

**Merge rule.** If #6 and #7 exceed 0.85 cosine on beta-removed P&L (§7.3), they merge. The freed
slot goes to a **power/grid regulatory shock** that isolates the CEG/VST sleeve.

**Sensitivity library.** These run on demand, outside the ten:
- momentum factor −12%;
- SMH-vs-basket basis;
- swap-spread basis;
- CDS-bond basis;
- AI melt-up (a squeeze on the SMH and AMD shorts and the QQQ short call).

`momentum_crash`, `semis_dispersion` and `funding_basis_squeeze` retire into this library.

### 4.3 Engine capabilities the set needs

| Capability | Needed by |
|---|---|
| `historical_replay` method: observed factor moves applied directly over an episode window, Σ filling only factors without history, replay and enriched versions, idiosyncratic labels, multi-month horizons | 1, 2, 4, 5, 6, 9 |
| Issuer-level credit factors (ORCL, META, AAPL, MSFT, AMZN; CDX). Today `shift_credit` moves every issuer by the same bp. | 8 |
| Per-name equity vol for option underliers. Today `eqvol` is a single factor. | 2, 10 |
| Return-period tiers and the multiplier cap (§3.3) | all |

---

## 5. The four sources: roles, coverage and gaps

### 5.1 Roles

| Source | Job | Never |
|---|---|---|
| **MacroRAG** | Size the macro legs from cited research and one coherent analog episode, at the scenario horizon | Set single-name equity moves |
| **XbrlRag** | Set the cross-section: which names are exposed to the theme and how much, from disclosed facts | Set overall severity |
| **Market intelligence** | Check severity against option tails; set state (GPR / EPU / throughput / implied correlation / EIA power demand); amplify by positioning (crowding) | Shrink a stress below its anchor |
| **Key events** | Mark which scenarios are live (triggers); amplify the names the events concern, for themes they concern | Apply a theme-agnostic multiplier |

**What each source does per scenario.** Columns are MacroRAG (M), XbrlRag (X), market
intelligence (MI) and key events (K).

| # | M: macro legs and episode | X: exposure | MI: severity, state, positioning | K: triggers |
|---|---|---|---|---|
| 1 | Rates, credit, VIX at a multi-month horizon from 2022 and dot-com analogs | Data-center revenue share (NVDA, AVGO, MU, TSM); capex intensity (GOOGL, META, MSFT, AMZN) | Throughput, EIA power demand, crowding, COR3M, option tails | 2.02 items with capex-guidance language |
| 2 | Episode card `market_episode_deepseek` | Contracted data-center load (CEG, VST); compute vs platform split | EIA power demand for CEG/VST; option tails; crowding | GDELT salience on model-efficiency news |
| 3 | USD/TWD, USD/JPY, credit, UST | Taiwan supply concentration (TSM 20-F, NVDA, AMD, AVGO) | GPR-TWN, COR3M | Export-control 8-Ks; GDELT on BIS/Taiwan |
| 4 | Episode card `market_episode_tariff`: UST, HY, VIX, USD/JPY | China revenue and tariff-exposed supply chains (AAPL, TSLA, NVDA, AMD) | EPU as the state multiplier | Trade-policy news salience |
| 5 | USD/JPY, VIX, front-end rates from the Aug-2024 episode | — (positioning-led) | Crowding sets the cross-section; COR3M; VIX term structure | Earnings 8-Ks as single-name amplifiers |
| 6 | 2022 episode: 2Y/10Y, real yields (DFII10), breakevens, gold | Long-duration growth exposure | MOVE / implied-vol regime | FOMC / CPI dates |
| 7 | Term premium (ACMTP10), 10Y/30Y, swap spread | Long-dated debt issuers (ORCL, META maturities) | MOVE, swaption-vol regime | Treasury refunding news |
| 8 | IG/HY OAS, credit episode | Debt, capex, free cash flow, maturities (ORCL, META, AMZN, MSFT, AAPL) | Options `credit_implied` (Merton spreads); CDS-bond basis | Debt-issuance 8-Ks (item 2.03) |
| 9 | Rotation episode: small-cap vs NDX, rates | Earnings-revision breadth | COR3M / DSPX dispersion regime | — |
| 10 | Spillover macro | Key-person and concentration risk factors | Option-implied event move sizes the shock | 5.02, 4.02, 2.02 items as triggers |

Every source has a job in at least six of the ten scenarios, and every scenario uses at least three
sources.

### 5.2 Coverage of the book's equity names today

| Name (position) | XbrlRag corpus | Used by a scenario | 8-K | GDELT | Crowding | Options chain |
|---|---|---|---|---|---|---|
| NVDA (long $92M) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| TSM (long $76M) | ✓ | ✗ | ✗ (files 6-K) | ✗ | ✓ | ✓ |
| AVGO (long $69M) | ✓ | ✗ | ✓ | ✓ | ✓ | ✓ |
| MU (long $49M) | ✓ | ✗ | ✓ | ✗ | ✓ | ✓ |
| XLK (long $113M) | n/a | n/a | n/a | n/a | ✓ | ✓ |
| SMH (short $113M) | n/a | n/a | n/a | n/a | ✓ | ✓ |
| GOOGL (long $91M) | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ (VXGOG index only) |
| CEG (long $41M) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| VST (long $55M) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| INTC (long $64M) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| AMD (short $40M) | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ |
| GLD (long $62M) | n/a | n/a | n/a | n/a | ✗ | ✗ |

Of $864M gross single-name/ETF equity, only NVDA ($92M) gets all four sources. CEG, VST and INTC
($159M) get none.

Signals already on disk that partly fill the gap:
- the `VXGOG` implied-vol index for GOOGL (2022+);
- `eia_power_demand.csv` for the power sleeve (2019+; CISO/ERCO/PJM/MISO).

### 5.3 Gaps per source, and the fix

| Source | Gap | Fix (§9 step) |
|---|---|---|
| MacroRAG | Used by 4 of 8 scenarios | Every scenario in §4 calls it (5) |
| MacroRAG | Mixes analog episodes within one scenario | One coherent episode per scenario (5) |
| MacroRAG | No guard against chunks dated after `as_of` | As-of filter in MacroRAG (14) |
| MacroRAG | Real-yield, breakeven, term-premium and gold fields never requested | Requested for #6 and #7 (5) |
| XbrlRag | 12 companies covered, 2 used, 1 fact calibration | Company legs for every covered name per theme, with theme concepts: data-center share, customer concentration, capex/revenue, debt maturities, contracted power (8) |
| XbrlRag | Authored total move used as the idiosyncratic seed | Seed is the idiosyncratic residual (4) |
| XbrlRag | No CEG, VST or INTC in the corpus | Added (7) |
| Market intelligence (options) | `OptionSignalProvider.severity_multiplier` never called | Wired or removed (10) |
| Market intelligence (options) | Option-implied checks flag with no consequence | Option tail used as a severity check (10) |
| Market intelligence (options) | `credit_implied.csv` unused | Used for #8 (10) |
| Market intelligence (options) | `option_signals.csv` is snapshot-only | Vol indices used where chains are missing (10) |
| Market intelligence (crowding) | No AMD, INTC, CEG, VST, GOOGL | Ingest (7) |
| Market intelligence (crowding) | Percentile over own history since 2023 at twice-monthly cadence | Reliability checked when applied to all equity legs (9) |
| Market intelligence (state) | GPR and throughput can damp to ×0.85; EPU unused | Floor at 1.0 (4); EPU drives #4 (5) |
| Market intelligence (state) | Crisis Σ windows configured but unused | Crisis Σ for the severe tier (12) |
| Key events | 8-K covers 9 tickers; GDELT 6 names since 2026-04 | 8-K for GOOGL, INTC, AMD; TSM 6-K (7) |
| Key events | News ignored unless the name has an XbrlRag leg | Applied to all equity legs (9) |
| Key events | Multiplier is theme-agnostic | Theme-relevant only (9) |
| Key events | Only item 5.02 triggers | Item → scenario map: 2.02 → #1, 2.03 → #8, 5.02/4.02 → #10 (9) |

---

## 6. Measurement gaps

| ID | Gap | Why it matters |
|---|---|---|
| G1 | No layer attribution | Can't say what each source added |
| G2 | Severity not anchored (§3) | A mild tier beyond history hides real differences between layers |
| G3 | No set-level coverage measure | Nothing reports whether the set covers the book's main loss patterns |
| G4 | No out-of-sample validation | The only evidence of *value* rather than influence |
| G5 | Σ from a calm, short regime (EWMA λ=0.94 ≈ 16-day memory; shrinkage up to 0.62) | Systematic co-moves understated |
| G6 | Thin source coverage (§5.2) | Intelligence reaches 10–20% of the book |
| G7 | Options don't shape shocks | The most forward-looking data is used only for flags |
| G8 | One credit factor, one equity-vol factor | No issuer or name differentiation |
| G9 | No repeat-run stability measure | Can't separate signal from synthesis noise |
| G10 | No signal-health view (coverage, staleness, gate holds) | Silent degradation |
| G11 | No cost or latency per layer against its contribution | Value per dollar unknown |
| G12 | Inconsistent horizons (5d / 10d; liquidity horizons to 60d) | Cross-scenario P&L needs a horizon note |
| G13 | Nothing shows whether the scenario set changes a decision | The most convincing proof for a demo |
| G14 | History too short (equities 2018+, rates 2022+, GDELT 2026-04+, 8-K 2024-01+) | No dot-com or 2008 anchor; no point-in-time test for news or chain options |

---

## 7. Measurement design

### 7.1 Layer ablation (G1)

Spec: `docs/specs/scenario-layer-ablation.md` (33 locked tests).

**Layers**, each measured against the authored scenario:

| Layer | Source |
|---|---|
| `macrorag` | MacroRAG |
| `xbrlrag_narrative` | XbrlRag 10-K/Q risk factors |
| `xbrlrag_facts` | XbrlRag XBRL fact calibration |
| `news` | Key events |
| `crowding` | Market intelligence |
| `state` | Market intelligence (GPR, throughput) |

**Method:** each service is resolved once per scenario. Every layer subset is then rebuilt from
those cached results and fully revalued, so differences between subsets aren't run-to-run noise.

**Outputs**, per scenario × tier, at the mild tier by default:
- **Ladder:** total P&L per prefix, marginal P&L, gross reallocation (Σ|Δ per-trade P&L|) and
  Spearman rank correlation against the previous step.
- **Exact Shapley values** per layer, which remove the order dependence of the ladder.
- **Floor hits:** per layer subset, the factors at the −95% return floor. Their contributions are
  understated.
- **Per-subset per-trade P&L, and provenance:** snapshot ids, `cov_id`, world view, resolutions,
  and the composition-rule version.

**Reading rules:**
- **The ablation measures influence, not value.** A layer that enlarges a loss isn't better for
  that reason. Value comes from §7.5 and §7.4.
- **Results are compared only within one composition-rule version.** Step 9 of §9 changes how news
  and crowding apply, and bumps the version.

**Demo view:** a per-scenario waterfall (authored → + MacroRAG → + XbrlRag → + key events → + market
intelligence → final), with the Shapley bar beside it.

### 7.2 Severity report (G2)

Per scenario × tier, each factor's move against:
- its return-period anchor;
- its option-implied tail probability;
- the compounded multiplier chain behind it.

### 7.3 Set coverage and diversity report (G3)

1. **Coverage (primary).** For each top principal component of the book's historical
   full-revaluation VaR P&L, the loss-weighted share spanned by the scenario set. It answers: is
   there a way this book loses money that no scenario represents?
2. **Diversity (secondary).** Remove the book's first principal loss direction, weight rows by loss,
   and compute the participation ratio of the remainder. Target: 3–4 for ten scenarios.
3. **Pairwise cosine** on beta-removed vectors. Pairs above 0.85 merge (§4.2).

### 7.4 Point-in-time event replay (G4, partial)

Re-run the engine as of the day before each episode (2024-07-10, 2025-01-24, 2025-04-02), with
sources pinned to that date where their history allows:

| Source | Point-in-time history | Usable for these episodes |
|---|---|---|
| MacroRAG | dated corpus | after the as-of filter (step 14) |
| XbrlRag | filings by fiscal year | yes |
| Crowding (FINRA) | 2023-01+ | yes |
| GPR / EPU / throughput / EIA | long / long / 2018+ / 2019+ | yes |
| CBOE vol indices (VIX, VXN, COR3M, SKEW, VXGOG, VXAZN, VXAPL) | 2022-01+ | yes; stands in for options |
| Option chains and signals | recent snapshots | no |
| 8-K | 2024-01+, 9 tickers | partly |
| GDELT | 2026-04+ | no |
| Treasury rates | 2022+ | yes |

Scores:
- name-ranking hit rate (Spearman correlation of predicted vs realised per-name moves);
- direction accuracy by sleeve;
- distance from the nearest scenario to the realised move vector.

Each score compares the replay baseline, the authored-only scenario and the enriched stack. News
and chain-based options are evaluated by §7.5 instead.

### 7.5 Forward scoring log (G4)

From the first run after delivery, every run stores the predicted per-name and per-factor moves for
each scenario, both the enriched version and its layer subsets. As time passes, the moves are
scored against realised moves at each scenario's horizon, with the cases where a trigger fired
(8-K item, GPR spike, crowding extreme) reported separately. This is the evidence base for the news
and options layers.

### 7.6 Decision impact (G13)

For each scenario set (replay baseline, authored, fully enriched), the scenario losses enter the
sizing module (`src/pricer/sizing`) as a stress-loss constraint next to the VaR budget. The report
shows the changes in position sizes, binding hedges and limit breaches with and without each
source.

### 7.7 Stability (G9)

The dual-RAG resolution runs three times at the same `as_of`. The report shows the spread of each
MacroRAG and XbrlRag leg and the resulting P&L range.

---

## 8. Settled parameters

| Parameter | Value |
|---|---|
| Tier definitions | mild ≈ 10-year return period; adverse = record incl. long-history analogs; severe = adverse × 1.5, capped by the hard bound |
| State-multiplier floor (reported tiers) | 1.0 |
| Compounded company-leg multiplier cap | 1.5 |
| Severe-tier Σ | crisis windows of the full aligned history (2022 only until the history reaches back further), shrinkage 0.2, at least 60 days; fail-loud below |
| Production Σ shrinkage | the COR3M regime's value, capped at 0.3 |
| Replay tier scale | capped at the largest scale that keeps every factor inside its hard plausibility bound |
| Merge threshold (beta-removed cosine) | 0.85 |
| Diversity target (beta-removed, loss-weighted participation ratio) | 3–4 |
| Default ablation tier | mild |
| Data sources | free only |

Golden PVs are unaffected. Scenario results and scenario limits change: mild losses fall where they
exceed their anchors, and the multi-month deflation raises adverse and severe losses.

---

## 9. Delivery plan

Each step is one test-first change (`spec/<id>` → `impl/<id>`) unless marked as an ingest.

| Step | Change | Closes | Depends on |
|---|---|---|---|
| 1 | `scenario-layer-ablation`: composition, ladder, Shapley, runner with floor hits | G1 | — |
| 2 | `scenario-forward-log`: store predictions; score as realised data arrives | G4 | 1 |
| 3 | Long-history ingest: NASDAQ Composite, S&P 500, semiconductor index, full FRED Treasury and OAS series, with factor-proxy mappings | G14 | — |
| 4 | `scenario-severity-anchor`: return-period tiers, state floor, multiplier cap, residual seed, authored drivers at the anchors, severity report | G2 | 1, 3 |
| 5 | `scenario-historical-replay`: replay method with replay and enriched versions, idiosyncratic labels, multi-month horizon; scenarios #1–#7 and #9; retire three scenarios to the sensitivity library | §4 | 4 |
| 6 | `scenario-set-coverage`: coverage and diversity report | G3 | 5 |
| 7 | Coverage ingest: 8-K for GOOGL, INTC, AMD; TSM 6-K; crowding and option chains for GOOGL, CEG, VST, INTC, AMD; XbrlRag corpus for CEG, VST, INTC | G6 | — |
| 8 | `xbrlrag-cross-section`: company legs for every covered name per theme | G6 | 7 |
| 9 | `event-scenario-mapping`: item → scenario triggers; theme-relevant news and crowding on all equity legs (new composition-rule version) | G6 | 7 |
| 10 | `options-shock-sizing`: scenario #10 sized by the option-implied event move, option-tail severity check, vol-index fallback, `credit_implied`, `severity_multiplier` wired or removed | G7 | 4 |
| 11 | `issuer-credit-factors` and scenario #8 | G8 | 5 |
| 12 | `crisis-sigma-tiers`: crisis Σ for the severe tier; shrinkage floor | G5 | 4 |
| 13 | `scenario-decision-impact`: scenario losses as a sizing constraint, with and without each source | G13 | 1, 5 |
| 14 | MacroRAG as-of filter (MacroRAG repo), then `scenario-event-replay` | G4 | 1, 5 |
| 15 | `scenario-stability`, signal-health and cost-per-layer reporting; BFF and UI waterfall | G9–G11 | 1 |

Retiring `momentum_crash`, `semis_dispersion` and `funding_basis_squeeze` (step 5) is done through
`authoring.py apply`, with a signoff entry for each removed scenario version.

---

## 10. Appendix: method and data notes

- **Similarity and participation ratio:** rows are the eight mild-tier per-trade P&L vectors, each
  normalised to unit length. Participation ratio = (Σλ)² / Σλ², with λ the squared singular values
  (shares 0.68, 0.16, 0.09, 0.04, 0.02, …; ratio 2.0). Unit normalisation weights every scenario
  equally regardless of loss, which is why §7.3 uses loss-weighted, beta-removed measures.
- **Worst 10-day moves:** 10-trading-day percentage change of adjusted closes, 2018-01-02 to
  2026-10-06, from `data/marketdata/equity/prices_adj_close.csv`.
- **Linear replays:** stock positions (signed notional × episode return) plus the USD/JPY forward
  (notional × USD/JPY return), from on-disk prices and Fed H.10 FX.
  - They exclude options, bonds, rates, credit, the TWD swap, convexity and carry.
  - Horizons differ by episode (1 day to 2 months), so treat them as relative sizing.
  - SPX puts and the QQQ collar would offset part of the tariff, yen-carry and 2022 losses, and
    little of the DeepSeek and rotation losses.
- **Episode windows:**

  | Episode | Window |
  |---|---|
  | DeepSeek | 2025-01-24 → 2025-01-27 |
  | Tariff | 2025-04-02 → 2025-04-08 |
  | Yen carry | 2024-07-10 → 2024-08-05 |
  | Rotation | 2024-07-10 → 2024-07-24 |
  | 2022 | 2022-08-16 → 2022-10-14 |

  Rates, spreads and VIX come from `fred_rates.csv` and `fred_stress_macro.csv`.
- **Multi-month drawdowns:**

  | Ticker | Peak | Trough | Drawdown |
  |---|---|---|---|
  | NVDA | 2021-11-29 | 2022-10-14 | −66.3% |
  | SMH | 2021-12-27 | 2022-10-14 | −45.3% |
  | XLK | 2021-12-27 | 2022-10-12 | −33.6% |

- **Run Σ:** `ewma:0.94+shrink:0.623485` (`cov_id` `b6d4e299314a5b54`; COR3M 11.72, 11th
  percentile). Raw NVDA~XLK daily beta is 1.03 over 250 days and 1.51 since 2018.
- **DTCC single-name CDS history** (input to #8):
  - `dtcc_single_name_cds.csv` holds 3,694 records from 2025-05-01 to 2026-10-08.
  - Only 845 carry `Spread-Leg 1` (688 ORCL, 157 META); the rest quote a fixed coupon and upfront
    price. The CDS adapter reads `Spread-Leg 1` only, so step 11 adds an upfront-to-spread
    conversion and reference-name normalisation (names vary, e.g. `ORACLE CORPORATION`,
    `Oracle Corp`).
  - About 17 months of history gives levels and recent episodes, so #8's adverse anchor comes from
    IG/HY OAS history.
