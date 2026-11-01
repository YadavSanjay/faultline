# Portfolio Rebalance Review: Real-World Balance

A critical, real-world review of the 30-trade AI book's **composition** — concentration,
hedge quality, and diversification — with a proposal to make it *slightly more balanced*
without dropping any instrument, plus new positions a real advanced hedge-fund desk would
carry. Complements [`portfolio_construction_design.md`](portfolio_construction_design.md),
which covers the *sizing mechanics*; this doc covers *what the sizing produces and how to
improve it*.

Date: 2026-07-26
Status: **IMPLEMENTED — backlog closed (Part 1 + Part 2 + N5 + realized risk-share cap §11; N6 data-deferred, N8/N9 deferred).** Part 1 built the `core` (Aggressive-Balanced) profile
and promoted it to the active default. Part 2 added 6 diversifier sleeves (CEG/VST AI-power, GOOGL
hyperscaler, GLD gold, an SPX convexity put, a 1Y-UST liquidity buffer) → 36 trades, and gave the
sizing optimizer a scale-to-budget step so every book respects its VaR budget. **N5** then added a
beta-neutral INTC/AMD semis dispersion pair → **38-trade book** (with own factors CEG/VST/GOOGL, a
follow-on refinement). Everything is repriced, risked, scenario-tested, golden-rebaselined, and
narrated — full gate green. See §3
(re-sizing), §6 (Part 1 shipped), and §7 (Part 2 shipped).

Sources (grounding): [`configs/pricer/sizing.json`](../../configs/pricer/sizing.json) ·
active book [`src/pricer/portfolio/portfolio.json`](../../src/pricer/portfolio/portfolio.json)
(= the `core` variant, 38 trades) · risk/sizing numbers from the sizing report
`data/pricer/sizing/2026-07-10/sizing_latest.json` (run `c22731d8-74f3-47a8-a04c-d43c81576c97`,
valuation 2026-07-10, $1bn NAV).

---

## 1. What the book actually is

Stripped to its risk, the live **aggressive** book is **one bet — long US AI-semiconductor
equity beta** — wrapped in a large but risk-inert bond / rates / credit / FX carry overlay.

| Metric (aggressive, 2026-07-10, $1bn NAV) | Value |
|---|--:|
| Total PV | $1.565bn |
| Gross leverage | 3.48× |
| 1-day 99% VaR | $26.9mm (2.7%) |
| ES 97.5% (economic capital) | $26.9mm |
| **Worst stress — AI capex bust** | **−$159.6mm (−16%)** |
| Credit blow-out | −$127.4mm |
| Taiwan geopolitical | −$104.2mm |
| AI capex boom (upside) | +$110.3mm |
| Expected return / Return-on-VaR | +4.0% / 1.60 |

**Headline finding:** the worst stress loss is **~6× the reported ES**. That gap is the
signature of factor concentration — normal-times VaR looks moderate, but a regime shift in
the single factor the book is exposed to is catastrophic and largely un-hedged.

**Risk-share concentration** (share of the $26.9mm VaR):

| Trade | Risk share |
|---|--:|
| MU (long) | +29.2% |
| NVDA (long) | +28.9% |
| TSM (long) | +25.8% |
| XLK (long) | +23.1% |
| AVGO (long) | +18.4% |
| SMH (short) | −33.9% |
| AAPL LEAPS / TSLA straddle / MSFT | +4.1 / +3.4 / +1.4% |
| All bonds + rates + credit + FX (22 trades) | ≈ ±2% each, ~nil net |

The five semis longs sum to **+125% of gross VaR** (SMH short offsets to a net ~91%). The
22 non-equity trades hold ~$2bn of the $3.5bn gross notional yet contribute almost nothing
to VaR — while generating most of the *stress* losses.

## 2. Issues (real-world lens)

- **I1 — Thematic monoculture.** Every directional name is AI *hardware / picks-and-shovels*
  (NVDA, TSM, AVGO, MU, XLK, SMH, + AAPL/MSFT/TSLA options). Zero application-layer /
  monetization exposure, zero AI-power / infrastructure (the dominant 2025-26 AI narrative),
  no non-US-tech risk premium. MSFT/AMZN/META appear only as *bonds* (no equity upside).

