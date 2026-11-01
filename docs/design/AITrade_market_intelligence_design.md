# AITrade ScenarioGen — Market-Intelligence Enrichment: Feasibility & Design

**Status:** **DELIVERED — P0–P7 complete (2026-07-24 → 2026-07-26).** Every phase is built, tested
(ruff + mypy + pytest + DQ gate + governance green on each), and on `main`; the one exception is **B5**,
correctly closed as *not free-buildable* (Alpha Vantage `HISTORICAL_OPTIONS` is a premium endpoint —
§8.1/§11.14) with its forward-accumulation fallback already running. The **as-built record + financial
impact is §0.1 below** (single source of truth); the per-phase detail is §11.8–§11.14. §1–§10 are the
**original analysis / rationale** (dated 2026-07-23), preserved as the "why"; where a build corrected an
analysis assumption it is called out at the point of use (e.g. FINDING 10, §11.8; GPR country cadence,
§11.14). **Golden PVs were untouched throughout (30/30 every commit): this program is risk/scenario
tooling, not a pricing change.**
**Date:** analysis 2026-07-23 · delivery 2026-07-24 → 2026-07-26.
**Scope:** extend the stress-scenario inputs beyond the two live RAG services (MacroRAG macro
narrative, ARag SEC 10-K/Q fundamentals) with (A) company **news/events**, (B) **options-market**
intelligence, and (C) ten further **factor classes** a state-of-the-art bank/HF stress module
carries that this one does not.
**Hard constraint:** **100% free data.** No paid tier, no trial, no non-commercial-only licence.
**Modules touched (as built):** `src/marketdata/` (new `news/`, `crowding/`; extended `options/`,
`rates/`, `stress/`) · `src/scenariogen/` (new `news/`, `options/`, `geopolitical`/`throughput`/`crowding`
/`liquidity`/`collateral`/`implied_corr` providers) · `src/pricer/risk/` (F6 drivers, F3 bumps) ·
`ui/bff/` + `ui/app/` (Market Signals + News surfaces).
**Placement:** everything in this repo — **single-project, no cross-repo work**. See **§9**.
**Dual-mode:** every artifact must run unchanged under `PRICER_DEPLOY_MODE` `local` **and** `aws`.
All `data/…` paths below are **logical locations** resolved by `storage.data_path()`. See **§10**.

> Companions: [AITrade_stresstest_scenario_def_design.md](AITrade_stresstest_scenario_def_design.md)
> (§4 input classes, §5 methodology, §6 scenario catalog) ·
> [scenariogen_named_scenario_dualrag_design.md](scenariogen_named_scenario_dualrag_design.md)
> (the additive systematic+idiosyncratic seam this work plugs into) ·
> [AITrade_quantlib_portfolio_marketdata.md](AITrade_quantlib_portfolio_marketdata.md)
> (§5 consumption audit) · [AITrade_risk_data_gap_analysis.md](AITrade_risk_data_gap_analysis.md)
> (GAP 4/5/6 residuals, several of which this work reopens as fillable).

---

## 0. Executive summary

Three things came out of the analysis that change the shape of the work:

1. **Much of the raw data is already on disk and simply not consumed.** Option chains carry
   `bid`/`ask`/`open_interest`/`volume`/`delta`/`gamma`/`theta`/`vega`/`iv` per contract; only
   `iv` is read (for the pricing surface). `prices_daily.csv` carries `volume` (10y). `SOFR` and
   `IORB` are both already columns of `fred_rates.csv`. The Treasury−SOFR swap spread is already
   computed inside `bonds_adapter`. None of these are risk factors or scenario inputs.
2. **[GAP 4](AITrade_risk_data_gap_analysis.md)'s residual is mis-diagnosed as a sourcing gap.**
   It records per-name equity vol for NVDA/MSFT/META/TSLA as blocked because CBOE publishes no
   per-name index (confirmed — `VXNVDA`/`VXTSLA` return 403). But their **full option chains, with
   IV on every contract, are already downloaded weekly**. The missing piece is *history*, not
   *level*. See §5 FINDING 10 and §3.
3. **The framework is a complete *marks* model and has no *liquidity*, *funding*, *basis*, or
   *style* dimension at all.** All 39 configured VaR factors are prices or levels. §5 covers this.

Everything proposed is free. The only new credential anywhere is an optional free Alpha Vantage
key (§3.4), and one already-supported free EIA key (§5 FINDING 9).

---

## 0.1 As-built record + financial / risk impact *(the single source of truth)*

Delivered 2026-07-24 → 2026-07-26, on `main`. **This is risk/scenario tooling: golden PVs were
untouched on every commit (30/30), so there is no *pricing* impact — the financial impact is on the
*stress / scenario* numbers.** Live figures are the **aggressive** book at valuation date 2026-07-10 —
the program's then-default; the active default has since moved to the **core** 38-trade book
([`portfolio_rebalance_review.md`](portfolio_rebalance_review.md)), so the numbers below are the
as-measured aggressive-book figures, not the current default.
Every calibration is **clamped** and **point-in-time** (a re-run reproduces exactly); each is
disabled-reversible and stamped into the scenario report for audit.