- **I2 — Hedges don't hedge the real risk, and are thinned when concentration is highest.**
  In `aggressive`, `hedge_scale = 0.5`: SPX puts, QQQ collar, NVDA protective put, the rate
  payer-swap/swaption, and the issuer CDS are all cut to half — while short-vol / carry
  sleeves are levered to 4×. The book **sheds convex protection and piles on negative-skew
  carry**. Hence the capex **bust (−$160mm) dwarfs the boom (+$110mm)**: the book is
  *negatively* convex to its own core theme — the opposite of how a concentrated thematic
  long should be run.

- **I3 — "Diversifiers" that are the same trade in disguise.** CDX.NA.IG sell-protection
  levered 4× to $200mm (long credit / short-vol / risk-on) and USD/JPY carry levered 4× to
  $200mm (negative-skew; JPY spikes in risk-off) are both **correlated with the equity longs
  in the left tail**. Stacking short-vol credit + FX carry + thinned equity puts = three
  negatively-skewed bets on top of a concentrated long — correlated tails, not
  diversification.

- **I4 — Sizes set by the notional cap, not conviction.** `binding_constraint = notional_cap`
  for **all** profiles; the VaR budget never binds. **Ten of 30 trades sit exactly at the
  $200mm (20%) cap** (XLK, SMH, TSLA straddle, META put-spread, AAPL & AMZN bonds, UST-2Y,
  Bermudan, CDX, JPY fwd). Half the gross notional is a cap artifact.

- **I5 — Duration / credit asymmetry.** Long-end corporates (ORCL '53, META '54) + AAPL/AMZN/
  USTs levered to ~$1bn long-duration, long-credit, while the payer swap and payer swaption
  (rate-rise hedges) are cut to 0.5×. Rates sell-off −$65mm falls mostly on unhedged long-end
  DV01.

- **I6 — Single-name event risk maxed out.** The TSLA 1-month ATM straddle is levered **5× to
  the $200mm cap** despite a *negative* expected return (theta bleed) — a max-size event/vol
  bet in the most headline-driven single name.

- **I7 — Taiwan single point of failure.** TSM ~$155mm long + the supply chain (NVDA/AVGO/
  AAPL all fab in Taiwan). The USD/TWD CCS hedges **only FX**, not the equity gap /
  geopolitical wipeout — Taiwan stress still −$104mm.

- **I8 — No ballast, no dry powder, no real convexity budget.** No gold / real-rate hedge, no
  dedicated (un-thinned) tail sleeve, no cash/T-bill buffer behind a 3.5× gross book.
  High-beta long (portfolio β ≈ 1.5+) with nothing that reliably rallies in a crisis.

## 3. Re-sizing — DELIVERED (same 30 trades, more balanced)

Done purely through the sizing mandate ([`configs/pricer/sizing.json`](../../configs/pricer/sizing.json))
— no trade dropped, none added. A new **`core` (Aggressive-Balanced)** profile was added and
promoted to the active default; all five books stay selectable via `--book`.

| Mandate dial | Aggressive | **core (shipped)** | Effect |
|---|--:|--:|---|
| VaR budget % | 2.5% | **2.0%** | smaller daily risk |
| Concentration cap (risk) | 25% | **15%** | flatter budget shares |
| Notional cap % | 20% | **12%** | ends the $200mm-cap pin (now $120mm) |
| Hedge scale | 0.5 | **0.85** | restore downside / rate / issuer protection |
| Tilt power | 1.0 | **0.7** | spread risk across more names |
| Gross cap | 5.0 | 3.5 | still an aggressive book |

**Delivered numbers** (both books freshly sized on current data, $1bn NAV, 2026-07-10):

| Metric | Aggressive | **core** | Change |
|---|--:|--:|--:|
| Gross leverage | 3.68× | **2.74×** | −26% |
| 1-day 99% VaR | $30.9mm (3.1%) | **$24.3mm (2.4%)** | −21% |
| Book PV | $1.69bn | **$1.25bn** | −26% |
| AI capex-bust stress | −$187.8mm | **−$129.0mm** | −31% |
| Credit blow-out | −$154.4mm | **−$107.2mm** | −31% |
| Taiwan geopolitical | −$126.4mm | **−$86.3mm** | −32% |
| Rates sell-off | −$77.9mm | **−$57.3mm** | −26% |
| Capex-boom upside | +$118.4mm | +$84.5mm | — |
| Expected return / ret-on-VaR | +4.6% / 1.83 | **+3.2% / 1.62** | — |

**Hedges restored** (0.85×): NVDA put $35mm (was $20.5mm), QQQ collar $62mm (was $36.5mm),
payer swap + 5Yx10Y swaption $42mm each (was $25mm), issuer CDS $42mm (was $25mm).
**Negative-skew carry de-levered** to the 12% cap: CDX-sell $120mm (was $200mm), JPY carry
$120mm (was $200mm), TSLA straddle $120mm (was $200mm). The independent `risk_runner` on the
core book reconciles (capex-bust −$129.2mm, VaR99 ~$23mm full-reval, backtest green).

**Honest caveat — RESOLVED (2026-07-27, §11).** The original `concentration_cap` bounds only the
*intended directional* risk budget (RC over the directional-only sub-covariance), so the single
highest-vol name (MU) still took ~24% of the *realized net* risk pie once the hedges compressed net
vol. This is now fixed by a `risk_share_cap` optimizer step that caps each single name's realized
component-VaR share of net-book risk directly (core = 18%): MU 23.6% → 18.0%, the top cluster
(TSM/NVDA/MU) evenly at the cap, VaR still at budget. See §11.

## 4. New positions a real AI desk would carry

All mapped to instrument types this system already prices (QuantLib + free data). Two are
flagged as data/model-constrained.

| # | Add | Fills gap | Instrument (free-data priceable?) |
|---|---|---|---|
| **N1** | **AI-power complex** — Constellation (CEG), Vistra (VST), GE Vernova (GEV) longs | I1 — biggest hole; datacenters are power-constrained; lower correlation to silicon, same thesis | `stock` (+ `equity_option`). ✅ |
| **N2** | **Hyperscaler / app-layer equity** — GOOGL or MSFT equity/option long; and/or an app name (PLTR/NOW) | I1 — diversify from capex-supply (semis) to AI *demand/monetization* | `stock` / `equity_option`. ✅ |
| **N3** | **Dedicated convexity sleeve (un-thinned)** — further-OTM SPX/QQQ put tranche + keep the 5Y10Y payer swaption on | I2/I8 — turn negative capex skew positive | `equity_option`, `european_swaption`. ✅ |
| **N4** | **Gold ballast** — GLD long (± a miner) | I3/I8 — genuine risk-off diversifier, low/neg correlation in stress | `stock` (+ option). ✅ |
| **N5** | **Formal semis dispersion / market-neutral pair** (long laggard vs short leader, or single-name vol vs index vol) | I1 — orthogonal alpha; makes the SMH-vs-single-names RV deliberate | `stock` / `equity_option`. ✅ |
| **N6** | **TSM tail hedge** — protective TSM put sized to the geopolitical gap (or trim TSM) | I7 — CCS hedges FX only, not the equity wipeout | `equity_option`. ✅ |
| **N7** | **Liquidity buffer** — T-bill / short-UST cash sleeve (dry powder + margin buffer) | I8 — 3.5× gross with no cash is fragile | `ust_bond` (short bill). ✅ |
| N8 | *VIX call spread* (explicit vol tail) | I3 | ⚠️ VIX options need a non-BS forward-vol model — likely out of current QL scope; SPX put ladder (N3) is the priceable substitute |
| N9 | *TIPS (inflation-linked UST)* — real-rate hedge | I5/I8 | ⚠️ QuantLib prices linkers, but free TIPS coverage needs checking first |