| Phase / feature | What it does (mode) | Financial / risk impact — live 2026-07-10 | Where it shows |
|---|---|---|---|
| **P0** F10 de-alias | Collapse 5 collinear `EQVOL_*` sensitivity factors → 1; QQQ→VXN | Sensitivity-VaR factor set 45→41; headline VaR moved ≤ +0.1% (QQQ→VXN only) | `pricer_config.json` `maps_to` |
| **P1** B1–B4 option signals | Per-name IV/term/skew/VRP, RND tail probs, Merton/CreditGrades, market-implied plausibility check | New capability: grade a shock vs the **market-implied** tail prob (e.g. RND P(NVDA ≤ −30%)); ORCL DD 2.5 ≪ mega-caps 8–15 | `option_signals.csv`, plausibility `market_implied` |
| **P2** F3 basis | ETF-dispersion Σ driver + swap-spread / CDS-bond repricing bumps | `semis_dispersion`: MU −10.6% vs SMH +0.8% (the hedge failing, netted flat before); swap-spread +30bp → USTs −$4.3M/−$1.1M; CDS-bond +60bp → ORCL/META CDS +$0.62M/+$0.66M | scenarios `semis_dispersion`, `funding_basis_squeeze` |
| **P2** F4 style | Ken French `style:MOM` Σ driver | `momentum_crash`: MU −30%, SMH −13%, NVDA −6% vs SPX −3%, MSFT +4% (rotation) | scenario `momentum_crash` |
| **P4** F1 liquidity horizon + bid-ask | √(LH/10) scaler on **losses** + spread haircut → exit value (mode-iii) | taiwan severe: mid P&L **−$173.5M → exit −$193.5M** (−$20.0M liquidity drag; TWD swap #26 @ 60d) | `ScenarioStressResult.liquidity` |
| **P4** F2 cash / collateral line | VM (−Σ margined P&L) + IM proxy → net cash outflow (mode-iii) | taiwan severe: **net cash −$85.6M (a *receive*)** — opposite sign to the −$173.5M mark (SMH short posts +$76M VM as semis crash) | `…collateral` |
| **P5** F6 real/breakeven/term-premium | 3 driver-only Σ factors (nominal → real vs breakeven) (mode-i) | `real_rate_repricing`: ir:10Y +52bp **and** equity −$69M **and** bonds −$35M *together* — the 2022 co-move a nominal-only model can't express | scenario `real_rate_repricing` |
| **P5** F7 implied correlation | COR3M percentile → Ledoit-Wolf shrinkage regime (mode-i, **0 Σ dims**) | COR3M @ 3.6th pct → **idiosyncratic → shrinkage 0.64** (damps cross-name propagation in a low-corr market) | `covariance.regime` |
| **P6** B6 + A5 | Market Signals + News UI surfaces (BFF `/options/signals/*`, `/news/*`) | Visibility only (no P&L): RND/skew/implied-credit + 8-K stream & keyman-trigger banner | `ui/` |
| **P3** A0–A4 news | 8-K item codes + GDELT tone → clamped ×[0.8,1.5] on ARag idiosyncratic leg; 5.02 = keyman trigger (mode-ii) | NVDA idiosyncratic ×**1.226** (5 events incl. 5.02 + tone); **`keyman_nvda` fires live** for NVDA/MU/ORCL/MSFT/META/AAPL | dual-RAG `…news` |
| **P7** F8 geopolitical (GPR) | GPR percentile → clamped scenario severity multiplier + live trigger (mode-ii) | **`taiwan_geopolitical` ×1.32, trigger LIVE** (GPRC_TWN @ 91st pct); severe −$173.5M → **−$199.8M** | `…geopolitical` |
| **P7** F9 throughput | Semiconductor IP YoY percentile → severity multiplier on `ai_bust` (**inverse** of F8) | IPG3344S YoY +11.5% @ 80th pct → thesis *intact* → **×0.85 damp** (a bust is less likely when throughput booms) | `…throughput` |
| **P7** F5 crowding | FINRA days-to-cover percentile → per-name multiplier, **composed with news**; #6 SMH crowded-short flag (mode-ii) | NVDA/TSLA idiosyncratic ×**1.30** (short interest at own highs); MU ×0.85; SMH squeeze mechanism armed | dual-RAG `…crowding` |
| **B5** AV backfill | *(not built)* | — | closed: premium endpoint, §11.14 |

Two calibration shapes were established and reused: **(a) external index → scenario severity
multiplier** on a tagged scenario (F8 high-amplifies, F9 low-amplifies, via
`catalog.materialize(severity_multiplier=)`); **(b) per-name → idiosyncratic-leg multiplier** composed
in the dual-RAG merge (`idiosyncratic = ARag × news × crowding`). Today the two thesis scenarios move in
**opposite, each-correct** directions: taiwan **amplified** (geopolitics elevated), ai_bust **damped**
(AI capex physically confirmed). Trade-level treatment (which trade carries which horizon / margin /
basis / crowding / scenario tag) is in each trade's *Risk & stress treatment* section under
[docs/pricing/trades/](../pricing/trades/).

---

## 1. Verified free-source inventory

Probed live on 2026-07-23 from the dev machine. `200` = fetched successfully.

### 1.1 Confirmed available

| Source | Probe | Result | Auth |
|---|---|---|---|
| SEC `data.sec.gov/submissions/CIK….json` | NVDA | **200** — 8 recent 8-Ks with **item codes** (`5.02`,`5.07`,`2.02`,`8.01`) | UA header w/ email |
| SEC `efts.sec.gov` full-text search | keyword + `forms=4` | **200** | UA header |
| GDELT 2.0 Doc API | `NVIDIA` | **200** — articles + tone + seendate | none |
| Yahoo per-ticker RSS | `?s=NVDA` | **200** — real headlines | UA header |
| Nasdaq RSS | `?symbol=NVDA` | **200** | UA header |
| CBOE vol indices | `VXAPL VXN VXTLT VXEEM VXGS OIV VXD RVX` | **200** each | none |
| CBOE **dispersion / implied correlation** | `DSPX COR1M COR3M` | **200** each | none |
| Ken French Data Library | FF5 daily, Momentum daily, 49-industry daily | **200** each | none |
| NY Fed **ACM term premium** | `ACMTermPremium.xls` | **200** | none |
| GPR geopolitical-risk index (Caldara-Iacoviello) | daily recent | **200** | none |
| EPU policy-uncertainty index | `All_Daily_Policy_Data.csv` | **200** | none |
| FINRA short interest | `api.finra.org` consolidated | **200** (CDN bulk path 403) | none for API |
| Alpha Vantage `HISTORICAL_OPTIONS` | IBM 2017-11-15 | **200** — full chain, **IV + all greeks**, back to 2008 | free key |

### 1.2 Confirmed *unavailable*

| Probe | Result | Consequence |
|---|---|---|
| CBOE `VXNVDA`, `VXTSLA` | **403** — do not exist | Per-name vol indices unavailable for the option book's main names → derive from chains (§3) |
| CBOE `TOTALPC_History.csv` | **403** | Aggregate put/call ratio not on the index CSV path; compute from our own chains instead |

### 1.3 Unverified — do not rely on without re-probing

| Source | Status | Note |
|---|---|---|
| FRED (`DFII10`, `T10YIE`, `T5YIFR`, `NFCI`, `IPG3344S`, …) | probes returned connection failure (`000`) **from this sandbox** | **FRED is known-good** — the repo already ingests it; `IORB` is a column of `fred_rates.csv`. Sandbox network artifact, not a source problem. Treat as available. |
| CFTC COT (`fut_fin_txt_2026.zip`) | `000` | Unverified. Re-probe before relying on it. |
| TSMC monthly revenue, SIA billings, Korea 20-day exports | not probed | Several are HTML scrapes, not clean APIs. Lowest-confidence item in this document (§5 FINDING 9). |
| Alpha Vantage `HISTORICAL_OPTIONS` free-tier or premium | **RESOLVED 2026-07-26: premium** | Tested with a real free key: returns `{"Information": "This is a premium endpoint…"}`. The original demo-key "200" was misleading (the demo key has special access). So B5's AV backfill is **not free-accessible** — precluded by the §0 free-data constraint. Fallback (forward-accumulate, §3.4 opt 1) stands. |

### 1.4 Deliberately rejected

NewsAPI, Marketaux, Finnhub free tiers — non-commercial-only licences, key management, and a
paid-upgrade cliff. Against the free-only constraint.

Yahoo / Nasdaq RSS are **display-only**: they work, but the ToS is grey for redistribution.
Show links in the UI; never derive a committed number from them.

---

## 2. Workstream A — News & Events

### 2.1 The overload problem, and the answer

The instinct is "pull a news API and summarize with an LLM." That is the wrong design here:
unbounded volume, non-deterministic classification, no citations, and it collides with the repo's
fail-loud + provenance discipline.

**The answer is a pre-classified, legally-mandated, low-volume event stream: SEC Form 8-K item
codes.** An 8-K is filed *only* when something material happens, and the filer must tag *which*
material thing from a fixed numbered taxonomy. The classification is done by the issuer under
legal liability — no LLM needed, and it is citable by accession number.

**Volume is inherently bounded: NVDA filed 8 8-Ks in ~5 months.** Across ~12 portfolio names that
is roughly 100–200 events/year. That is the manageability requirement, satisfied by construction
rather than by filtering.

8-K is also the natural complement to what ARag already does: ARag indexes 10-K/Q — the
**structural, slow-moving** disclosure. 8-K is the **event** stream over the same corpus.

### 2.2 Item-code allowlist

| Item | Meaning | Relevance to this book |
|---|---|---|
| **2.02** | Results of operations | Earnings event → pairs with option-implied move (§3.3 signal 3) |
| **5.02** | Departure of directors/officers | **Directly fires the existing `keyman_nvda` scenario** |
| **4.02** | Non-reliance on prior financials | Severe distress flag (restatement) |
| **2.06** | Material impairment | Write-down → credit deterioration |
| **1.01 / 1.02** | Entry into / termination of material agreement | Supply contracts, hyperscaler deals — the AI-capex thesis |
| **1.05** | Material cybersecurity incident | Operational tail |
| **2.03 / 2.04** | New debt obligation / triggering event | Leverage → feeds bond (#15–19) and CDS (#28–29) legs |
| **7.01 / 8.01** | Reg FD / Other | Catch-all, lower weight |

### 2.3 Source tiering

| Tier | Source | Gives | Derived numbers? |
|---|---|---|---|
| **1 — spine** | SEC EDGAR submissions JSON | Structured events: item codes, dates, accession, doc URLs | **Yes** |
| **2 — intensity** | GDELT 2.0 (`doc` + `timeline`) | Article volume + tone per entity, 15-min cadence, history to 2015 | **Yes** — `timelinevolinfo` gives a *historical* volume series, so z-scores are available immediately with no accumulation wait |
| **3 — display** | Yahoo / Nasdaq per-ticker RSS | Headlines + links for the UI | **No** (see §1.4) |

All three land in this repo's `marketdata` (§9). The 8-K use here is **structured metadata** —
item codes and dates, no NLP — which is why it does not need ARag's retrieval machinery.

### 2.4 Architecture

Ingest in `marketdata` (house convention: downloader + config + DQ gate + provenance sidecar),
consumption in `scenariogen` (mirrors `arag/` and `macro/`).

```
src/marketdata/news/                     ← NEW asset class
  runner.py
  sec_events_downloader.py               → data/marketdata/news/sec_events.csv
  gdelt_salience_downloader.py           → data/marketdata/news/gdelt_salience.csv
configs/marketdata/news/news_marketdata.json
configs/marketdata/dq_gate.json          ← +2 gated files

src/scenariogen/news/                    ← mirrors arag/ and macro/
  provider.py      NewsEventProvider.resolve(scenario) -> NewsResolution
  mapping.py       item code + salience -> per-name multiplier
  gate.py          freshness + minimum-event gate (mirrors arag/gate.py)
  tool_source.py   ToolSource adapter so the agentic llm/ path sees events too (§9)
```

**Two compact CSVs, no document store.** This is the *architectural* guard against overload: news
enters as a **numeric feature with a citation**, never as retrieved text. We do not build a third
RAG.

```
sec_events.csv:      ticker, cik, filing_date, form, item_codes, accession, doc_url, weight
gdelt_salience.csv:  ticker, date, article_volume, avg_tone, vol_z_63d, tone_z_63d
```

**Reaching the agentic path.** `llm/mcp/registry.py`'s `ToolSource` is a **Protocol**
(`list_tools` / `call_tool`), not MCP-bound — so a local CSV-backed source registers as an OpenAI
tool without running an MCP server. One small adapter (`tool_source.py`) gives the agentic
`llm/` flow the same events the deterministic dual-RAG path gets. This is the one extra
integration point the single-project placement costs (§9).

### 2.5 How it feeds a scenario

Reuse the existing calibration seam in
[`src/scenariogen/arag/calibrate.py`](../../src/scenariogen/arag/calibrate.py) — **no new merge
semantics in [`dualrag.py`](../../src/scenariogen/dualrag.py)**:

```
news_multiplier(ticker) = clamp(1 + β_event·event_score + β_tone·(−tone_z), min_scale, max_scale)

final(eq:NVDA) = systematic(macro) + idiosyncratic(ARag × news_multiplier)
```

`event_score` is a config-weighted sum over allowlisted 8-K items in a trailing window, decayed by
age. Proposed defaults `min_scale=0.8, max_scale=1.5` — **news modulates a governed baseline; it
can never invent one.** Every leg carries accession numbers and GDELT z-scores in
`driver_sources`, so the Trace Explorer shows exactly what news contributed.

Two direct wins beyond calibration:
- A `5.02` filing on NVDA/TSLA is a **live trigger** for `keyman_nvda` — the scenario stops being
  purely hypothetical.
- A `2.02` earnings date populates the scenario event calendar, which §3.3 signal 3 prices.

### 2.6 Phases

| Phase | Scope | Size |
|---|---|---|
| **A0** | Ticker→CIK map (SEC `company_tickers.json`), news config, DQ-gate entries | S | ✅ delivered 2026-07-26 |
| **A1** | `sec_events_downloader` + item-code allowlist + offline-fixture tests | M | ✅ delivered 2026-07-26 |
| **A2** | `gdelt_salience_downloader` + z-scores | S–M | ✅ delivered 2026-07-26 |
| **A3** | `scenariogen/news/` provider + gate + calibration wiring + provenance | M | ✅ delivered 2026-07-26 |
| **A4** | `news/tool_source.py` — `ToolSource` adapter so the agentic `llm/` path sees events | S | ✅ delivered 2026-07-26 |
| **A5** | BFF `/news/*` + UI panel (Tier-3 headlines dropped at the §8 ToS gate) | M | ✅ delivered 2026-07-26 (§11.13) |

All phases are in-repo; no cross-repo coordination. A4 is small but **not optional** — without it
only the deterministic dual-RAG path gets news (§9).

### 2.7 Risks

- **CIK mapping drift** — use the official SEC map file, refreshed per run; fail loud below a
  coverage floor per the `MarketDataIngestError` convention.
- **Duplicated EDGAR plumbing.** ARag already owns a CIK map and EDGAR fetch policy for 10-K/Q.
  Keeping 8-K here means a second, smaller copy. Accepted (§9) — the map is a static SEC-published
  file and the 8-K path needs no retrieval machinery — but it is real duplication, and the two
  should not be allowed to disagree on CIK↔ticker.
- **Agentic-path wiring is manual.** Unlike an ARag capability (which reaches both surfaces via
  the `capabilities.py` catalog and its drift test), a local feed reaches the agentic `llm/` path
  only through the A4 `ToolSource` adapter. Cheap, but it is an explicit step that can be
  forgotten — hence A4 is marked not-optional.
- **Salience ≠ severity** — GDELT volume spikes on good news too. Tone is signed and the
  multiplier is clamped in both directions.
- **Reflexivity** — scenario severity becomes news-dependent and so less stable run-to-run.
  Mitigate by pinning the news window to the snapshot date (PIT), so a re-run reproduces exactly.

---

## 3. Workstream B — Options-Market Intelligence

### 3.1 Why this is the higher-value half

Options are a **forward-looking, market-priced** view of exactly what scenariogen is trying to
size: the probability and severity of large moves. Today every scenario magnitude is anchored on
*historical* covariance plus narrative. Options add a market-implied anchor and — uniquely — a
**market-implied probability**, which nothing else in the repo can produce.

Most of the data is already on disk.

### 3.2 Data to download

| Item | Status | Action |
|---|---|---|
| CBOE chains: AAPL/NVDA/MSFT/META/TSLA/SPX/QQQ | ✅ have (7 files; e.g. 8.4k NVDA rows, 60.8k SPX rows) | — |
| CBOE chains: **TSM, AVGO, MU, ORCL, AMZN, XLK, SMH** | ❌ missing | **Config-only edit** to `configs/marketdata/options/options_marketdata.json` |
| CBOE vol indices VIX/VIX3M/VIX6M/VIX9D/VVIX/SKEW/VXAPL/VXAZN/VXGOG | ✅ have | — |
| CBOE **VXN, RVX, VXD, VXTLT, VXEEM, OIV** | ❌ missing (all verified 200) | Config-only. `VXN` matters — QQQ/NDX is a book underlier |
| CBOE **DSPX, COR1M, COR3M** (dispersion / implied correlation) | ❌ missing (verified 200) | Config-only; feeds §5 FINDING 7 |
| Daily spot history (realized vol) | ✅ have (10y `prices_daily.csv`) | — |
| Historical chains (backfill) | ❌ | Alpha Vantage — see §3.4, **rate limit unverified** |

Net new download volume: ~7 chain files (a few MB/snapshot) + ~9 small index CSVs. Negligible.

**Coverage note.** Options ingest currently tracks the *option book* (#7–14), not the *portfolio*.
TSM, AVGO, MU, ORCL, AMZN, XLK and SMH all have liquid US-listed options and are absent. **ORCL
and META matter most** — they are the CDS/bond issuers (#17/#18, #28/#29), where equity-option
signals are a classic credit early-warning (§3.3 signal 6).

### 3.3 The eight signals

| # | Signal | Method | Tells us | Needs history? |
|---|---|---|---|---|
| 1 | **ATM IV level, per name** | Interpolate IV at delta≈0.50 per expiry | Per-name vol level — **closes GAP 4's residual for NVDA/MSFT/META/TSLA** and fixes FINDING 10 | No |
| 2 | **IV term structure** | ATM IV vs DTE; front/back ratio | Inversion (front > back) = market pricing near-term stress | No |
| 3 | **Earnings implied move** | Straddle price at first post-earnings expiry ÷ spot | Market's own sizing of the event — **pairs with 8-K item 2.02** | No |
| 4 | **Skew / 25-delta risk reversal** | `IV(25Δ put) − IV(25Δ call)` | Crash-risk pricing per name; steepening = distress | No |
| 5 | **Risk-neutral density → tail probability** | Breeden–Litzenberger, `∂²C/∂K²` over the strike grid | **Market-implied P(NVDA ≤ −30%)** — a forward-looking plausibility check | No |
| 6 | **Option-implied credit spread** | Merton / CreditGrades from equity vol + leverage (leverage via ARag `facts_lookup`) | **Financial-health signal**; cross-check vs DTCC CDS for ORCL/META | No |
| 7 | **Variance risk premium** | ATM IV − realized vol (from `prices_daily.csv`) | Cushion vs complacency; VRP compression precedes vol spikes | No |
| 8 | **Positioning: put/call OI + volume concentration** | Aggregate `open_interest`, `volume` by strike/type | Where the market is hedged; gamma walls | No |

**Note the last column.** Every signal is point-in-time / cross-sectional — computed from a
*single* chain snapshot. They all work **today** on the two snapshots already on disk. That is the
key feasibility result: no history is required to ship the whole signal set.

### 3.4 The one real constraint — chain history

**CBOE's free tier serves the current chain only. No history.** Two snapshots exist on disk
(2026-06-17, 2026-07-10). History is needed only for *percentile/z-score context* ("skew is at its
95th percentile") and for a per-name `eqvol:<TKR>` covariance factor. Three options:

1. **Accumulate forward** — the existing weekly upsert already does this. Usable percentiles in
   ~1 year. Zero work, zero cost, slow.
2. **Backfill via Alpha Vantage** — verified returning IV + greeks back to 2008. One request =
   one ticker-day. Weekly for 14 names × 2 years ≈ 1,450 ticker-days ≈ **58 days of background
   trickle at 25 req/day**. Feasible as a slow one-time job. ⚠ **Rate limit and free-vs-premium
   status are unverified (§1.3) — pin before committing.**
3. **Proxy meanwhile** — CBOE index history (VIX/VIX3M/SKEW/VXN, full history, already have) for
   *systematic* vol dynamics; per-name chains for *cross-sectional* level and shape. This is what
   the repo already does for the term/skew factors; extending it is consistent.

**Recommendation: do not block on history.** Ship the point-in-time signals, start the weekly
archive immediately, treat backfill as an optional background phase.

### 3.5 Architecture

```
src/marketdata/options/
  signals.py               ← NEW: derive signals from a chain snapshot
  signals_runner.py        → data/marketdata/options/signals/option_signals.csv
                             + risk_neutral_density/<TKR>_<snap>.csv
configs/marketdata/options/options_marketdata.json   ← +7 underliers, +9 indices, +signals block
configs/marketdata/dq_gate.json                      ← gate the signals file

src/scenariogen/options/
  provider.py       OptionSignalProvider -> per-name eqvol overlay + severity multiplier
  plausibility.py   hook: market-implied tail probability for the scenario's own move
```

Derivation lives in `marketdata` because it is a **data product from a feed** — provenance
sidecar, DQ gate, snapshot-addressed — consistent with how every other derived series is treated.

### 3.6 How it feeds scenariogen / risk

Three integration points, increasing in value:

1. **Per-name equity vol overlay.** `eqvol` is currently a *single global* factor
   ([`factor_keys.py`](../../src/scenariogen/factor_keys.py)). Signal 1 gives a real per-name level
   and signal 4 a per-name skew, enabling `eqvol:<TKR>` overlays. Closes GAP 4's residual and
   FINDING 10 with data already downloaded.
2. **Severity calibration.** Signals 2/4/7 give a market-stress multiplier on the equity legs —
   same clamped-multiplier shape as §2.5 and `arag/calibrate.py`.
3. **Forward-looking plausibility gate — the biggest win.**
   [`plausibility.py`](../../src/scenariogen/plausibility.py) currently checks the derived vector
   against *historical* envelopes only. Signal 5 lets it also ask: *"the market currently prices
   P(NVDA ≤ −30% by 3M) at X%."* A scenario can then be graded against what the market is actually
   pricing, not only against what has happened before. That is a genuinely new capability, free.

**Bonus outside scenariogen.** §5.3 of the market-data design notes AAPL/MSFT/AMZN have no clean
free single-name CDS quotes and fall back to hardcoded 60/60/75bp. Signal 6 (Merton) is a free,
independent estimate for exactly those names, and an early-warning cross-check on ORCL (177bp) and
META (73.5bp).

### 3.7 Phases

| Phase | Scope | Size | Status |
|---|---|---|---|
| **B0** | Config: +7 underliers, +9 indices; re-run options runner; DQ-gate entries | **S** | ✅ delivered in P0 (§11.8) |
| **B1** | `signals.py` — signals 1, 2, 4, 7, 8 + tests | M | ✅ delivered 2026-07-24 |
| **B2** | Signals 3 (earnings move) + 5 (RND / tail probability) — the numerically fiddly two | M–L | ✅ delivered 2026-07-24 |
| **B3** | Signal 6 (Merton/CreditGrades) + CDS cross-check report | M | ✅ delivered 2026-07-24 |
| **B4** | `scenariogen/options/` provider: per-name eqvol + severity multiplier + plausibility hook | M | ✅ delivered 2026-07-24 — **P1 complete** |
| **B5** | *Optional* — Alpha Vantage backfill → percentile context + `eqvol:<TKR>` covariance factor | M + wall-clock | **closed 2026-07-26: `HISTORICAL_OPTIONS` is premium** → not free-buildable (§0); forward-accumulation fallback running (§11.14) |
| **B6** | BFF `/options/signals/*` + UI (vol surface, skew, RND chart on trade pages) | M | ✅ delivered 2026-07-26 (§11.11) |

**B1 (delivered).** `src/marketdata/options/signals.py` + `signals_runner.py` — point-in-time
signals 1/2/4/7/8 from each chain snapshot (reads `chains/` **and** `chains_signals/`), written to
`options/signals/option_signals.csv`. Uses the CBOE `delta` to pick ATM/25Δ contracts, so no
spot/forward inversion is needed for B1. Term structure is the 30d-vs-90d convention (a first cut
that took the nearest ≥7d expiry hit a 7-day IV hump and mis-flagged every name inverted). Closes
GAP 4's residual (per-name vol level).

**B2 (delivered).** `src/marketdata/options/rnd.py` — signal 3 (front-expiry expected move =
ATM straddle ÷ forward) and signal 5 (risk-neutral density + tail probabilities). Forward and
discount recovered per expiry from a put-call-parity regression (no assumed rate); a **smoothed IV
smile → Black-Scholes → Breeden-Litzenberger** pipeline gives a stable density (numpy only, no
scipy). Validated against the analytic lognormal for a flat smile (CDF matches `N(−d₂)` within
0.03); density integrates to 1, CDF monotone, tail probabilities monotone. Tail probs + expected
move land in `option_signals.csv`; the full densities in `options/signals/rnd_curves.csv` (one long
CSV, not many small files — dual-mode trap 3). **Note:** the *earnings-specific* move (expiry
straddling the next earnings date) awaits P3's calendar; B2 ships the generic front-expiry move.

**B3 (delivered).** `src/marketdata/options/credit_implied.py` — signal 6. Merton (1974) solved by
a KMV fixed-point for the asset value/vol → distance-to-default + risk-neutral PD, **and**
CreditGrades (Finger 2002) for the spread itself, because basic Merton gives ~0 short-horizon
spreads for investment-grade names (the credit-spread puzzle) and CreditGrades' random default
barrier restores realistic non-zero spreads. Leverage comes from a **committed fundamentals config**
`configs/marketdata/options/issuer_fundamentals.json` (debt from the on-disk EDGAR XBRL facts +
10-K shares), read via the façade — offline, reproducible, dual-mode, no live-ARag dependency;
documented so ARag `facts_lookup` can auto-refresh it later. Per-(issuer, snapshot) output in
`options/signals/credit_implied.csv` with a CDS cross-check for the names with a single-name
reference (ORCL/META + the AAPL/MSFT/AMZN fallbacks). **Data caveat:** on this synthetic 2026 book
the elevated equity prices compress per-share leverage, so the absolute structural spreads read low
(only ORCL, at ~20% leverage, is non-trivial). The **distance-to-default ranking is the robust
signal** (ORCL DD 2.5 vs the mega-caps' 8–15) and the large negative CG-vs-CDS basis is itself
interpretable — the equity market prices these names far safer than the CDS market does. Validated
by tests on genuinely-levered synthetic firms (CreditGrades > 50bp at 1.5× debt/share, monotone in
leverage and vol), so the near-zero portfolio spreads are a property of the data, not the model. It
is an **indicator only** — the DTCC CDS feed stays the pricing input.

**B4 (delivered — P1 complete).** `src/scenariogen/options/provider.py` (`OptionSignalProvider`)
loads the B1-B3 signals + RND curves (latest snapshot per name, via the façade) and serves
scenariogen three things: `implied_prob(ticker, move)` (market-implied tail probability off the RND
CDF), `atm_iv`/`skew` (per-name vol-overlay inputs), and a clamped mode-(ii) `severity_multiplier`.
The headline — the design's "biggest win" — is a **forward-looking plausibility check**: a new
`market_implied` finding in `PlausibilityChecker` grades each `eq:<TKR>` shock against what the
option market actually prices. It is **strictly additive and flag-only** — it records how extreme a
move is (e.g. NVDA −35% sits at the 0.27th percentile of the market's risk-neutral distribution) but
**never fails**, so gating is unchanged; and it is a complete no-op when the signals aren't on disk
(`market=None`), so every existing plausibility test and the authored-scenario gate stay green. The
runner wires the provider in automatically when the option signals exist. Validated end-to-end on
the real signals (NVDA/META/SPX tail probabilities and severity multipliers all economically
coherent — index lowest, high-vol single names richer). **Remaining wiring for a follow-on:** the
per-name eqvol overlay and `severity_multiplier` are *exposed* by the provider but not yet applied
to the equity legs during materialization (that touches the `dualrag`/`calibrate` seam); the
plausibility hook — the highest-value integration — is fully live.

---

**Workstream B (options-market intelligence) status: B0-B4 delivered; B5 gated (§8.1), B6 (BFF/UI)
in P6.** The full point-in-time signal set, the risk-neutral density, the credit indicator, and the
forward-looking plausibility gate all run today on the chains already on disk.

---

**P2 / F4 (style momentum) — delivered 2026-07-24.** The first mode-(i) factor. New marketdata
subpackage `src/marketdata/factors/` ingests the **Ken French daily FF5 + Momentum** ZIPs (parsed in
memory — ZIP → `BytesIO` → `zipfile`, never extracted, trap 1) into `factors/ff_factors.csv`.
`factor_history` gains an **`include_style`** flag that adds a `style:MOM` column to the aligned
panel; scenariogen turns it on (`covariance.json: include_style`), so **MOM enters the scenariogen
Σ** as a canonical driver, while the **full-reval VaR and sensitivity VaR are byte-identical**
(they never opt in — `style:MOM` has no bump transform, so it is a *driver-only* factor whose P&L is
entirely the equity moves it propagates to). A `momentum_crash` scenario is now authorable
(`authoring.py apply`, no macro/ARag services); its `style:MOM` driver propagates through Σ.

*Measured (before → after, scenariogen only):* Σ **31 → 32 factors**; MOM correlates **+0.30–0.36**
with the high-momentum names (MU/XLK/SPX). `momentum_crash`/adverse (`style:MOM −18%`) propagates to
**MU −30%, SMH −13%, AVGO/TSM −10%, NVDA −6%** while the **index holds (SPX −3%)** and a low-momentum
name rotates up (MSFT +4%) — the positioning-unwind co-move a name-level model structurally cannot
produce. All authored scenarios (incl. the new one) stay promotable; the style bound (`max_abs_pct
30`) admits a severe momentum crash. The style-augmented Σ window ends a few weeks earlier than the
base panel (French's monthly refresh lag), so **existing scenarios' propagated vectors shift
slightly** — a disclosed mode-(i) effect confined to scenariogen; the full-reval measure and the
golden PVs are untouched (governance PASS). **Follow-on:** the FF5+MOM style-exposure regression on
the book (FINDING 4 use 1) is not yet wired — the driver is the higher-value half and is live.

**P2 / F3 (basis) — ETF-basket dispersion delivered 2026-07-24; swap-spread + CDS-bond scoped.**
FINDING 3's *clearest case* and the P2 exit criterion — trade #6 (short SMH vs the single-name semi
longs) — is delivered as a driver-only factor **`basis:SMH_SEMI`** (SMH return − equal-weight
NVDA/TSM/AVGO/MU return), added to the scenariogen Σ under the same `include_style` opt-in as MOM
(so **full-reval and sensitivity VaR stay 31-factor / byte-identical**; scenariogen Σ is now 33). Its
correlation structure is the key to why this works: the basis is **≈0 with SMH** (SMH *is* a semi
ETF, so `var(SMH) ≈ cov(SMH, semis)`) and **negative with each single name**, so a positive shock
drives *the long semis down while the short ETF holds* — both legs of the hedge losing, the exact
co-move a name-level Σ nets to flat. A `semis_dispersion` scenario is authorable; at adverse it gives
**MU −10.6%, NVDA −4.9%, TSM/AVGO −2.6..−2.9% vs SMH +0.8%**, promotable. Shrinkage (0.5) keeps the
basis — an exact linear combo of the `eq:` legs — from making Σ singular. Validated by tests
(dispersion direction; scenario promotable).

**Swap-spread + CDS-bond bases — delivered 2026-07-24 (F3 follow-on, P2 complete).** Unlike the
ETF-basket case (two legs already separate `eq:` factors), these are *not* between existing factors:
the swap (#22) prices off the SOFR curve and the USTs off Treasury; the bonds price off a Z-spread,
the CDS off CDS par spreads. Two new **one-sided repricing bumps** express each basis where the
existing joint bumps cannot: `shift_swap_spread` moves **only `TreasuryCurveData`** (holding the
OIS/swap curve fixed — `shift_ir` moves both), and `shift_cds_bond_basis` moves **only single-name
`CdsSpreadData`** (holding the bond Z-spreads *and* the CDX index fixed — `shift_credit` moves all).
Because there is **no free daily history** of OIS par rates or bond Z-spreads, these can't be Σ
covariance factors; they are **authorable overlay factors** (`basis:SWAP_SPREAD`, `basis:CDS_BOND`,
bp-quoted, their own `basis_bp` plausibility class), lowered to the direct bumps by
`vector_to_transforms`. Isolation is validated on the live book: `shift_swap_spread(+30bp)` reprices
**only the USTs** (10Y −$4.3M, 2Y −$1.1M) — the SOFR swap and the corporate bonds are untouched,
confirming the bonds discount off OIS, not Treasury; `shift_cds_bond_basis(+60bp)` reprices **only
the single-name CDS** (ORCL +$0.62M, META +$0.66M) — the bonds and the CDX index are untouched. A
`funding_basis_squeeze` scenario dislocates both bases (as overlays) on top of a covariance `credit`
driver; promotable. Unit tests pin the isolation (no cross-contamination between the rate and credit
legs). **What this closes:** three of FINDING 3's four bases are now stressable — the fourth (TWD
XCCY) stays unfillable and is handled by F1's liquidity horizon in P4. These two are stress-only
(no daily history → not in VaR), which is the honest data limit.

### 3.8 Risks

- **RND numerics.** Breeden–Litzenberger is second-derivative-sensitive; without strike smoothing
  and wide-strike filtering the tail probabilities are garbage. Mitigate: fit a smooth IV
  parameterization first, then differentiate; DQ-gate the output for non-negativity and
  integration to 1. **The one item with real technical risk.**
- **Delayed + stale quotes.** CBOE is ~15-min delayed and deep strikes carry stale IVs. The
  existing `min_iv`/`max_iv` filters help; add OI/volume floors.
- **Merton is a model, not a quote.** Report it as an indicator with a stated calibration, never as
  a spread feeding PV — the DTCC CDS feed stays the pricing input.

---

## 4. The diagnostic behind §5

`configs/pricer/pricer_config.json` defines **39 VaR risk factors**. Every one is a **price or
level**: 12 nominal rate tenors, 2 credit spreads, 12 equity spots, 9 vol levels, 3 FX, 1 foreign
rate. That is a complete *marks* model — "what are my positions worth if factor levels move."

A state-of-the-art bank or hedge-fund stress module answers three questions this cannot:

1. **Can I get out?** — liquidity and funding (FINDINGS 1, 2)
2. **Will my hedges hold?** — basis and correlation-regime (FINDINGS 3, 7)
3. **What am I actually betting on, and who else is betting it?** — style factors and crowding
   (FINDINGS 4, 5)

And for this specific book, a fourth: **is the AI thesis physically true?** (FINDING 9)

---

## 5. Ten factor-class findings

### FINDING 1 — Liquidity risk is entirely absent *(highest-impact gap)*

Every scenario reprices at mid, with `horizon_days: 10` flat across all factors. Basel/FRTB
mandates **liquidity horizons by risk-factor class** — 10d large-cap equity and major rates/FX,
20d IG credit, 40d equity vol and HY credit, 60–120d for the illiquid tail. This book's positions
genuinely differ:

| Position | Realistic LH | Currently |
|---|---|---|
| SPX/QQQ options, UST 10Y/2Y | 10d | 10d ✓ |
| ORCL 5.55% 2053, META 5.40% 2054 | 20–40d | 10d ✗ |
| Single-name CDS (#28/#29) | 40d | 10d ✗ |
| **5Y USD/TWD XCCY swap (#26)** | **60d+** | 10d ✗ |

**The TWD swap is the standout.** TWD is not freely convertible and GAP 5 already records that no
free XCCY basis exists. In `taiwan_geopolitical` that trade is simultaneously the *least* liquid
and the *most* stressed — carried at a 10-day horizon. Largest single understatement of risk in
the current setup.

**Free data, all already on disk:**
- **`bid`/`ask` in the option chains** — downloaded per contract, currently discarded. Relative
  spread `(ask−bid)/mid` is a direct liquidity measure, and its *widening* is itself the signal.
- **`volume` in `prices_daily.csv`** — 10 years. Position/ADV = days-to-liquidate; directly
  computable for a book concentrated in NVDA/AVGO/MU.
- **Amihud illiquidity** = `|return| / dollar volume` — full history from existing OHLCV.

Implementation: a horizon scaler (`√LH/√10`) per factor class plus a bid-ask haircut on stressed
P&L. Converts a scenario from a *mid-market mark* into an *exit-value estimate*.

### FINDING 2 — The scenario says what you lose, not what you must post

What kills a fund is the margin call, not the mark. This book has 4 IR derivatives, 3 CDS and 2 FX
derivatives — all margined, cleared or under CSA.

Under +100bp the 10Y payer swap (#22) *gains* while the receiver swaption (#24) and the bond book
lose, and the CDS trades post variation margin. **Net cash outflow ≠ net P&L**, and only P&L is
computed. Institutional practice: stressed P&L + VM call + IM increase (IM rises with vol under
SIMM/CCP models) + collateral haircut widening.

**Free data:** `SOFR` and `IORB` are **both already columns of `fred_rates.csv`** — their spread
is a canonical funding-stress gauge computable today with zero new ingest. NY Fed publishes SOFR
percentiles and TGCR/BGCR repo free; FRED `NFCI` gives financial conditions.

Cheapest high-leverage move: add a **cash/collateral line** to the stress report alongside P&L.
The pricer already emits per-trade P&L; margined-vs-unmargined is a portfolio-config tag.

### FINDING 3 — Four live basis risks in the book. Zero are risk factors.

Most portfolio-specific finding. Every hedge here is imperfect, and in stress **every one of these
bases widens** — that *is* a hedge failing:

| Hedge | The basis | Current treatment |
|---|---|---|
| #6 SMH short vs #1–4 single-name longs | ETF-vs-basket / dispersion | Only implicit in Σ — hedge-ratio failure is not a factor |
| #28/#29 CDS vs #17/#18 bonds | **CDS-bond basis** | §5.3: *"held at zero"* |
| #22 SOFR IRS vs #20/#21 USTs | Swap spread | Both curves ingested; the spread is not a factor |
| #26 XCCY swap | USD/TWD XCCY basis | GAP 5: unfillable free |

Trade #6 is the clearest case: SMH short against single-name semis long is a **relative-value
trade whose entire risk is the spread**. A scenario where SMH falls in lockstep with NVDA shows a
near-flat P&L and materially understates the risk.

**Three of the four are computable from data already on disk:**
- **Swap spread** = Treasury par − SOFR OIS par — **already computed inside `bonds_adapter`** for
  the §5.3 credit lift (~+42bp at 10Y). It exists as a derived number; it is simply not registered
  as a risk factor.
- **CDS-bond basis** = DTCC single-name CDS (have) − bond Z-spread (computed).
- **ETF-basket basis** = XLK/SMH return − weighted constituent return, all in `prices_daily.csv`.

Highest ratio of insight to effort in this document.

### FINDING 4 — The book is a *style* bet, and style factors are free and daily back to 1926

All 39 factors are name-level. Banks run Barra/Axioma style decomposition: momentum, growth, size,
quality, low-vol. This book is maximum-length **momentum + growth**, concentrated in one industry.

**Momentum crashes are a distinct, recurring, violent phenomenon that a name-level covariance
model structurally cannot see**, because they are driven by positioning unwind rather than
fundamentals: 2009-03/04, 2016-11, 2018-Q4, 2020-11 (vaccine rotation), 2021-01, 2022. In each,
high-momentum names fell hard *while the index was flat or rising* — a co-move pattern an
EWMA-250d Σ estimated in calm markets will not reproduce.

**Ken French Data Library — verified 200.** FF5 daily, momentum daily, 49-industry daily. Free, no
key, back to 1926/1963. Best quality-to-cost ratio of anything here.

Three uses:
1. Regress book factor returns on FF5+MOM → **real style exposures**.
2. Add a `style:MOM` driver so **"momentum crash" becomes a first-class authorable scenario**.
3. **Unblocks deferred work.** §4.9 of the market-data design records that Methodology A
   (historical-analogue replay) was deferred because ICE licensing limits FRED OAS to a ~3-year
   rolling window. French factors carry a century of daily crisis co-moves with no such limit —
   recovering historical replay for the equity block, free.

### FINDING 5 — Crowding: an AI-theme book's dominant non-fundamental risk

Every name here is consensus-long. The risk is not that NVDA misses a quarter — it is that
everyone owns it and leaves together. Crowding is a *state*, not a covariance, so Σ cannot
represent it.

**Verified free:** SEC 13F (EDGAR pipeline exists), SEC Form 4 insider transactions (efts probe
200), FINRA short-interest API (200). **CFTC COT unreachable from this sandbox — unverified.**

Metrics: 13F holder count and ownership concentration per name; Form 4 net insider selling; short
interest / float and days-to-cover.

**Note the asymmetry: #6 is a short.** A crowded *short* is squeeze risk — an upside tail a
long-book stress framework will never flag. Worth surfacing explicitly.

Caveat: 13F is quarterly and 45-days lagged — a slow-moving crowding *level*, not a trigger. Frame
it as a severity multiplier.

### FINDING 6 — Nominal-only rates is the wrong decomposition for a long-duration tech book

All 12 IR factors are nominal (`DGS*`). A +100bp nominal move is treated identically whether it
comes from real rates or breakevens. For *this* book those are not equivalent:

- **Real-rate-driven** → discount rate on long-duration growth equity rises → tech *and* long
  bonds fall together. Correlated loss across both blocks.
- **Breakeven-driven** → nominal growth up → equity often holds; bonds lose. Partial natural hedge.

2022 was real-rate-driven, which is exactly why tech and bonds fell together. **A nominal-only
factor model cannot express that scenario** — and it is the most relevant historical analogue for
this portfolio.

Free: FRED `DFII10`, `T10YIE`, `T5YIFR` via the same `fredgraph.csv` path already in use. Plus
**NY Fed ACM term premium (verified 200)**, decomposing yields into expectations vs term premium —
which matters because the `rate_capital` scenario's stated driver is literally *"+150bp 10s30s
steepener (term premium)"*, a term-premium narrative with no term-premium data behind it.

### FINDING 7 — Implied correlation: the missing link between idiosyncratic and systemic

The dual-RAG model splits systematic + idiosyncratic, but *how much* propagates is governed by Σ —
a **historical** estimate driving a **forward-looking** scenario. `covariance.json` picks the
regime by hand (`window: "ewma"`, crisis windows available as a config choice).

Options markets price forward correlation directly. **CBOE COR1M, COR3M and DSPX verified 200** —
free, full history. Workstream B's single-name + SPX chains also let us compute dispersion
ourselves.

Use as a **data-driven regime switch on Σ**: elevated implied correlation → propagate broadly
(systemic); low → keep the shock idiosyncratic. Upgrades the covariance window from a hand-chosen
constant to a market-implied state.

### FINDING 8 — The Taiwan scenario has a narrative trigger but no quantitative one

**Verified free: GPR daily index (Caldara-Iacoviello) — 200. EPU daily — 200.** Both appear in the
stress-test design's §4C wishlist; neither is ingested.

GPR carries **country sub-indices (Taiwan/China)** with daily history back to 1900. Three uses:
(a) **trigger** — a GPR-TWN z-score threshold flags the scenario as live rather than hypothetical;
(b) **severity calibration** — size the shock by GPR percentile; (c) **historical anchor** — locate
past GPR spikes and read off actual TWD/semis/VIX co-moves. Methodology A for Taiwan, free.

### FINDING 9 — Physical throughput: ground truth for the AI thesis

The whole book is one bet on the AI capex cycle. ARag gives disclosure, MacroRAG gives narrative;
neither gives *physical* verification.

The design already ingests **EIA datacenter-grid power demand** (PJM/ERCO/MISO/CISO), explicitly
described as AI-capex corroboration — but §5.2 lists it as narrative-only, **not consumed**.
Wiring the existing feed into a scenario driver is nearly free.

Beyond that: TSMC monthly revenue, SIA global billings, Korea 20-day exports, FRED `IPG3344S`
semiconductor industrial production.

**Lowest-confidence finding here.** TSMC/SIA/Korea endpoints were not verified and several are
HTML scrapes rather than clean APIs. Safe subset: EIA (key support already exists) + FRED
`IPG3344S`.

### FINDING 10 — A structural defect worth fixing regardless

`EQVOL_NVDA`, `EQVOL_MSFT`, `EQVOL_META`, `EQVOL_TSLA`, `EQVOL_QQQ` and `EQVOL_SPX` **all read the
same file** — `options/vol_indices/VIX_History.csv`. Only `EQVOL_AAPL` is genuinely distinct
(VXAPL).

So the **sensitivity-VaR factor set** (`config['risk']['var']['factors']`, the "39 factors")
contains ~6 perfectly collinear columns. The factor count overstates real dimensionality and it
makes the parametric variance-covariance cross-check (`var._parametric`) and the component-ES
decomposition (`var._decompose`) operate on a near-singular covariance.

> **Correction (2026-07-24, during P0 build).** An earlier draft of this finding and of §9 said
> the aliases also inflate the **scenariogen conditional-covariance Σ** and that removing them
> "buys back dimensionality" for the mode-(i) work, possibly letting `shrinkage` fall from 0.5.
> **That is wrong, and the build proved it.** scenariogen's Σ and the full-revaluation VaR are
> built from `factor_history.aligned_panel`, which already collapses equity vol to a **single**
> `eqvol` column (`_equity_vol()` reads `VIX_History.csv` once). The ~6 aliases live *only* in the
> separate sensitivity-VaR factor dict; they never entered the Σ. So F10 does **not** free up
> headroom in the Σ that F3/F4/F6/F7 spend into — that Σ was never carrying the duplication. The
> de-alias is still worth doing (honest factor count; a non-singular parametric cross-check; and
> QQQ moved onto its correct VXN index, a real data improvement), but the dimensionality argument
> for sequencing it *first* does not hold. See §11.6 for the corrected sequencing rationale.

**Workstream B signal 1 refines it further** with data already on disk; P0 delivers the first,
config-level pass (below).

---

## 6. Priority view

| Tier | Findings | Why | Data readiness |
|---|---|---|---|
| **1** | F3 basis · F4 style · F1 liquidity · F10 de-aliasing | Highest insight per unit effort; each addresses a risk the model is structurally blind to | 3 of 4 bases, ADV, bid-ask all **on disk**; Ken French verified |
| **2** | F6 real/breakeven + ACM · F7 implied correlation · F2 funding | Real conceptual upgrades, small-to-medium build | Verified free; F2 needs a portfolio tag |
| **3** | F8 GPR/EPU · F5 crowding · F9 throughput | Narrower or lagged | F8 verified; F5 partly; F9 weakest |

**If only two were built: F4 (style factors) and F3 (basis risk).** F4 because "momentum crash" is
the most likely way this specific book loses a lot of money quickly and the framework cannot
currently express it — for a free zip file. F3 because four hedges are carried as if perfect,
three of the four bases are already computable from data on disk, and hedge failure is precisely a
stress-scenario phenomenon.

This tiering ranks candidates by *value*. It is not the build order — that additionally has to
respect dimensionality, sourcing risk and blast radius, and is set out in **§11**.

---

## 7. Explicitly out of scope

Stated because a list like this invites scope creep:

- **A full Barra-style risk model** — F4 gets ~80% of the value from a free zip; a proprietary
  multi-factor model is not proportionate to a <50-user deployment.
- **DCC-GARCH / regime-switching Σ** — F7's implied-correlation switch is simpler,
  forward-looking and free. Time-varying covariance estimation adds calibration burden and
  fragility for marginal gain.
- **Agent-based / fire-sale contagion models** — research-grade, unvalidatable on free data,
  unauditable.
- **Alternative data** (satellite, card spend, web scrape) — no free tier; licensing conflicts
  with the free-only constraint.
- **Intraday microstructure** — the book is stressed at 10-day-plus horizons; tick data would be
  cost without signal.

---

## 8. Open items to pin before any build

1. **Alpha Vantage** — ~~free-tier or premium~~ **RESOLVED 2026-07-26: `HISTORICAL_OPTIONS` is
   premium** (tested with a real free key → "This is a premium endpoint" notice). B5's AV backfill is
   therefore not free-accessible and is **precluded by the §0 free-data constraint**; the
   forward-accumulation fallback (§3.4 opt 1) is already running. Blocks §3.4 option 2 only; the rest
   of Workstream B was unaffected and shipped (B0–B4).
2. **CFTC COT** — re-probe; unreachable from the dev sandbox (FINDING 5). **RESOLVED 2026-07-26:
   still `000`** — dropped; F5 shipped on the verified FINRA short-interest subset (§11.14).
3. **TSMC / SIA / Korea exports** — confirm whether any offers a clean machine-readable free feed
   (FINDING 9).
4. **Yahoo / Nasdaq RSS ToS** — confirm display-only usage is acceptable, or drop Tier 3 (§2.3).
5. **Liquidity-horizon table** — the per-factor-class LH assignment in FINDING 1 is a desk/
   governance decision, not a data one.
6. **Keep the two CIK maps consistent** — this repo's 8-K path and ARag's 10-K/Q path each carry
   one (§9). Both derive from the same SEC-published file; add a check if they ever drift.

---

## 9. Placement decision — where each change lives

**Decision (2026-07-23, desk):** **everything in this document lands in this repo, including 8-K
event ingestion.** No cross-repo work. Revisit only if 8-K *text* is wanted — see *When to
revisit* below.

> **Decision history.** An intermediate position (recorded and then reverted the same day) placed
> all SEC/EDGAR ingestion in ARag on the grounds that it avoids a future migration and reaches both
> scenario-generation paths automatically. The second of those turned out to be weaker than stated:
> `llm/mcp/registry.py`'s `ToolSource` is a **Protocol**, not MCP-bound, so a local feed reaches the
> agentic path through a small adapter rather than not at all (§2.4, phase A4). With that argument
> reduced to a one-file integration step, the single-project placement wins on simplicity.

### The line

| Change | Placement | Rationale |
|---|---|---|
| SEC 8-K **structured metadata** (item codes, dates, accessions) | **this repo**, `marketdata/news/` | No NLP, no retrieval — a numeric feature. Belongs with the other free feeds, and stays inside the content-addressed `snapshot_id` |
| SEC filing **text / semantics** (10-K/Q today; 8-K item text if ever needed) | **ARag** | Retrieval and embeddings are ARag's job; this repo does not build a third RAG |
| GDELT, Yahoo/Nasdaq RSS, CBOE, Ken French, NY Fed, FRED, GPR/EPU, FINRA, EIA | **this repo** | Not filings corpora |
| All options signals (§3), all ten factor findings (§5) | **this repo** | Tightly coupled to *this* book (currently 38 trades) and its factor model; single consumer |

**The rule: structured filing *metadata* here; filing *text semantics* in ARag.**

### Why single-project

1. **Reproducibility spine.** Results are stamped with `RunProvenance` + a content-addressed
   market-data `snapshot_id`. A local `sec_events.csv` is *inside* that hash, so an 8-K-driven
   scenario is strictly re-derivable. A service response would be provenance-recorded (trace JSONL
   + per-leg `driver_sources`) but **not** content-addressed — attribution, not a pin. This is the
   invariant the root `CLAUDE.md` says not to break, and it is the decisive argument.
2. **Single consumer.** Unlike ARag/MacroRAG — genuinely general-purpose services with plausible
   other consumers — these signals are meaningless outside this book. F3's basis risks reference
   specific trade IDs.
3. **Scale is not a constraint.** ~30k lines across four well-partitioned subsystems (110 files in
   `pricer`, 66 in `scenariogen`, 51 in `marketdata`); the full proposal adds ~3–4k lines, ~12%.
   The repo has absorbed larger single workstreams (pipeline P0–P5, risk Tiers 1–3, pricing A→C,
   UI Phases 0–6, AWS A–D).
4. **Free infrastructure.** New feeds inherit the DQ gate, provenance sidecars, the dual-mode
   storage façade, the job runner, containers, and the BFF/UI — all already built.
5. **No cross-repo change coupling**, no second deploy unit, no service-availability dependency on
   the news path.

### What it costs

- **A second, smaller CIK map** alongside ARag's (§2.7, §8 item 6). Accepted: it is a static
  SEC-published file and the 8-K path needs no retrieval machinery.
- **Manual wiring to the agentic path** — the A4 `ToolSource` adapter, rather than reaching both
  surfaces automatically via ARag's capability catalog and its drift test.
- **A latent migration** if 8-K *text* is ever wanted: the ingest would move to ARag and the
  consumer rewire. Judged acceptable — the metadata path is small, and the trigger is speculative.
- **Feed fragility churns the risk engine's repo** (§2.7) — mitigated by the existing fail-loud
  `MarketDataIngestError` floor and the DQ gate.

### Entry-mode discipline (applies to everything in §5)

Placement answers *where the code lives*; it does not answer *how a signal enters the model*. The
binding constraint there is **Σ dimensionality, not repo layout**. `covariance.json` records that
the **31-factor** Σ over ~250 days already produced *"very noisy cross-asset correlations"* and a
propagation failure (*"a +100bp rate scenario propagated to a +96% equity move"*), which
`shrinkage: 0.5` exists to suppress. That is ~8 observations per factor — already marginal.

Naively promoting every finding to a covariance factor approaches ~70 factors on 250 observations
(~3.6 obs/factor): degenerate, and shrinkage cannot rescue it. So classify each item by **how it
enters**:

| Entry mode | Costs Σ dimensionality? | Items |
|---|---|---|
| **(i) New covariance factor** — needs history | **Yes** | Style/MOM (F4), the four basis spreads (F3), real-rate/breakeven split (F6), implied correlation (F7) |
| **(ii) Clamped multiplier on an existing leg** | No | News salience (§2.5), options severity (§3.6), crowding (F5), GPR (F8) |
| **(iii) Post-processing on the materialized result** | No | Liquidity horizons + bid-ask haircut (F1), funding/margin line (F2) |

Only ~4 of the 12 items are mode (i). Mode (ii) reuses the existing `arag/calibrate.py` seam at
zero dimensional cost. F1 is the clearest case: a liquidity horizon is a `√LH/√10` scaler applied
*after* the vector is materialized — making it a risk factor would be a modeling error as well as
a dimensionality cost.

**Sequencing constraint (corrected 2026-07-24 during P0):** the original constraint here —
*"fix FINDING 10 before any mode-(i) factor, because removing the aliased `EQVOL_*` columns buys
back Σ dimensionality"* — **was based on a false premise** and is withdrawn. The aliases were never
in the conditional-covariance Σ (it reads one `eqvol` column from `factor_history.aligned_panel`);
they were only in the sensitivity-VaR factor dict. F10 is still done first in P0 because it is
cheap, config-level, and declutters the parametric cross-check — **not** because it frees Σ
headroom, which it does not. The real dimensionality budget for mode-(i) work is unchanged by P0;
mode-(i) factors must still be added sparingly and with shrinkage in mind. See FINDING 10's
correction note and §11.6.

### When to revisit

- **8-K *text* is wanted** (semantic search over item narratives, not just item codes) → move 8-K
  ingestion to ARag as a capability in
  [`arag/capabilities.py`](../../src/scenariogen/arag/capabilities.py), retire
  `sec_events_downloader`, and repoint `NewsEventProvider` at `AragClient`. **This is the expected
  trigger and is explicitly deferred, not rejected.** Two things make the move cheap if the
  interfaces are respected now: keep `NewsEventProvider` the *only* consumer of `sec_events.csv`
  (so the swap is one file), and give any ARag capability a PIT `as_of` so the reproducibility
  property currently held by `snapshot_id` survives the move.
- Option signals needing intraday/tick storage or heavy compute → extract as a service.
- A **second portfolio or desk** appears → market-intelligence stops being single-consumer and
  should be split out as shared infrastructure. Not true today.

---

## 10. Dual-mode requirement (local + AWS)

**Standing requirement (desk, 2026-07-23): every artifact of this program must run unchanged in
both `PRICER_DEPLOY_MODE=local` and `=aws`.** No new code may assume a local filesystem. See
[../guides/deployment_guide.md](../guides/deployment_guide.md) and
[AITrade_aws_deployment_design.md](AITrade_aws_deployment_design.md).

> **Reading convention for this document:** every `data/...` path written above is a **logical
> location**, resolved by `storage.data_path(...)`. It is never a literal filesystem path. In AWS
> mode the same logical path resolves under `PRICER_DATA_URI` (`s3://…`).

### 10.1 What is already free

`src/marketdata/common/data_store.py` **already routes through `src.common.storage`** —
`get_output_path()` calls `storage.data_path()` + `makedirs_for()`, and `save_csv`/`load_csv`/
`upsert_csv`/`write_provenance` all go through the façade. **A new downloader that follows the
house convention (`get_output_path` → `save_csv`/`upsert_csv`) is dual-mode with no extra work.**

Likewise, `src/common/jobs/` already resolves `LocalSubprocessRunner` vs `AwsJobRunner` (SQS via
`JOBS_QUEUE_URL`) through `get_job_runner()`.

So the requirement is satisfied **by convention-following** for the bulk of this program. What
follows is the set of places where this program *departs* from that convention — each one is a
real trap.

### 10.2 The eight traps specific to this program

| # | Where | Trap | Required approach |
|---|---|---|---|
| 1 | **F4** Ken French | Source is a **ZIP**, not CSV | Fetch bytes → `io.BytesIO` + `zipfile` **in memory** → parse → `save_csv`. Never extract to a temp dir under `data/`. French files carry multi-section headers/footers — parse once, persist clean, so downstream never re-parses |
| 2 | **F6** NY Fed ACM | Source is **binary `.xls`** | `storage.read_bytes()` → `io.BytesIO` → `pd.read_excel`. **Never** `pd.read_excel(path)` on a raw path — that breaks on `s3://` |
| 3 | **B2** RND outputs | Many small per-ticker/per-snapshot files; S3 listing of many small CSVs is slow | Prefer Parquet + `storage.write_dataset()`, read back via `storage.query()` (DuckDB) — the existing results-store pattern |
| 4 | **§2.5, §3.6** new scenariogen providers | Existing providers take `project_root: Path` and may tempt a `Path(...)` read | Read through `storage` (`read_text`/`query`/`exists`), not `pathlib`. **Most likely regression in the whole program** |
| 5 | **B1/B2/F3/F4** derivation steps | Intermediate/scratch files | OS temp dir only (`tempfile`). Never `data/` — it is the shared, possibly-remote tree |
| 6 | Any direct pandas I/O | `pd.read_csv("s3://…")` silently ignores `set_storage_options()` | Call **`storage.read_csv(loc, **kwargs)`** (added 2026-07-23, §10.5) — it branches on `is_remote_uri` and threads `storage_options()` through. Never bare `pd.read_csv` on a façade-built location |
| 7 | New runners (`marketdata.news.runner`, `options.signals_runner`) | Launched as a subprocess in AWS instead of enqueued | Route through `get_job_runner()`, and add the key to the `_WHITELIST` in [`ui/bff/app/jobs.py`](../../ui/bff/app/jobs.py) so it is launchable from the UI in both modes |
| 8 | New credentials (Alpha Vantage; EIA already present) | Ad-hoc `os.getenv` | Extend the typed `MarketdataSettings` model in `src/marketdata/common/settings.py` and surface via `load_credentials()`. A missing optional key must **warn and skip**, never crash (the `eia_power` precedent) |

### 10.3 Per-phase compliance checklist

Applies to every phase in the sequencing plan:

- [ ] All writes go through `data_store` / `storage`; **zero** `project_root / "data" / …` constructions
- [ ] New feeds added to `configs/marketdata/dq_gate.json` with storage-relative paths
- [ ] `.provenance.json` sidecar emitted (free via `save_csv`/`write_provenance`)
- [ ] New runner registered in `get_job_runner()` path **and** the BFF `_WHITELIST`
- [ ] New credentials in the typed settings model; optional keys degrade gracefully
- [ ] Unit tests run in **local** mode by default; remote-path behavior covered by the
      `tests/common/test_storage.py` pattern (no network, no real S3)
- [ ] BFF endpoints read via the façade so the UI works in both modes

### 10.4 Deliberate non-goal

**No S3-specific optimization** (partitioning schemes, lifecycle rules, request-cost tuning) in
this program. The requirement is *mode parity* — identical behavior in both modes — not AWS
performance work. Local mode must stay byte-identical to today.

### 10.5 Pre-existing gaps found and fixed (2026-07-23, **delivered**)

An audit ahead of the build found that the façade discipline holds almost everywhere — **zero**
raw `project_root / "data" / …` constructions under `src/`, and `equity_adapter` is a clean
reference for the pattern B1 should copy (`storage.join(storage.data_base(root), …)` +
`storage.exists`). Two real gaps sat directly on this program's path and are now closed.

**1. The data-quality gate was local-only — a genuine bug.**
`DataQualityGate` typed `base_dir: Path` and did `self.base_dir / rel` + `path.is_file()` +
`pd.read_csv(path)`, and `scripts/marketdata/dq_gate.py` constructed it with
`_ROOT / "data" / "marketdata"` — exactly the raw-path pattern the root `CLAUDE.md` forbids. **In
AWS mode the gate would have reported every declared file as missing.** This blocks §10.3's
"new feeds added to `dq_gate.json`": gating a feed that the gate cannot see is worse than not
gating it, because it reads as a pass.
*Fixed:* `base_dir` is now a **storage location** (`Path | str`), access goes through
`storage.join` / `storage.exists` / `storage.read_csv`, and the script builds it with
`storage.join(storage.data_base(_ROOT), "marketdata")`.

**2. No storage-aware CSV reader existed**, so `storage_options()` was never threaded into any
pandas CSV read. Remote reads fell back to the ambient credential chain and silently ignored
`set_storage_options()` — which is what tests and non-default endpoints rely on.
*Fixed:* added **`storage.read_csv()`** to the façade and routed the four call sites that read
façade-built locations through it: `common/quality/gate.py`, `pricer/risk/factor_history.py`,
`pricer/risk/var.py`, `pricer/loader/snapshot.py`. The first three are exactly where F4 (Ken
French), F6 (ACM/real-rates) and B1 (option signals) will add series, so new factors inherit the
fix rather than repeating the gap.

**Verification:** `ruff` clean on all changed files · `mypy` strict green (252 files) · **full
pytest suite green** (the golden-PV suite is the regression gate for the `snapshot.py` change) ·
the DQ gate re-run locally is unchanged — *24 files checked, 0 block, PASS, exit 0*. New tests
cover both branches: `storage.read_csv` local + remote + kwargs pass-through, and the gate
running against a **remote** `base_dir` (fsspec `memory://`, so no AWS or `s3fs` needed) plus its
missing-file case. The remote-gate test fails against the old implementation.

**Known remaining gaps (not fixed — out of this program's path):** the ad-hoc
`scripts/marketdata/verify_*.py` / `check_*.py` probes hardcode `Path("data/marketdata/…")`. They
are developer scratch tools excluded from the ruff gate, not pipeline steps. Left alone
deliberately; do not copy their pattern.

---

## 11. Implementation sequence

§6 ranks the candidates by *value*. Value alone is the wrong build order, because three of these
items change numbers that already exist and four of them consume a budget (Σ dimensionality) that
one other item first has to refill. This section fixes the order.

### 11.1 Ordering principles

1. **Refill the dimensionality budget before spending it.** §9 records the binding constraint:
   ~250 observations against 31–39 factors is already marginal, and `shrinkage: 0.5` exists to
   suppress the resulting noise. FINDING 10 shows ~6 of those columns are perfect aliases — they
   cost dimensionality and return nothing. Every mode-(i) item (F3, F4, F6, F7) is queued *behind*
   that fix.
2. **On-disk data before new sourcing.** Phases that need no new external feed come first, so
   external fragility (rate limits, ToS, endpoint drift) is never on the critical path. Most of
   Workstream B qualifies: the signals are point-in-time and the chains are already downloaded.
3. **Entry mode sets the blast radius.** Mode (iii) post-processing is additive and trivially
   reversible; mode (ii) multipliers are clamped and bounded; mode (i) changes Σ and therefore
   *every* VaR, ES and scenario magnitude in the repo. Group the Σ-changing work so the numbers
   move deliberately, in few steps, each with a published before/after — not incidentally across
   ten merges.
4. **Provider before surface.** BFF/UI last per workstream, matching the repo's own precedent
   (engine work preceded UI Phases 0–6).

### 11.2 The order

| # | Phase | Contents | Entry mode | New external feed? | Moves existing numbers? | Size |
|---|---|---|---|---|---|---|
| **P0** ✅ | Foundations | **B0** (7 signal chains + 8 vol indices) → **F10** de-alias `EQVOL_*` | enables (i) | Config-only; endpoints verified 200 | Sensitivity-VaR only (±0.1%); **not** Σ/plausibility (see §11.8) | S–M |
| **P1** ✅ | Options intelligence | **B1** → **B2** → **B3** → **B4** | (ii)+(iii) | No — chains on disk | No (additive) | L |
| **P2** ✅ | The "if only two" pair | **F3** basis (ETF Σ-driver + swap/CDS bump overlays) · **F4** style/MOM | **(i)** | F4: Ken French ZIP (trap 1) | **Yes — scenariogen Σ + 2 new bumps** | M–L |
| **P3** ✅ | News & events | **A0**→**A1**→**A2**→**A3**→**A4** | (ii) | SEC EDGAR, GDELT | No (additive) | M–L |
| **P4** ✅ | Exit value & funding | **F1** liquidity horizons + bid-ask haircut · **F2** collateral line | **(iii)** | No — chains, `prices_daily`, `fred_rates` all on disk | Report-additive only (see §11.9) | M |
| **P5** ✅ | Rates & correlation | **F6** real/breakeven + ACM · **F7** implied correlation | **(i)** | ACM `.xls` (trap 2); DSPX/COR1M/COR3M arrive in P0 | **Yes — Σ** (see §11.10) | M |
| **P6** ✅ | Surfaces | **A5** `/news/*` + panel · **B6** `/options/signals/*` + vol-surface/skew/RND charts | — | No | No | M |
| **P7** ✅ | Gated / optional | **F8** GPR/EPU ✅ · **F9** throughput ✅ · **F5** crowding ✅ (scoped) · **B5** AV backfill (closed: premium, not free) | mixed | Yes | No | §11.14 |

**Why P1 sits ahead of P2 despite §6 ranking F3/F4 first.** P1 needs no new sourcing, costs no Σ
dimensionality, and cannot break an existing number — so it delivers the program's headline
capability (a market-implied probability, which nothing else in the repo can produce) at the
lowest risk available. F3/F4 are higher-value but are mode (i): they are worth doing second, not
first.

**Why P4 is late despite FINDING 1 being the highest-impact gap.** F1 is pure post-processing and
could technically ship at any point, but its per-factor-class liquidity-horizon table is a
*governance* decision (§8 item 5), not a data one, and it reprices the meaning of every published
stress number. Landing it after P0/P1/P2 means it is applied once, to a stable factor set, with
desk sign-off — rather than being re-tuned each time Σ changes underneath it.

### 11.3 Phase detail and exit criteria

Every phase additionally satisfies the §10.3 dual-mode checklist and the root `CLAUDE.md` gate
(`ruff` · `mypy` strict · `pytest`) before it is considered done. Only the phase-specific criteria
are listed here.

**P0 — Foundations.** Extend `configs/marketdata/options/options_marketdata.json` with the 7
missing underliers (TSM, AVGO, MU, ORCL, AMZN, XLK, SMH — ORCL and META matter most, being the
CDS/bond issuers) and the 9 missing indices (VXN, RVX, VXD, VXTLT, VXEEM, OIV, DSPX, COR1M,
COR3M); re-run the options runner; add DQ-gate entries; then repoint the aliased `EQVOL_*` factors
at genuinely distinct series and drop what has no distinct source.
*Exit:* no two factor columns in `pricer_config.json` resolve to the same file; a published
before/after of headline VaR, factor contributions and per-scenario plausibility outcomes.
**Delivered 2026-07-24 — see §11.8.** Note the shrinkage sub-goal from an earlier draft was
dropped once the build showed the aliases were never in the scenariogen Σ (FINDING 10 correction);
`covariance.json` is unaffected by P0.

**P1 — Options intelligence.** B1 (signals 1, 2, 4, 7, 8) → B2 (earnings move, RND/tail
probability — the numerically fiddly two) → B3 (Merton/CreditGrades + CDS cross-check) → B4
(scenariogen provider: per-name eqvol, severity multiplier, plausibility hook).
*Exit:* B1 supplies the per-name vol series P0 wanted; B3's implied spreads reconcile to DTCC
single-name CDS within a documented tolerance for ORCL/META, or the divergence is explained; B2
writes Parquet via `storage.write_dataset` and reads back via `storage.query` (trap 3), not
per-ticker CSVs.

**P2 — Basis and style.** F3 registers the three computable bases (swap spread — already derived
inside `bonds_adapter`, just unregistered; CDS-bond; ETF-basket) as risk factors; the TWD XCCY
basis stays unfillable and is handled by F1's liquidity horizon instead. F4 ingests Ken French
FF5+MOM, regresses book factor returns to get real style exposures, and adds a `style:MOM` driver.
*Exit:* "momentum crash" is authorable as a first-class scenario; trade #6 (SMH short vs
single-name longs) shows non-trivial P&L under a dispersion scenario, where today it nets to
~flat; the French ZIP is parsed in memory and persisted clean (trap 1); Σ before/after published.

**P3 — News & events.** A0 CIK map + config + DQ entries → A1 `sec_events_downloader` with the
item-code allowlist and offline-fixture tests → A2 GDELT salience + z-scores → A3
`scenariogen/news/` provider, gate, calibration wiring → **A4 `ToolSource` adapter (not
optional** — without it only the deterministic dual-RAG path sees news; §2.7).
*Exit:* the news window is pinned to the snapshot date, so a re-run reproduces exactly
(reflexivity mitigation, §2.7); CIK coverage below the floor fails loud as
`MarketDataIngestError`; A4 verified by the agentic path actually citing an event.

**P4 — Exit value and funding.** F1 `√LH/√10` horizon scaler by factor class + bid-ask haircut
from the chain `bid`/`ask` already downloaded and currently discarded; F2 a cash/collateral line
(VM + IM proxy) alongside P&L, using `SOFR`−`IORB` from `fred_rates.csv` and a margined/unmargined
portfolio tag.
*Exit:* the LH table is signed off (§8 item 5) **before** merge; the TWD XCCY swap (#26) carries a
60d+ horizon in `taiwan_geopolitical`; stress reports show P&L *and* net cash outflow, which can
have opposite signs.

**P5 — Rates and correlation.** F6 splits nominal into real + breakeven and adds ACM term premium
(`.xls` via `read_bytes` → `BytesIO` → `read_excel`, trap 2); F7 adds implied correlation from the
DSPX/COR series P0 ingested.
*Exit:* the long-duration tech book is decomposed against real rates rather than nominal only; Σ
before/after published; combined mode-(i) factor count from P2+P5 tracked against the ~250-obs
budget (P0 did **not** recover Σ headroom — see §11.8) — if the count gets tight, say so and stop
rather than raising `shrinkage` to hide it.

**P6 — Surfaces.** BFF `/news/*` and `/options/signals/*` read through the façade; UI panels for
Tier-3 headlines, vol surface, skew and RND on trade pages.

**P7 — Gated.** Nothing here starts until its §8 open item is resolved (11.5).

### 11.4 What can run in parallel

After P0, three tracks are independent and touch disjoint files:

- **B-track** P1 → P6(B6)
- **A-track** P3 → P6(A5) — fully independent; the only shared file is the BFF whitelist
- **F-track** P2 → P5, with P4 detachable at any point once its LH table is signed off

Serialize only what P0 gates. If capacity is limited, run B-track first — it is the one with no
external-sourcing risk and no Σ impact.

### 11.5 Decision gates

| §8 open item | Blocks | Consequence if unresolved |
|---|---|---|
| 1 — Alpha Vantage limit / `HISTORICAL_OPTIONS` free-tier status | **B5 only** (P7) | **Resolved: premium** (2026-07-26). B5 not free-buildable; P1 signals stay point-in-time; forward-accumulation gives percentile context for free over ~1y. Never a blocker for the program |
| 2 — CFTC COT reachability | F5 (P7) | Drop F5 |
| 3 — TSMC / SIA / Korea feeds | F9 (P7) | Fall back to the safe subset: EIA + FRED `IPG3344S` |
| 4 — Yahoo / Nasdaq RSS ToS | A5 Tier-3 headlines (P6) | Drop Tier 3; Tiers 1–2 (8-K + GDELT) are unaffected |
| 5 — Liquidity-horizon table | **F1 (P4)** | P4 cannot merge. Desk/governance decision, not a data one |
| 6 — CIK map consistency with ARag | A0 (P3) | Add the drift check in A0 rather than retrofitting |

### 11.6 Regression policy

**P0, P2 and P5 move numbers that already exist.** That is intended, but it must be visible, not
incidental. (P0's actual movement turned out to be tiny and confined to the sensitivity-VaR
surface — §11.8 — because the aliases were never in the scenariogen Σ.)

- **Every Σ-changing phase publishes a before/after**: headline VaR/ES, top factor contributions,
  and the pass/fail of each authored scenario against the plausibility gate.
- **Golden PVs are pricing, not risk**, so P0/P2/P5 should leave them untouched. Confirm it by
  running `governance_runner` rather than assuming it. If a golden does break, that is a signal
  something leaked from the risk side into pricing — investigate the driver first. **Rebaseline
  only when asked**, recording a `rebaseline_history` entry (root `CLAUDE.md`).
- **The plausibility gate is a usable regression signal.** `test_authored_scenarios_pass_the_gate`
  was previously recorded as failing in module notes; as of 2026-07-23 the full suite is green end
  to end, so it is a trustworthy tripwire for P0/P2/P5 rather than a known-bad test to be ignored.
  Those module notes are stale.
- **Reversibility**: mode (ii) and (iii) work (P1, P3, P4) is additive and can be disabled by
  config. Mode (i) work is not — a factor added to Σ changes the estimate for every other factor.
  Land P2 and P5 as single, reviewable steps.

### 11.7 Definition of done, per phase

- `ruff check .` · `mypy` · `pytest -q` green (check `$LASTEXITCODE`; the conftest teardown
  swallows the summary line)
- §10.3 dual-mode checklist satisfied — including the remote-path test pattern from
  `tests/common/test_storage.py` (fsspec `memory://`, no network, no real S3)
- New feeds in `configs/marketdata/dq_gate.json`; `.provenance.json` sidecars emitted
- New runners in `get_job_runner()` **and** the BFF `_WHITELIST`
- Design doc updated: the phase marked delivered, with what was measured — not just what was built

### 11.8 P0 — delivered 2026-07-24

**What shipped.**
- **B0 data.** Added the 7 signal-only underliers (TSM, AVGO, MU, ORCL, AMZN, XLK, SMH) to
  `options_marketdata.json` and 8 new vol indices (VXN, RVX, VXD, VXTLT, VXEEM, DSPX, COR1M, COR3M).
  Ran the ingest: all live. Signal chains are written to **`options/chains_signals/`**, deliberately
  *outside* the pricer's `options/chains/*.csv` snapshot glob (see the trap below).
- **F10 de-alias.** `maps_to` now accepts a **list** of sensitivity keys (`var._maps_to`,
  `var._factor_pnl_columns`, `sizing/trade_risk.py`). The 5 factors that all read `VIX_History.csv`
  (NVDA/MSFT/META/TSLA/SPX) collapse into one `EQVOL_VIX` carrying their summed vega —
  arithmetically identical daily P&L, one Σ/parametric column instead of 5 collinear ones. **QQQ
  moved onto its correct index, VXN** (was proxied to VIX); AAPL stays on VXAPL. Sensitivity-VaR
  factor set: **45 → 41** columns.

**Measured before/after** (aggressive book, 2026-07-10, sensitivity VaR):

| Metric | Before | After | Note |
|---|---|---|---|
| VaR 0.95 / 0.975 / 0.99 | 17.944M / 22.218M / 26.291M | 17.944M / 22.240M / 26.293M | ≤ +0.10%; drift is **only** QQQ→VXN (a real series change), not the VIX collapse |
| Parametric x-check 0.99 | 30.485M | 30.488M | +0.01% |
| `factor_stdev` columns | 45 | 41 | 5 collinear VIX columns → 1 |
| Aligned sim days | 645 | 645 | VXN's shorter history was not the binding factor |
| Named-scenario full-reval P&L | unchanged | unchanged | those use `aligned_panel`, not the factor dict |
| Golden PVs | 30/30 | 30/30 | **0 breaks**; `snapshot_id` rebaselined (below) |

**The correction that the build forced (see FINDING 10 and §9).** The aliases lived **only** in the
sensitivity-VaR factor dict. scenariogen's conditional-covariance Σ and the full-reval VaR read
`factor_history.aligned_panel`, which already collapses equity vol to a **single** `eqvol` column.
So F10 does **not** buy back Σ dimensionality for the mode-(i) work (P2/P5) — that Σ never carried
the duplication. The de-alias is still worth it (honest count, non-singular parametric x-check, QQQ
on its right index), but the original "fix F10 first to free Σ headroom" rationale was wrong and is
withdrawn. Scenariogen plausibility outcomes are therefore **unchanged** by P0 (verified:
`tests/scenariogen` green).

**Dual-mode trap found and closed.** The pricer's content-addressed snapshot globs
`options/chains/*.csv`. Left in `chains/`, the 7 signal-only chains would have entered the
**pricing** fingerprint despite never being priced — churning the pricing `snapshot_id` (and forcing
golden re-baselines) on every signal refresh. Routing them to `chains_signals/` keeps the pricing
snapshot clean; verified the snapshot fingerprints only the 7 priced chains.

**Discontinued feed caught.** CBOE `OIV` returns HTTP 200 but the series ends 2022-11-07 — dropped,
and recorded in-config so a 200 is not re-read as "live".

**Golden re-baseline (with permission).** Re-running the chains ingest re-fetched the 7 priced
chains and appended fresh snapshots; the upsert re-serialized them, moving `snapshot_id`
`d9be3d481c8244bf → 719afc6717bc078b`. **All 30 PVs identical (0 breaks)** — a provenance-only bump,
recorded in `golden_pvs.json` `rebaseline_history`. The prior bytes are unrecoverable (CBOE serves
current quotes only), so restoration was not possible.

**Gate:** `ruff` clean on all changed files (the 4 pre-existing `test_*_real_data.py` I001/B905
findings are not P0's) · `mypy` strict green (252 files) · `tests/pricer`, `tests/marketdata`,
`tests/scenariogen` each exit 0 · `governance_runner` PASS · DQ gate 25 files / 0 block / PASS
(VXN added; the other new feeds stay ungated until their consuming phase reads them).

**Tests added:** `_maps_to` string/list/empty handling; a no-two-EQVOL-factors-share-a-series guard;
and a multi-key-equals-sum-of-single-key P&L-equivalence test (the invariant that makes the collapse
P&L-neutral). All in `tests/pricer/test_risk_scenarios_var_pnl.py`.

**Follow-on noted for P1.** Re-running `--sources cboe_chains` refetches the priced chains too;
a future `--only-signals` selector would let signal refreshes avoid touching the pricing snapshot.
Not required for P0 (the `chains_signals/` split already keeps signal *data* out of the fingerprint;
this is only about avoiding the incidental priced-chain refresh).

### 11.9 P4 — delivered 2026-07-25

**What shipped.** Two mode-(iii) post-processing passes on the materialized stress result — additive,
reversible (`--no-exit-value`), and with no Σ / plausibility / golden-PV impact. Both read data
already on disk and degrade to a no-op when their config is disabled or absent.

- **F1 liquidity horizon + bid-ask haircut** (`src/scenariogen/liquidity.py`). A signed-off
  liquidity-horizon table (`configs/scenariogen/liquidity.json` → `signoff`, Basel/FRTB standard;
  §8 item 5 governance gate cleared) drives a `√(LH / base_horizon)` scaler applied to **losses
  only** — a longer horizon deepens what an unwound position can lose, it does not enlarge a gain.
  Horizon resolves per trade: per-currency override (TWD → **60d**) > CDS-index (20d) > by-trade-type
  > base (10d). A bid-ask haircut is subtracted on top: for equity-option legs from the **real chain
  `bid`/`ask`** (previously discarded — the FINDING 1 headline), OI-weighted; elsewhere a config
  half-spread floor. Cash instruments are haircut on `|base_pv|`, derivatives on `|notional|`.
- **F2 cash / collateral line** (`src/scenariogen/collateral.py`). Net cash outflow = VM
  (`-Σ margined P&L`, netted) + an IM-increase proxy (per-instrument bps of notional × the scenario's
  vol stress from the `eqvol`/`ratesvol` shocks) + collateral-haircut widening. `SOFR - IORB` from
  `fred_rates.csv` is carried as the funding-stress gauge. The margined/unmargined split is a
  scenariogen-side config tag (`configs/scenariogen/collateral.json`), **not** in `portfolio.json`,
  so it never touches the pricing snapshot / golden-PV fingerprint.

New optional fields `liquidity` / `collateral` on `ScenarioStressResult.to_dict()`; both `None` when
disabled, so the report stays byte-identical to pre-P4. Wired in `runner.py` (built once, applied per
result); the run log gains `exit P&L` + `cash out` columns.

**Live check** (aggressive book, 2026-07-10, `taiwan_geopolitical` severe):

| Measure | Value | Reads as |
|---|---|---|
| Mid-market P&L | −173.5M | the scenario's mark |
| **Exit-value P&L** | **−193.5M** | −20.0M liquidity drag (−15.1M horizon on losses, −4.9M bid-ask) |
| Max liquidity horizon | 60d | the USD/TWD XCCY swap #26 |
| **Net cash outflow** | **−85.6M (receive)** | **opposite sign to P&L** — the SMH short (#6) posts a +76M VM gain as semis crash while the unmargined long book craters on the mark |
| Funding gauge `SOFR−IORB` | −12 bp | current funding context |

This is FINDING 1's "mid-mark → exit-value" and FINDING 2's "what you lose ≠ what you must post,
and they can have opposite signs," both realized on the live book.

**Governance.** The LH table is signed off in-config (`signoff` block, desk, 2026-07-25) per §8 item 5
before merge — it reprices the meaning of every published scenario, so the assignment is explicit and
auditable, not incidental.

**Gate:** `ruff` clean · `mypy` strict green (263 files) · `tests/scenariogen` exit 0 (adds
`test_liquidity.py` + `test_collateral.py`: horizon resolution, `√` scaler, loss-only deepening,
haircut base selection, chain-vs-config spread, margined classification, VM sign, and the
opposite-sign cash-vs-P&L invariant). Pricing untouched (no golden change).

**Follow-on.** 3 of FINDING 3's 4 bases are stressable (P2) and the 4th (TWD XCCY basis) remains
unfillable free — but the TWD swap's illiquidity is now at least *carried* via its 60d horizon here.
The IM-increase is a bps-of-notional × vol-stress proxy, not a SIMM/CCP re-simulation; adequate as a
cash-line indicator, and the natural upgrade if a margin model is ever added.

### 11.10 P5 — delivered 2026-07-26

**What shipped.** The two mode-(i) rate/correlation upgrades. F6 costs Σ dimensions (new driver
factors); F7 costs **zero** (it modulates an existing knob). Both are scenariogen-only and leave the
full-reval VaR panel byte-identical; golden PVs untouched (governance PASS 30/30).

- **F6 real / breakeven / term-premium drivers.** New ingest: FRED `DFII10`/`T10YIE`/`T5YIFR` →
  `rates/fred_real_rates.csv` (a `FREDDownloader` subclass keyed on a new `sources.fred_real_rates`),
  and NY Fed ACM `ACMTP10` → `rates/nyfed_acm_term_premium.csv` (new `NYFedACMDownloader`; **trap 2**:
  the binary `.xls` is read as bytes → `io.BytesIO` → `pd.read_excel`, never a raw-path read). Both
  land in **separate** files from the pricing-adjacent `fred_rates.csv` (snapshot discipline, cf. P0's
  `chains_signals/`). `factor_history._rates_decomp()` turns them into three DRIVER-ONLY factors
  `real:DFII10` / `be:T10YIE` / `tp:ACMTP10` (decimal daily changes) under the existing `include_style`
  flag — no bump transform, like `style:`/`basis:`. New plausibility class `rates_decomp` (max 300bp);
  authoring accepts the keys; new scenario `real_rate_repricing` (a +75bp real-rate shock + 40bp term
  premium). Live: the driver propagates to `ir:DGS10` +52bp **and** NVDA −9.7% / SMH −19.3% **and**
  bonds −$35M — equity (−$69M) and bonds falling *together*, the 2022 co-move a nominal-only factor
  set cannot express (FINDING 6), realized on the book.
- **F7 implied-correlation regime switch.** `implied_corr.py` reads the current CBOE 3-month
  implied-correlation percentile (`COR3M`; `DSPX` supported, inverted as it is dispersion) and maps it
  onto the Ledoit-Wolf `shrinkage`: elevated correlation → low shrinkage (propagate broadly, systemic);
  low → high shrinkage (idiosyncratic). Band `[0.4, 0.65]` centered on the fixed 0.5 so authored
  scenarios stay promotable at both ends. The catalog resolves the regime, uses its shrinkage, and
  stamps `{source, percentile, regime, shrinkage}` into the covariance provenance. `enabled: false` /
  absent series → the fixed config shrinkage, byte-identical to pre-F7. Costs **zero** Σ dimensions.
  Today's read: `COR3M` at the 3.6th percentile → **idiosyncratic** → shrinkage 0.64 (correctly damps
  propagation in a historically low-correlation market).

**Σ before / after** (aggressive book, 2026-07-10, scenariogen `include_style` Σ):

| Metric | Before (P2) | After (P5) | Note |
|---|---|---|---|
| Σ factors | 33 | **36** | +`real:DFII10`, `be:T10YIE`, `tp:ACMTP10` (F6). F7 adds none |
| Aligned obs | 250 | 250 | ACM/real are daily & current → window not shrunk |
| obs / factor | 7.6 | **6.9** | well above the ~3.6 degenerate floor; **budget not tight** |
| Shrinkage | fixed 0.5 | **0.64 (regime)** | F7 market-implied (idiosyncratic today) |
| Authored scenarios promotable | 21/21 | **22/22** | +`real_rate_repricing`; all promotable at the 0.4 systemic floor, live 0.64, and fixed 0.5 |
| Full-reval VaR panel | 31 factors | 31 factors | unchanged (drivers are scenariogen-only) |
| Golden PVs | 30/30 | 30/30 | pricing untouched |

Per the §11.6 budget discipline: 36 factors / 250 obs is comfortable; if F-track ever pushes the
count where obs/factor approaches the degenerate range, the move is to stop adding factors, not to
raise `shrinkage` to hide it.

**Gate:** `ruff` clean (changed files) · `mypy` strict green (266) · `tests/scenariogen` +
`tests/marketdata` green (adds `test_implied_corr.py`, `test_rates_decomp.py`,
`test_nyfed_acm_downloader.py`) · DQ gate 31 files / 0 block / PASS (F6 files gated) · governance PASS.

### 11.11 P6 (B6) — delivered 2026-07-26

**Scope.** P6's **B6** half (option-signal surfaces). The **A5** half (news `/news/*` + Tier-3
headline panel) is **not** built — it depends on P3's SEC-8-K/GDELT ingest, which is unstarted — so
P6 is partial (◐): everything with data behind it now ships; the news panels wait on P3.

**What shipped.** A read-only surface over the P1 option-signal feeds, no `src/` changes.

- **BFF** `ui/bff/app/options_signals.py` + 4 routes in `main.py` (the shared route table): `GET
  /api/options/signals` (name catalog — latest per-underlier summary + availability flags), `.../credit`
  (option-implied credit across names, DD-ranked), `.../{name}` (full latest row + short history +
  implied credit), `.../{name}/rnd` (the risk-neutral density grid + tail probs). Direct file reads
  over `option_signals.csv` / `rnd_curves.csv` / `credit_implied.csv`, mirroring `marketdata.py`
  (mtime-keyed cache; empty payload when the signals are absent).
- **UI** a new **Market Signals** section (`ui/app/src/features/signals/`, nav under Subsystems):
  a **Signal Catalog** (per-name ATM IV, 30d/90d term ratio + inverted badge, 25Δ skew, VRP,
  positioning, expected move, RND P(−20%), and option-implied DD), a per-name **detail** (stat tiles +
  the **RND chart** — pdf area with spot / ±expected-move marklines — + the five tail-probability
  cells + an implied-credit panel + a signal-history line chart), and an **Implied Credit** table
  ranked by distance-to-default. Each equity-option **trade page** links to its underlier's signals
  ("what the option market is pricing").

The catalog frames DD as the robust credit signal (ORCL DD ~2.5 vs the mega-caps' 8–15) and the
RND tail probabilities as exactly what the scenario plausibility gate grades an equity shock against —
closing the loop from the B4 market-implied check back to a visible surface.

**Gate:** FE `tsc` typecheck + `vite build` green (737 modules) · `vitest` 16 green · `tests/bff` 50
green (adds `test_options_signals.py`) · BFF endpoints verified live (14 names, ORCL riskiest DD 2.54,
NVDA RND 400 pts). `ui/` is outside the `src/*` ruff/mypy gate by design.

**Remaining for full P6:** A5 — news surfaces — after P3 builds the 8-K/GDELT ingest.

### 11.12 P3 (A0–A4) — delivered 2026-07-26

**What shipped.** The news-and-events A-track: a pre-classified, legally-mandated, low-volume event
stream (SEC 8-K item codes) + GDELT intensity, entering scenariogen as a **numeric feature with a
citation** — a clamped multiplier on the ARag idiosyncratic leg — never as retrieved text. No third
RAG. Additive / mode-(ii): no Σ cost, golden PVs untouched. **A5** (news UI surfaces) is the P6/A5
half and stays pending.

- **A0 CIK map** (`src/marketdata/news/cik_map.py`) — resolves the portfolio's 9 domestic 8-K filers
  to CIKs from the authoritative SEC `company_tickers.json`, with a **coverage floor** (fail-loud
  below it) and a **drift check** against the in-repo bond-issuer CIKs (both derive from the same SEC
  file, §8 item 6; a self-contained guard, since ARag's CIK map lives in a SQL `entity_master` table,
  not a portable file). → `data/marketdata/news/cik_map.csv`.
- **A1 SEC 8-K events** (`sec_events_downloader.py`) — per-CIK submissions JSON filtered to Form 8-K
  with allowlisted item codes (§2.2), each weighted by the config item weights; fail-loud floor.
  Live: **206 allowlisted events across 9 filers** (~80/yr, matching the §2.1 estimate). →
  `sec_events.csv` (`ticker, cik, filing_date, form, item_codes, accession, doc_url, weight`).
- **A2 GDELT salience** (`gdelt_salience_downloader.py`) — per-name daily article-volume + tone with
  63-day z-scores from the `timelinevolinfo`/`timelinetone` history. **Graceful** (GDELT throttles at
  1 req/5s, returning a text notice at HTTP 200): a name that fails simply contributes no rows, and an
  absent feed means news runs on the 8-K spine alone. → `gdelt_salience.csv`.
- **A3 consumption** (`src/scenariogen/news/`) — `NewsEventProvider` reads both feeds **point-in-time**
  to the run's as_of (reflexivity mitigation, §2.7), `mapping.py` composes the clamped multiplier
  `clamp(1 + β_event·event_score(age-decayed) + β_tone·(−tone_z), 0.8, 1.5)`, and `gate.py` holds news
  out on a stale or thin feed. Wired into `dualrag.py`: the multiplier scales the ARag idiosyncratic
  leg — `final(eq:NVDA) = systematic(macro) + idiosyncratic(ARag × news_multiplier)` — with the
  accessions + tone-z recorded in `overlay_sources`/`driver_sources` for the Trace Explorer. Two direct
  wins: a **5.02** (officer departure) fires `keyman_nvda` as a *live* trigger; a **2.02** date
  populates the earnings calendar. `None` / held gate → a no-op (byte-identical to pre-P3).
- **A4 agentic reach** (`news/tool_source.py`) — a local CSV-backed `ToolSource` (`list_tools`/
  `call_tool`) exposing a `news_events` tool, so the agentic `llm/` path cites the same events without
  an MCP server (the one integration point single-project placement costs, §2.4 — hence not-optional).

**Live check** (as_of 2026-07-10): gate OK (newest 8-K 1d old, 9 names) · **5.02 keyman triggers fire
for NVDA/MU/ORCL/MSFT/META/AAPL** · NVDA multiplier **1.226** (5 in-window events incl. 5.02 + tone
+0.07) · earnings calendar populated. The clamp holds a governed baseline: news modulates, never
invents.

**Gate:** `ruff` clean (changed files) · `mypy` strict green (276) · `tests/scenariogen` +
`tests/marketdata` green (adds `test_news.py`, `test_news_downloaders.py`, dual-RAG news-scaling
tests) · DQ gate 33 files / 0 block / PASS (both news feeds gated) · governance PASS 30/30.

**Note on placement (§9 confirmed).** 8-K *metadata* landed in this repo inside the content-addressed
snapshot; the ARag drift check validates against SEC rather than reaching into ARag's DB. If 8-K
*text* is ever wanted, the ingest migrates to ARag and `NewsEventProvider` repoints at `AragClient`
(the documented "when to revisit" trigger).

**Remaining:** A5 (news UI — BFF `/news/*` + Tier-3 headline panels) closes P6; and the P7 gated items
(B5, F5, F8, F9), each blocked on a §8 open item. With P3 done, the whole non-gated program is
complete bar the A5 surfaces.

### 11.13 P6 (A5) — delivered 2026-07-26 — **P6 complete**

**What shipped.** The news UI surfaces — the A5 half of P6, and the last non-gated phase. Read-only
over the P3 feeds, no `src/` changes. **Tier-3 display headlines (Yahoo/Nasdaq RSS) are dropped** at
the §8 item-4 ToS decision gate; Tiers 1–2 (8-K + GDELT) are what ship.

- **BFF** `ui/bff/app/news.py` + 3 routes in `main.py`: `GET /api/news/events` (the 8-K item-code
  stream, newest first, item codes decoded to labels, optional `?ticker=`), `.../calibration?as_of=`
  (the *resolved* scenariogen calibration — gate, per-name multiplier + legs, 5.02 key-man triggers,
  2.02 earnings — by reusing `NewsEventProvider` so the UI shows exactly what a scenario run would),
  and `.../{ticker}` (per-name events + GDELT salience series + the resolved leg). Built from the feeds
  via `data_dir()` (dual-mode safe; mtime-keyed cache; empty payload when absent; config-optional).
- **UI** a **News & Events** tab under Market Signals (`ui/app/src/features/signals/NewsEvents.tsx`):
  a **calibration panel** (gate badge + a highlighted live-trigger banner when a 5.02 fires + a
  per-name table: multiplier, event score, tone-z, item chips, next earnings) and an **8-K event feed**
  (filterable by name, item chips coloured — 5.02 in danger tone — with what-happened labels, weight,
  and a link to the SEC filing by accession). The per-name signal page gains a **Recent 8-K events**
  card, closing the loop from a key-man trigger to the filings behind it.

**Live check** (as_of 2026-07-10): `/events` 206 across 9 names · `/calibration` applied, 5.02 fires
for NVDA/MU/ORCL/MSFT/META/AAPL, NVDA multiplier 1.226 · `/NVDA` 22 events + 176 salience rows.

**Gate:** FE `tsc` + `vite build` green (738 modules) · `vitest` 16 · `tests/bff` green (adds
`test_news.py`) · `ruff` clean on the BFF module · endpoints verified live. `ui/` is outside the
`src/*` gate by design.

**Program status: the entire non-gated market-intelligence program (P0–P6) is delivered.** Only the
P7 gated/optional items (B5 AV backfill, F5 crowding, F8 GPR/EPU, F9 throughput) remain — each blocked
on a §8 open item (a rate-limit / reachability / ToS confirmation), a decision, not a build.

### 11.14 P7 — item-by-item (F8 delivered 2026-07-26)

Re-probed the P7 gates before building. Outcome: **F8 buildable now** (GPR + EPU both 200); F9
buildable on the safe subset (FRED `IPG3344S` 200, EIA already on disk); F5 partial (FINRA
short-interest 200, but CFTC COT still `000`); B5 stays gated (no free Alpha Vantage key here).

**F8 — GPR/EPU geopolitical driver — delivered.** Gives `taiwan_geopolitical` the quantitative
trigger its narrative already named but had no data behind (FINDING 8). Mode-(ii): a clamped severity
multiplier on the whole scenario shock + a live-trigger flag; no Σ cost, golden untouched.

- **Ingest** `src/marketdata/stress/geopolitical_downloader.py` + `sources.gpr`/`epu` in the stress
  runner: the Caldara-Iacoviello **daily global** GPR (`GPRD`) and the **monthly country** file
  (`GPRC_TWN`/`GPRC_CHN`) — both binary `.xls`, **trap 2** (bytes → `BytesIO` → `read_excel`) — plus
  the daily US EPU CSV. **Data-structure correction:** the design assumed *daily* country sub-indices,
  but the daily file is **global only**; the Taiwan/China sub-indices are **monthly**. Both files are
  ingested (`gpr_daily.csv`, `gpr_country.csv`, `epu_daily.csv`). Fail-loud floor on GPR; EPU graceful.
- **Consumption** `src/scenariogen/geopolitical.py` — `GeopoliticalProvider.resolve(series, as_of)`
  computes, PIT over all history ≤ as_of, `multiplier = clamp(1 + β·(2·percentile − 1), 0.85, 1.4)`
  and `trigger = z ≥ 1.5 or percentile ≥ 0.9`. A scenario tagged `"geopolitical": {"series": ...}` in
  `scenarios.json` (taiwan → `GPRC_TWN`) has its whole shock scaled by the multiplier via a new
  `severity_multiplier` on `catalog.materialize`/`definition` (it multiplies the tier magnitude, so it
  acts exactly like a tier on the drivers); the resolution is stamped on `ScenarioStressResult.
  geopolitical`. Untagged / feed-absent / disabled → a no-op (multiplier 1.0), byte-identical.
- **Live** (as_of 2026-07-10): `GPRC_TWN` is at the **91st percentile (z=1.34)** — Taiwan risk is
  structurally elevated in 2026 — so the trigger fires **live** and the scenario is amplified **×1.32**;
  all three tiers stay promotable. A calm regime would damp only to `min_scale` 0.85, keeping the stress
  test substantial.
- **Gate:** ruff clean · mypy strict green (278) · `tests/scenariogen` + `tests/marketdata` green (adds
  `test_geopolitical.py`, `test_geopolitical_downloader.py`) · DQ gate 36 files / 0 block / PASS ·
  governance PASS 30/30.

**F9 — physical throughput — delivered.** Corroborates the AI-capex thesis with hard data and sizes
`ai_bust` by it — the **inverse** of F8: *decelerating* throughput (the thesis breaking) amplifies the
bust, accelerating throughput damps it. Reuses the F8 machinery (severity multiplier + a
`ScenarioStressResult.throughput` provenance field). Safe subset only (design §11.5 gate 3): the
unverified TSMC/SIA/Korea feeds are skipped.

- **Ingest** a `FREDMacroDownloader` subclass (`sources.fred_throughput`) pulls **FRED IPG3344S**
  (semiconductor industrial production) → `stress/fred_throughput.csv`, kept out of the credit factor's
  file; the **EIA datacenter-region power demand** already on disk (previously ingested but unconsumed,
  FINDING 9) is now read too.
- **Consumption** `src/scenariogen/throughput.py` — `ThroughputProvider.resolve(as_of)` computes IPG3344S
  YoY growth, percentiles it over full history ≤ as_of, and maps it to
  `multiplier = clamp(1 + β·(1 − 2·percentile), 0.85, 1.4)` (low growth amplifies) + a thesis flag
  (`intact`/`cooling`/`breaking`) and a trigger (`percentile ≤ 0.15 or yoy < 0`); EIA power YoY is
  carried alongside as corroboration. `ai_bust` is tagged `"throughput": {}` and sized by it; the runner
  now combines any external multipliers (geo × throughput) into the one `severity_multiplier`.
- **Live** (as_of 2026-07-10): IPG3344S YoY **+11.5% at the 80th percentile → thesis "intact" → ×0.85
  damp** — semiconductor production is booming, so a bust is *less* likely and `ai_bust` is scaled down.
  (Contrast F8: taiwan amplified ×1.32. The two calibrations pull opposite ways, each correctly.)
- **Gate:** ruff clean · mypy strict green (279) · `tests/scenariogen` + `tests/marketdata` green (adds
  `test_throughput.py`) · DQ gate 37 files / 0 block / PASS · governance PASS 30/30.

**F5 — crowding — delivered (scoped).** Crowding is a positioning *state* Σ can't represent (FINDING 5).
**Decision:** the design's gate 2 says "drop F5 if COT unreachable" (it is, `000`), but COT was only one
of three F5 sources — FINRA short-interest was always verified. So the **verified subset ships** (as F9's
"safe subset" did): FINRA days-to-cover as a per-name crowding multiplier; the dead COT and the heavier
13F holder-concentration leg are dropped. **User-approved** deviation from the literal gate.

- **Ingest** a new `crowding/` asset class (`short_interest_downloader.py` + runner): FINRA consolidated
  short interest per portfolio name (a POST query per symbol) → `crowding/short_interest.csv` (settlement
  date, short position, ADV, **days-to-cover**). Fail-loud floor.
- **Consumption** `src/scenariogen/crowding.py` — `CrowdingProvider.resolve(as_of)` percentiles each name's
  days-to-cover over its own history ≤ as_of and maps it to `multiplier = clamp(1 + β·(2·pct − 1), 0.8,
  1.4)` (crowded amplifies). It wires into `dualrag.py` **composed with news** on the ARag idiosyncratic
  legs — `idiosyncratic = ARag × news × crowding` (the shared provenance key is now `calibration`). The
  **#6 SMH is a short**, so a short-listed name at/above `squeeze_percentile` is flagged as *squeeze* risk
  — the upside tail a long-book framework misses.
- **Live** (as_of 2026-07-10): NVDA + TSLA short interest at their own historical highs → ×1.30 (crowded);
  MU low → ×0.85; SMH (#6 short) at its median → no squeeze flag today, but the asymmetry fires if SMH
  short interest spikes. Dual-RAG-mode effect; unit-tested deterministically (provider + composition).
- **Gate:** ruff clean · mypy strict green (283) · `tests/scenariogen` + `tests/marketdata` green (adds
  `test_crowding.py` + a dual-RAG composition test) · DQ gate 38 files / 0 block / PASS · governance 30/30.

**B5 — Alpha Vantage chain-history backfill — gate RESOLVED (premium), stays deferred.** A real free AV
key *was* provisioned and tested (2026-07-26): `HISTORICAL_OPTIONS` returns `{"Information": "This is a
premium endpoint…"}`. The endpoint is **premium**, so B5's AV backfill is **precluded by the §0
100%-free-data constraint** — this is now a *resolved* gate, not an open one: the free path does not exist.
The documented fallback (§3.4 option 1 — the **weekly chain upsert forward-accumulates** the signal
snapshots) is already running, so per-name percentile context becomes available for free as ~1y of
snapshots build, with **no build needed**. Building the AV backfill would require relaxing the free-data
constraint to a paid plan (a deliberate scope change, not taken).

**P7 status: F8, F9, F5 delivered; B5 resolved-gated (premium endpoint, free path does not exist). The
market-intelligence program is complete** — every phase P0–P7 is either delivered or, for B5 alone,
correctly closed as not-free-buildable, with its forward-accumulation fallback already running.