Highest-value, most defensible: **N1 (AI-power), N4 (gold), N3 (real convexity budget),
N7 (cash buffer)** — together they convert a levered one-way AI-hardware bet into a
diversified AI-thesis book with genuine ballast, while keeping the aggressive posture.

## 5. Roadmap

1. **Re-sizing (pure config).** ✅ **DONE (2026-07-26)** — see §6.
2. **New positions (N1–N7).** ✅ **DONE** — N1 CEG/VST, N2 GOOGL, N3 SPX convexity put, N4 GLD,
   N7 1Y-UST buffer (Part 2, §7), and **N5** INTC/AMD dispersion pair (2026-07-27, §8). **N6**
   TSM tail-put is **data-deferred** (§9) — TSM promoted to the priced chain list; the put is added
   once the valuation date reaches a TSM chain. **N8** (VIX) and **N9** (TIPS) are **analyzed and
   deferred** (§10) — N8 on a QL model gap (no VIX-option engine; N3's SPX put is the substitute),
   N9 as a larger build than its diversification value warrants (real-rate risk is measured, not hedged).
3. **Optimizer follow-ups.** ✅ scale-to-budget (Part 2, every book respects its VaR budget),
   ✅ `market_neutral_pairs` (N5, sizes a long/short pair beta-neutral instead of letting the return-tilt
   floor the short), and ✅ `risk_share_cap` (§11 — bounds each single name's realized component-VaR share
   of net-book risk, closing the §3 MU-concentration caveat). **All optimizer follow-ups now shipped.**

Constraints to honor: free market data only; every added position must map to an existing
instrument type with a pricer + trade builder (`portfolio_loader._BUILDERS`); keep the
reproducibility spine (provenance + snapshot) intact.

## 6. What shipped (Part 1 — re-sizing)

- **Config** — `core` profile added to [`sizing.json`](../../configs/pricer/sizing.json);
  `default_profile` promoted `aggressive → core`.
- **Books** — all five variants regenerated on current data by the sizing runner;
  `portfolio.json` is now the core book (gross 2.74×). `portfolio_core.json` registered in
  `_KNOWN_BOOKS` ([`portfolio_loader.py`](../../src/pricer/portfolio/portfolio_loader.py)).
- **Pricing** — default book repriced at 2026-07-10 (PV artifacts refreshed).
- **Governance** — [`golden_pvs.json`](../../configs/pricer/golden_pvs.json) rebaselined to core
  (30 PVs re-marked, snapshot unchanged, `rebaseline_history` entry added); governance PASS. The
  five per-asset `GOLDEN_PV` regression dicts in `tests/pricer/test_{cash_equity,bonds,credit,fx,
  equity_real_data}.py` rebaselined to core (rates is book-independent, unchanged).
- **Risk** — `risk_runner` re-run on core (VaR/ES, stress, backtest green).
- **Scenariogen** — a `--book` flag added to [`runner.py`](../../src/scenariogen/runner.py) for
  parity with the pricer runners (`run_stress` / `run_reverse_stress` thread `book=`); scenarios
  re-run on the core book.
- **Docs** — trade narratives regenerated on the core default; this doc + the construction design doc updated.
- **Gate** — ruff + mypy + full pytest (pricer/scenariogen/bff/marketdata) green.

## 7. What shipped (Part 2 — new positions)

- **6 trades** added to the base book → **36-trade book**: TRADE_31 CEG + TRADE_32 VST (AI-power),
  TRADE_33 GOOGL (hyperscaler), TRADE_34 GLD (gold ballast), TRADE_35 SPX 6M ~85% put (convexity
  sleeve, sized as a hedge), TRADE_36 1Y-UST (liquidity buffer). N5 (dispersion) and N6 (TSM tail-put)
  deferred.
- **Market data** — CEG/VST/GOOGL/GLD ingested from Yahoo into `prices_daily.csv` (free; gitignored).
- **Risk factors** — all four Part-2 equity names now get their **own** factors (`EQ_GLD` / `EQ_CEG` /
  `EQ_VST` / `EQ_GOOGL`, added to `_EQUITY_TICKERS`). (Initially CEG/VST/GOOGL were XLK-proxied via a
  shared `EQ_XLK_PROXY_P2`; refined to own factors on 2026-07-27 — see the design note below.)
- **Sizing** — new `betas`/`growth_tilt` for the 4 names + TRADE_35 flagged as a hedge; the optimizer
  gained a **scale-to-budget** step (re-scale after the notional cap when it trims an offsetting
  short), so all books now hit their VaR budget exactly. **Core = 2.0% VaR, gross 2.74×→2.02×, 36
  trades** — the new sleeves take a share of the budget, spreading risk off the semis concentration.
- **Flow-through** — repriced; `golden_pvs.json` rebaselined (36 PVs, snapshot updated for the new
  price series); governance PASS; risk re-run (VaR ≈ 2.0%, backtest green, GLD factor live);
  scenariogen re-run (`--workers`) on the 36-trade core; 6 new trade-narrative pages + `STRESS_NOTES`;
  30→36-trade doc references updated.
- **Gate** — ruff + mypy + full pytest green.

**Design note — RESOLVED (2026-07-27).** The XLK proxy for CEG/VST/GOOGL was replaced with dedicated
factors (`EQ_CEG` / `EQ_VST` / `EQ_GOOGL`). A detailed re-examination showed the proxy saved **nothing**
— the price series are already on disk for pricing (one shared `prices_daily.csv`), so own factors need
**zero extra market data** and only ~5 lines of config; the trade-risk model goes 36→38 factors. And it
was not merely cosmetic: crediting the names' *real* risk revealed the proxy had **over-sized** the
volatile single-name power names (it treated them as the low-vol XLK basket). With own factors the
sizing corrects to **CEG $79mm→$38mm, VST $79mm→$32mm, GLD $55mm→$66mm** (the true diversifier gains
budget), GOOGL ≈ unchanged (it genuinely is XLK-like). Core stays 2.0% VaR (gross 2.02×→**2.13×** — the
diversification is spent on more exposure at the same budget); **AI-capex-bust tail −$159mm→−$152mm**.
CEG's ~4.5y history (Feb-2022 spin-off) sits inside the 750/250-day risk windows, so no panel
truncation (and `splice_backfill` is available if lookbacks ever grow).

## 8. What shipped (N5 — semis dispersion pair)

- **2 trades** → **38-trade book**: TRADE_37 **INTC long** (the laggard) and TRADE_38 **AMD short** (the
  leader) — a market-neutral single-name semis relative-value pair (long laggard / short leader). Both
  ingested from Yahoo (free); both get **own** factors (`EQ_INTC` / `EQ_AMD`) — a shared proxy would net
  the correlated long/short to ~zero risk and erase the dispersion.
- **New optimizer feature — `market_neutral_pairs`.** A long/short cash pair can't be sized as a neutral
  unit by the return-tilt: it sizes the long up and *floors the negative-carry short* (INTC $53mm long
  vs AMD $21mm short → net +$74mm long, i.e. just more semis beta). The feature pins the short leg to
  `short_ratio × ` the long (config-driven; `0.636 = β_INTC/β_AMD`), overriding that asymmetry. Result:
  INTC $52.7mm long / AMD $33.5mm short → **net beta exposure ≈ $0** (a genuine beta-neutral pair,
  orthogonal alpha rather than net AI-beta).
- **Effect** — core stays ~2.0% VaR (1.94%, gross 2.00×); the pair carries little net risk so the book
  runs just below budget; **AI-capex-bust tail −$152mm→−$148mm** (the pair adds no market beta).
- **Flow-through** — repriced; `golden_pvs.json` rebaselined (38 PVs, snapshot updated for INTC/AMD);
  governance PASS; per-asset test goldens + governance `n_checked` → 38; risk + scenariogen re-run; 2 new
  trade-narrative pages + `STRESS_NOTES`; 36→38-trade doc references updated.
- **Gate** — ruff + mypy + full pytest green.

## 9. N6 (TSM protective put) — data-deferred

**Intent (I7):** the USD/TWD CCS (#26) hedges only the FX on the TSM long (#2), not the equity
gap/geopolitical wipeout; N6 adds a protective TSM put sized to that gap.

**Why it can't be added yet.** TSM's option chain is not priceable at the book's **2026-07-10**
valuation date. CBOE's free feed serves **current chains only** (no history), and TSM was only added
to the (signal-only) chain ingest on **2026-07-24** — so its earliest chain snapshot is 2026-07-24,
*after* the pinned valuation date. The priced underliers (AAPL/NVDA/SPX/…) have 2026-06-17 and
2026-07-10 snapshots because they were captured over earlier weekly runs; TSM's history simply doesn't
reach back that far, and it can't be back-filled. (Alternatives considered and set aside: **trim TSM**
via a per-name cap — addresses the concentration but isn't a put; **advance the whole book to
2026-07-24** — re-dates all 38 trades and needs a SOFR curve at that date, which Pensford may not have.)

**What was done now (2026-07-27).** TSM is **promoted from signal-only to the priced chain list** in
[`options_marketdata.json`](../../configs/marketdata/options/options_marketdata.json) (moved from
`extra_underlyings` to `portfolio` as #39, `status: deferred_data`), so weekly ingests now capture its
chain into `options/chains/` (the pricer's glob) rather than `chains_signals/`. No re-ingest was run, so
the pricing snapshot is unchanged and no golden re-baseline was needed.

**To add the put later** (once the valuation date is ≥ a date with a TSM chain, e.g. after the book
rolls past 2026-07-24): (1) run the options ingest so `chains/TSM_chains.csv` exists at that date;
(2) add `chains/TSM_chains.csv` to [`dq_gate.json`](../../configs/marketdata/dq_gate.json);
(3) add **TRADE_39** — a ~90% 6M American protective put on TSM — to `portfolio_base.json`, sized as a
hedge (`hedge_trades`); (4) re-size, reprice, rebaseline goldens (snapshot changes — TSM chain enters
the fingerprint), re-risk, re-scenario, and add its trade-narrative page.

## 10. N8 (VIX call spread) & N9 (TIPS real-rate hedge) — analyzed and deferred

Both were reviewed for feasibility on 2026-07-27 (free data + QuantLib support) and **deferred** — N8
on a hard model+data wall, N9 as a nice-to-have whose *meaningful* form is a larger build than its value
warrants right now. The analysis is recorded here so the decision is auditable and the re-open path is clear.

### N8 — VIX call spread (explicit vol tail, I3): out of scope, two independent walls

- **Model gap.** VIX options do not price off Black-Scholes on a spot — VIX is not tradeable; the options
  settle on **VIX futures** (forward variance), needing a mean-reverting square-root (Grünbichler-Longstaff)
  or SABR-on-futures model. QuantLib 1.42 has **no VIX/forward-variance option engine** (verified: its
  `*Variance*` classes are Variance-Gamma and Black-variance surfaces — none price VIX options). Building
  one is a genuine modeling project, not a config add.
- **Data gap.** No VIX options or futures are ingested (only the VIX/VVIX *index levels*, used as risk
  factors). CBOE VIX-option chains are current-only, so they would hit the **exact same historical wall as
  TSM** (§9) — unpriceable at the 2026-07-10 valuation date.
- **Already substituted.** The book's **N3 SPX deep-OTM put** (#35, Part 2) is the priceable vol-tail
  convexity — precisely the substitute this review proposed for N8. So the intent (long crash convexity)
  is already carried.

→ **Deferred.** A VIX-option model is not warranted for a nice-to-have while N3 covers the tail.

### N9 — TIPS / real-rate hedge (real-rate ballast, I5/I8): buildable but under-valued vs effort

- **Data we have:** the 10Y TIPS real yield (`DFII10` = 2.32% at 2026-07-10) + breakevens (`T10YIE` 2.24%,
  `T5YIFR` 2.2%) in [`fred_real_rates.csv`](../../data/marketdata/rates/fred_real_rates.csv). **Missing** for
  a *proper* CPI-linked TIPS: the CPI index history (FRED `CPIAUCSL`) and ZC-inflation-swap quotes needed to
  build a QuantLib inflation term structure for principal indexation. QL *has* the machinery (`CPIBond`,
  `ZeroCouponInflationSwap`) — the gap is the inflation-curve inputs.
- **Why the light version isn't worth it.** Pricing a bond's real cashflows on a flat `DFII10` real curve is
  easy and gives a real-yield PV — but for the position to be *sized and risked* as a distinct real-rate
  hedge (rather than duplicating the nominal duration the USTs #20/#21/#36 already carry), it needs a
  **dedicated real-rate factor** wired through four engines: a `shift_real_curve` bump + a `tips_bond` pricer
  emitting a `REAL_DV01`; a `REAL_10Y` factor (reads `DFII10`) in the sizing/sensitivity-VaR config;
  `real:DFII10` promoted from scenariogen-driver-only into the *default* factor panel + a bump so full-reval
  VaR shocks it; and wiring the existing `real_rate_repricing` driver to reprice it. That is a ~8-file
  multi-component build + a full re-size/rebaseline. If instead the TIPS's DV01 is mapped to the *nominal*
  IR key-rate factors (the cheap path), the risk model treats it like another long UST and it adds little
  distinct diversification — self-defeating for a real-rate hedge.

→ **Deferred.** The real-rate risk is already **measured** (the F6 `real:DFII10` scenario driver flags it in
stress), just not **hedged** by an instrument. Re-open by either (a) ingesting `CPIAUCSL` + building an
inflation curve for a proper `CPIBond`, or (b) doing the dedicated-`REAL_10Y`-factor build above so a
real-yield-discounted note is a genuine, distinctly-risked hedge.

**Backlog now:** N1–N5 + N7 shipped; **N6 data-deferred** (§9, groundwork laid); **N8/N9 deferred** (this
section); the realized single-name risk-share refinement is **shipped** (§11). **The rebalance backlog is
closed** — all remaining items are deliberately deferred (data/model-gated), with re-open paths recorded.

## 11. Realized single-name risk-share cap — shipped (2026-07-27)

Closes the §3 "Honest caveat": `concentration_cap` bounds only the **intended directional** risk budget
(the risk-budget solve makes component RC ∝ the budget shares, but that budget is over the *directional-only*
sub-covariance), whereas what a desk actually cares about is each name's share of the **realized net-book**
risk. Because the hedges compress net vol, a name capped at 15% of directional budget (MU) still landed at
**~24% of net risk** — the positive component-VaR shares summed to ~1.37.

**Fix.** A new profile field `risk_share_cap` and a final optimizer step
([`optimizer.py`](../../src/pricer/sizing/optimizer.py) step 7) that flattens any directional (non-hedge)
name whose realized share `RC_j / portfolio_vol` exceeds the cap, then re-hits the VaR budget. The key
property is that the realized RC share is **invariant to a uniform rescale** (RC_j and the vol both scale
linearly), so (a) flattening the *shape* and then re-scaling to budget never re-breaches the cap, and (b) the
re-scale preserves any `market_neutral_pairs` leg ratio (both legs scale together) — so this runs *last*, after
the pair pin, with nothing left to disturb it. Market-neutral pair legs are excluded from the trim (they are
sized as a net-neutral unit, so a leg's gross share is not single-name concentration).

**Result (core, `risk_share_cap` = 0.18).** MU 23.6% → **18.0%**; the top cluster (TSM/NVDA/MU) sits evenly
at the 0.18 cap instead of one name dominating. VaR stays at budget ($20.0mm), and de-concentration lets the
book run at slightly higher gross (2.00× → 2.18×) for ~the same expected return — a strictly better
risk-adjusted shape. Only `core` carries the cap; the more concentrated `aggressive`/`risky` profiles leave it
at the 1.0 (off) default by design. **Flow-through:** re-sized, `golden_pvs.json` + 5 per-asset test golden
dicts rebaselined (MU notional −17%, other directional names ~+10% to fill the freed budget; snapshot
unchanged — position sizes only), governance PASS, risk + scenariogen re-run, 38 trade narratives refreshed,
3 unit tests + gate green.
