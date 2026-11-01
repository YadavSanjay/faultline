# AI-Themed Portfolio: QuantLib Pricing/Risk + Market Data Pipeline

A working design conversation covering:

1. A 30-trade AI-themed portfolio scoped to instruments QuantLib prices reliably
2. Market data requirements per instrument, with vendor cost tiers
3. Re-scoping the data stack for weekly-cadence stress testing (mostly FREE)
4. Concrete inspectable URLs/API endpoints to validate before automating

Date: 2026-05-16 (last revised 2026-05-29 — §4 endpoints validated/corrected; §2–§3 reconciled to those fixes)
Context: Sophisticated investor; pricing & risk system built on open-source QuantLib; ~30 large-value trades; weekly valuation + stress testing (e.g., Taiwan geopolitical, AI capex boom/bust).

> **Source-of-truth note:** §4 (Inspectable URLs) reflects hands-on endpoint validation and is authoritative where it conflicts with the §2 initial vendor inventory. Free-data endpoints change without notice — re-verify before automating.

---

## 1. The Portfolio — 30 Trades Across Asset Classes

> **Update (2026-07-27):** this section documents the **original 30-trade** design (as of 2026-05).
> The live book has since grown to **38 trades** (default profile: **core**); see
> [`portfolio_rebalance_review.md`](portfolio_rebalance_review.md) for the 2026-07-26/27 rebalance
> (core book, +8 trades, realized risk-share cap). The 30-trade enumeration below is preserved as the
> as-designed record.

Universe: NVDA, GOOGL, MSFT, META, AAPL, TSLA, AMZN, ORCL, AVGO, AMD, TSM (ADR), MU.

Everything below is either US-listed or ISDA-standard OTC. Each instrument is one QuantLib has battle-tested pricer/engine coverage for. Intentionally excluded: convertibles (CB class exists but rarely production-ready), autocallables, CDS index tranches, callable Yankee bonds with embedded resets, inflation-linked bond options.

### 1.1 Cash Equity (US-listed) — 6 trades

Pricing in QL is trivial (`Stock` quote + dividend/repo curve). Used as the delta-bearing core and as underliers for the option/CDS book.

| # | Instrument | Notes |
|---|---|---|
| 1 | NVDA common stock — long | Core AI beta |
| 2 | TSM ADR — long | Foundry exposure; FX risk via ADR is implicit |
| 3 | AVGO common — long | Networking/custom silicon |
| 4 | MU common — long | HBM cycle play |
| 5 | XLK ETF — long (hedge basket) | For sector hedging via listed options |
| 6 | SMH ETF — short (relative-value hedge) | Pair vs single-name semi longs |

**QL coverage:** `Stock`, `SimpleQuote`, `YieldTermStructure` for repo/dividends. Greeks via bump-and-revalue.

### 1.2 Equity Derivatives (Listed, US) — 8 trades

QL handles American single-stock options and European index options cleanly. Stick to standard monthly/LEAPS for risk stability.

| # | Instrument | QL engine |
|---|---|---|
| 7 | AAPL Jan-27 LEAPS call (American) | `BaroneAdesiWhaleyApproximationEngine` or `BinomialVanillaEngine<CoxRossRubinstein>` |
| 8 | NVDA 6M ATM put (American, protective) | `FdBlackScholesVanillaEngine` (FDM, good for early exercise + dividends) |
| 9 | MSFT 3M 25-delta call spread | Two `VanillaOption` legs, analytic American engine |
| 10 | META 6M put spread (skew play) | Same; calibrate to listed vol surface |
| 11 | TSLA 1M straddle (vol/event) | American; FDM engine recommended |
| 12 | SPX 3M ATM put (European, portfolio hedge) | `AnalyticEuropeanEngine` (Black-Scholes-Merton) |
| 13 | SPX 6M 90–80% put spread | Same |
| 14 | QQQ 3M collar overlay (American) | American engines as above |

**QL coverage:** `VanillaOption`, `EuropeanExercise`/`AmericanExercise`, `BlackScholesMertonProcess`, `BlackVolTermStructure` (with `BlackVarianceSurface` for the vol grid). Vega/vanna via bump.

> Limitation: QL's local-vol calibration (`LocalVolSurface`) works but you'll want to smooth your listed surface (SVI fit done externally) before feeding it in. For pure Black-Scholes pricing with flat-vol-by-strike/term, you're fine.

> **⚠ Implementation findings (pricer build, 2026-06-18).** Building `src/pricer/ql/equity/` against the real data surfaced two gaps; both are handled:
>
> 1. **The option underliers have no spot or dividend data in the pull.** §1.1/§2.1 assume Yahoo spot + dividends, but the equity pipeline only downloaded the **cash-equity book (#1–6:** AVGO, MU, NVDA, SMH, TSM, XLK); the option names **AAPL, MSFT, META, TSLA, QQQ, SPX have no spot and no dividends** on file (only NVDA overlaps). **Alternative taken:** recover both **spot and the continuous dividend yield from the option chain itself** via put-call parity — the implied forward F(T)=K+e^{rT}(C−P) per expiry, then regress ln F(T) on T (intercept = ln spot, slope = r−q). Validated to ~0.07% against NVDA's known close; recovered yields are realistic (TSLA ~0%, MSFT ~0.6%, SPX ~0.4%). To price the cash-equity book (#1–6) or use exchange settles directly, the equity downloader must be extended to the option underliers. Dividends are modelled as a **continuous yield**, not the discrete schedule implied by the FDM-with-dividends note above — adequate for these tenors, refine with an escrowed-dividend model if needed.
>
> 2. **The chains are a single snapshot (2026-06-17), distinct from the 2026-05-29 rates/bonds valuation date.** The equity book therefore values at the **chain snapshot date**; a single-date full-book valuation requires aligning the snapshots (or accepting a ~19d stale SOFR curve, which the loader flags). Implied vol is read **per-(expiry, strike)** from the chain (skew-aware), which the §1.2 skew/spread trades (#9/#10/#13) depend on; a smoothed SVI/`BlackVarianceSurface` is the production upgrade.

### 1.3 Bonds — 7 trades

All MAG-7 issuers below have liquid, vanilla fixed-rate USD bullet bonds; QL prices these cleanly. Treasuries are the rates hedge.

| # | Instrument | QL class |
|---|---|---|
| 15 | AAPL 4.75% 2035 senior unsecured (CUSIP 037833FB1) | `FixedRateBond` |
| 16 | MSFT 4.10% 2037 (CUSIP 594918BZ6) | `FixedRateBond` |
| 17 | ORCL 5.55% 2053 (long-end credit; CUSIP 68389XCQ6) | `FixedRateBond` |
| 18 | META 5.40% 2054 (issued post-2022; CUSIP 30303M8V7) | `FixedRateBond` |
| 19 | AMZN 4.70% 2032 (CUSIP 023135CR5) | `FixedRateBond` |
| 20 | UST 10Y on-the-run | `FixedRateBond` w/ ActualActual(Bond) |
| 21 | UST 2Y on-the-run (curve hedge) | `FixedRateBond` |

**QL coverage:** `FixedRateBond` + `DiscountingBondEngine` against a SOFR-discounting curve and an issuer spread curve (`ZeroSpreadedTermStructure` or `PiecewiseYieldCurve` from CDS-implied). Risk: KRD via bucketed curve bumps, OAS via `BondFunctions::zSpread`.

> Skip make-whole callables / convertibles — QL has classes but production use is uneven. The bonds above are picked to be straight bullets.

> **⚠ Implementation findings (pricer build, 2026-06-18).** Three gaps surfaced when building `src/pricer/ql/bonds/` against the actual free data; each is handled, but the design as originally written was over-optimistic:
>
> 1. **The free DTCC single-name CDS feed does not yield usable issuer spreads.** §1.3/§2.4/§3.2 assume the corporate Z-spread comes from "CDS-implied spread from DTCC public dissemination." In the downloaded `dtcc_corp_cds_spreads.csv`, `Spread-Leg 1` is **~76% NaN**, and the few non-null values (~1.0) are **upfront prices for misfiltered LBO entities** ("Amentum (Amazon Holdco)", "Atlantic Aviation (KKR Apple Bidco)") — *not* the mega-cap issuers; MSFT has 1 row, AAPL/META effectively none. Single-name IG CDS at usable granularity is the licensed Markit product. **Alternative taken:** the loader still *attempts* a clean extraction (exact issuer-name match, LBO-name exclusions, sane 5–500bp band, min-observation count), then **falls back to a per-issuer Z-spread in `configs/pricer/pricer_config.json`** (`bonds.credit_spread.issuer_fallback_bp`) and emits a warning — mirroring the flat-vol fallback used for swaptions (§1.4). TRACE prints (manual-only) remain the intended mark-validation, not a pricing input.
>
> 2. **A CDS-style spread over SOFR is *not* the right Z-spread, because the SOFR–Treasury swap spread is materially negative.** With SOFR 10Y ≈ 4.00% sitting ~**45bp below** the 10Y UST (4.45%), discounting a corporate on `SOFR + (CDS-like 35bp)` produces a yield *below* Treasuries — unrealistic for IG. **Alternative taken:** the configured fallback values are **Z-spreads over SOFR that already include the negative swap spread** (AAPL/MSFT 60, AMZN 75, META 95, ORCL 130 bp), keeping net spread-to-Treasury positive (~+15 to +85bp). A production build should decompose this explicitly into (Treasury–SOFR basis) + (CDS-bond basis) + (credit spread).
>
> 3. **The free SOFR curve stops at ~10Y, but ORCL 2053 and META 2054 are 27–28Y.** The Pensford forward export (`pensford_sofr_forward.csv`) only reaches ~10Y, so long-dated corporates are currently discounted by **flat-forward extrapolation beyond the last node** (the pricer flags this with a warning). §3.2 already anticipates needing the "Pensford/Chatham published long-end rate for the 30Y node" — that long-end node is **not** in the current data pull and must be added for accurate 20Y+ credit pricing.

### 1.4 Fixed-Income Derivatives — 4 trades

QL's multi-curve framework (post-LIBOR, SOFR-based) is rock-solid here.

| # | Instrument | QL class / engine |
|---|---|---|
| 22 | 10Y SOFR payer IRS (rates hedge for bond book) | `MakeOIS` / `OvernightIndexedSwap` + `DiscountingSwapEngine` |
| 23 | 5Y x 10Y European payer swaption (rate-rise hedge) | `Swaption` + `BlackSwaptionEngine` (lognormal) or `BachelierSwaptionEngine` (normal/SABR) |
| 24 | 2Y x 5Y Bermudan receiver swaption | `Swaption` + `TreeSwaptionEngine` on Hull-White, or `FdG2SwaptionEngine` |
| 25 | 1Y SOFR cap, ATM strike (tail hedge on funding) | `Cap` + `BlackCapFloorEngine` |

**QL coverage:** Calibration via `HullWhite` / `G2++` to a co-terminal swaption grid using `JamshidianSwaptionEngine` for European calibration instruments.

### 1.5 FX Derivatives — 2 trades

Limited to where QL is unambiguously production-ready. MtM-resetting CCS is supported.

| # | Instrument | QL class |
|---|---|---|
| 26 | 5Y USD/TWD non-deliverable CCS (hedge TSM ADR FX) | Built as two `IborLeg`s + FX notional resets; price with multi-curve `DiscountingSwapEngine` and FX-basis-adjusted discount curve |
| 27 | 3M USD/JPY FX forward (carry overlay) | `FxSwap` instrument; trivial discount pricing |

> Skip in QL: FX-TARFs, accumulators, vol-target FX baskets. Library doesn't cover them cleanly.

> **⚠ Implementation findings (pricer build, 2026-06-18).** `src/pricer/ql/fx/` prices both via discount-factor valuation (USD legs on the SOFR curve, foreign legs on a flat foreign curve):
>
> 1. **#26 is simplified to a fixed-for-fixed CCS with notional exchange, not the MtM-resetting two-`IborLeg` NDS above.** A full floating/MtM-reset CCS needs a TWD projection curve (TAIBOR) and a USD/TWD cross-currency basis curve — **neither has a free feed** (EMTA retired, no free TAIBOR). Per §4.6 the USD/TWD **spot is the dominant risk and the rate differential / basis are second-order**, so the build holds the TWD leg on a flat **CBC policy-rate curve (2.00%, a config constant)** with **zero xccy basis**, and values both legs' fixed coupons + notional exchange by discount factors, converting the foreign leg at the current spot. This captures the FX MtM (the hedge's purpose) correctly; a production NDS would add the TAIBOR + basis curves and FX resets.
>
> 2. **FX forwards/forward points are CIP-derived, not quoted.** No free FX-forward-point / NDF-fixing feed survives, so the fair forward is `spot x DF_usd / DF_foreign` (covered interest parity) using SOFR vs the foreign overnight rate (JPY TONAR; TWD CBC). #27 reprices to zero when struck at this fair forward (validated). Spots: USD/JPY from the ECB cross, USD/TWD from Fed H.10 (the CBC interbank fix is the NDF settlement reference for #26).

### 1.6 Credit Derivatives — 3 trades

ISDA-standard single-name CDS is QL's strongest credit instrument. Stick to that.

| # | Instrument | QL class / engine |
|---|---|---|
| 28 | 5Y ORCL CDS — buy protection (issuer hedge for trade #17) | `CreditDefaultSwap` + `IsdaCdsEngine` (ISDA model, par-equivalent upfront) |
| 29 | 5Y META CDS — buy protection (issuer hedge for trade #18) | Same |
| 30 | 5Y CDX.NA.IG on-the-run series — sell protection (carry/diversifier) | Approximated as basket of single-name CDS or as a single `CreditDefaultSwap` against an index-implied flat hazard curve. **Caveat:** QL does not natively price CDX tranches; the *unfunded index* itself is fine modeled as a portfolio CDS. |

**QL coverage:** Hazard rate bootstrap via `PiecewiseDefaultCurve<HazardRate, BackwardFlat>` from CDS quotes, then `IsdaCdsEngine` for both pricing and risk (CS01, IR01, JTD by bumping the hazard curve / recovery).

> Notable exclusion: single-name CDS options. QL has experimental support but not production-grade. If you need vol-on-credit, model it externally.

> **⚠ Implementation findings (pricer build, 2026-06-18).** `src/pricer/ql/credit/` prices all three; two notes:
>
> 1. **QuantLib 1.42 ships no `IsdaCdsHelper`, and `IsdaCdsEngine` rejects non-flat-forward discount curves.** So the `PiecewiseDefaultCurve<HazardRate>` bootstrap above can't be made *ISDA-consistent* via the usual helper (the available `SpreadCdsHelper` bootstraps with the MidPoint model — ~5bp off the ISDA engine, so a par CDS won't reprice to zero). **Alternative taken:** calibrate a **flat hazard rate** by a 1-D root-find so a par CDS (coupon = market spread) reprices to exactly zero *under `IsdaCdsEngine` itself* — self-consistent by construction (fair spread == market spread to <0.5bp). The discount curve is rebuilt as **`PiecewiseFlatForward`** (the only interpolation `IsdaCdsEngine` accepts) from the same SOFR par-OIS quotes. Recovery 40% senior unsecured; CDX.NA.IG modelled as a single CDS vs the index-implied flat hazard, per the §1.6 caveat.
>
> 2. **The free DTCC single-name CDS feed is usable here** (unlike the bonds corp-CDS pull). The credit pipeline's `dtcc_single_name_cds.csv` (SEC path) is well populated — ORCL ~546, META ~44 usable 5Y quotes; CDX.NA.IG ~184k via the CFTC `UPI Underlier Name`. So CDS #28-30 derive real 5Y par spreads from DTCC (ORCL ~175, META ~74, CDX.NA.IG ~55 bp), with only a config fallback for thin windows. **Corollary:** the bonds-book Z-spreads (§1.3 finding, which fell back to config off the sparser `dtcc_corp_cds_spreads.csv`) could be upgraded to derive from this same single-name file — a worthwhile future reconciliation.

### 1.7 Cross-cutting QuantLib build notes

1. **Curves needed:** USD SOFR OIS (discount + projection), USD Treasury par curve, per-issuer CDS hazard curves, USD/TWD and USD/JPY FX-basis-adjusted discount curves, per-name equity dividend/repo curve, per-name equity vol surface.
2. **Risk approach:** For a 30-trade book, bump-and-revalue is fine. Vectorize via `Observable`/`Observer` so a single curve bump reprices everything dependent. Greeks: delta, gamma, vega, IR01 (per bucket), CS01, jump-to-default for CDS, FX delta, theta.
3. **Calibration:** Hull-White / G2++ for swaptions, ISDA flat-forward for CDS, Heston only if extending to vol-of-vol on equity.
4. **Keep OUT of QL pricer (import PV/Greeks externally):** convertibles, equity autocallables, CDS tranches, exotic FX, inflation derivatives.

> **⚠ Implementation findings — SOFR curve construction (pricer build).** Two gaps in "USD SOFR OIS (discount + projection)" surfaced in the actual build:
> - **Front end (<1Y) must be pinned, or the curve mis-states the realized SOFR.** Bootstrapping from par nodes starting at 1Y left the curve's 0–1M forward at ~3.74% while realized SOFR (NY Fed *and* FRED) was 3.63% — a ~12bp error that mis-prices the 1Y cap's near caplets. **Fix taken:** derive **sub-1Y par nodes (1M/3M/6M/9M)** from the Pensford monthly forward grid so the front matches the fixing (now ~3.62%). A cross-source test (`curve front ≈ realized SOFR`) guards this.
> - **Long end (>10Y) is missing from the free data.** The Pensford export reaches only ~10Y, so 20Y+ corporates (ORCL 2053, META 2054) are discounted by flat-forward extrapolation. The 30Y long-end node referenced in §3.2 must actually be sourced (Pensford/Chatham published long-end) to price the long-dated credit book accurately.
> - **(2026-07-11) The forward path was collapsed to a single point → curve degenerated to ~0%.** The committed `pensford_sofr_forward.csv` had only one forward row per snapshot (the archive downloader de-dups on `as_of_date`), so the OIS builder couldn't compound a path and every swap/bond/CDS discounted at zero. The Pensford *archive* also lags (month-end, newest 2026-05-29); the live `/forward-curve` page is backed by a public **Supabase** DB with the full daily path. **Fix:** `scripts/rebuild_pensford_from_supabase.py` rebuilds the file with real monthly paths (current 2026-07-10 + historical backfill) → a real ~4% curve; default valuation date moved to 2026-07-10. See [`01-sofr-multicurve.md`](../pricing/methodology/01-sofr-multicurve.md).
>
> Note also that for SOFR OIS the "discount + projection" split is degenerate — both are the *same* SOFR curve (no tenor basis within an overnight RFR); the pricer keeps the two roles as distinct handles so a genuine basis/XCCY curve can be slotted in later without refactoring.

---

## 2. Market Data Requirements — Initial Vendor Inventory

Costs are USD annual list price (negotiable): **FREE**, **$ (cheap, ad-hoc)**, **$$ (mid, 5–50K/yr)**, **$$$ (institutional, 50K+/yr)**.

### 2.1 Equity book (trades #1–6)

| Data point | Source — FREE | Source — Paid |
|---|---|---|
| Closing prices, OHLC, volume (EOD) | **Yahoo Finance chart API** (confirmed primary — returns OHLCV + adj_close + corporate actions in one call). Stooq: now returns JS-rendered HTML for all automated requests — broken. Nasdaq Data Link (Quandl EOD): free tier, CSV, API key required. | Bloomberg BDL ($$$), Refinitiv ($$$), Polygon.io ($, ~$200/mo), Tiingo ($) |
| Intraday / tick | IEX Cloud (limited free) | Polygon ($), Databento ($$), CBOE/Nasdaq direct ($$$) |
| Corporate actions | Nasdaq Data Link, SEC EDGAR | Bloomberg DVD, Refinitiv |
| Discrete dividend forecasts | — | IHS Markit Dividend Forecasting ($$), Bloomberg DVD ($$$) |
| Borrow / repo rates | — | IBKR SLB, S3 Partners ($$), Hazeltree ($$) |
| ADR ratio + FX (TSM) | NY Fed, SEC | Bloomberg |

### 2.2 Equity options (trades #7–14)

| Data point | Source — FREE | Source — Paid |
|---|---|---|
| Listed option chains | Yahoo (delayed, no greeks), CBOE delayed | OptionMetrics IvyDB ($$/$$$), ORATS ($$, ~$10–25K), CBOE DataShop ($), Polygon Options ($) |
| Implied volatility surface | derivable from chains | OptionMetrics pre-computed; ORATS cleaned surfaces |
| Risk-free / OIS discount | NY Fed SOFR | Same |
| SPX index options | CBOE delayed FREE | OptionMetrics, CBOE DataShop |
| VIX & VIX term structure | CBOE FREE, FRED | Bloomberg |

**Minimum viable:** CBOE DataShop one-shot historical chains (few hundred USD per name-year) + nightly Polygon Options (~$200/mo).

### 2.3 Rates curves & IR derivatives (trades #15–25)

| Data point | Source — FREE | Source — Paid |
|---|---|---|
| SOFR overnight fixings | NY Fed | — |
| Term-SOFR proxy (realized 1M/3M/6M) | NY Fed / FRED compounded SOFR averages (`SOFR30DAYAVG`, `SOFR90DAYAVG`, `SOFR180DAYAVG`, `SOFRINDEX`) — backward-looking | CME Term SOFR (licensed benchmark — not on FRED), Bloomberg |
| SOFR OIS swap rates | Pensford Forward Curve (1Y–10Y, Excel export), Chatham US market rates (view), DTCC/CFTC SDR public feed (derive par nodes from disseminated trades) | ICE Swap Rate ($$), Bloomberg ICVS ($$$) |
| SOFR forward curve / front-end path | Pensford / Chatham published forward curves (FREE) | CME real-time futures ($) |
| UST yields, par curve, CMT | Treasury.gov FREE, FRED H.15 FREE | Bloomberg, Refinitiv |
| UST pricing input | Treasury.gov par yield curve (build discount curve) + TreasuryDirect (reference/CUSIP). Per-CUSIP TRACE prints carry the same access limit as corp TRACE (§4.5) — not needed for pricing | Bloomberg BVAL, ICE Data ($$$) |
| Swaption volatility cube (ATM + smile) | DTCC/CFTC SDR disseminated swaption premiums + MOVE level (proxy) | ICAP/Tullett broker runs ($$$), Bloomberg VCUB ($$$) |
| Cap/floor vol surface | DTCC/CFTC SDR disseminated cap/floor premiums (proxy) | Same |

> No free production swaption-vol cube. For weekly cadence, derive from DTCC/CFTC SDR-disseminated swaption & cap/floor prints (premium + strike + expiry, free under Dodd-Frank Part 43), anchored to the MOVE index level.
> Note: CME's free `CmeWS` settlement endpoints (SOFR futures/options) and ICE's free swap-rate page are now access-restricted (browser-only / no clean API) — replaced above with Pensford/Chatham published curves and the DTCC SDR public feed.

### 2.4 Corporate bonds (trades #15–19)

| Data point | Source — FREE | Source — Paid |
|---|---|---|
| Transacted prices per CUSIP (automated) | No free API — TRACE API requires FINRA broker-dealer membership (TRAQS); FINRA developer API excludes TRACE | ICE BVAL, Bloomberg BVAL ($$$) |
| Transacted prices per CUSIP (manual) | FINRA Bond Center web UI — free, lookup by CUSIP, adequate for weekly mark validation of 5 bonds | — |
| Bond reference data | SEC EDGAR submissions + 424B5 filings (requires `User-Agent` header) | Bloomberg DES, Refinitiv |
| Issuer ratings | S&P/Moody's/Fitch press releases (lagged), EDGAR 8-Ks | Rating agency direct feeds ($$$) |
| Issuer credit spread curve | CDS-implied spread from DTCC public dissemination (covers all 5 issuers) → `ZeroSpreadedTermStructure` in QL | ICE/BVAL spreads, Bloomberg CRVS |

> **⚠ Correction (pricer build):** the "CDS-implied spread from DTCC ... covers all 5 issuers" row is **not borne out by the free feed** — `Spread-Leg 1` is mostly NaN and the populated rows are upfront prices for unrelated LBO entities, not the issuers. The pricer falls back to configured per-issuer Z-spreads. See the Implementation-findings callout in §1.3.

### 2.5 FX & cross-currency (trades #26–27)

| Data point | Source — FREE | Source — Paid |
|---|---|---|
| USD/JPY, USD/TWD spot | ECB cross (JPY), Fed H.10 (TWD + JPY backup) — **not FRED** (FX series 504-timeout, see §4.6) | Refinitiv, Bloomberg, EBS |
| FX forward points (USD/JPY) | CIP-derived from SOFR vs JPY TONAR (BoJ) | Refinitiv WMR ($$), Bloomberg FRD ($$$) |
| USD/TWD NDF forward points | CIP-derived from CBC policy rate + SOFR + CBC spot (`lp-700-2.html`); historical NDF prints via DTCC public dissemination (FX asset class, Part 43) | Bloomberg NDF curve ($$$) |
| Cross-currency basis | — | Bloomberg XCCY ($$$), Tullett/ICAP runs |

### 2.6 Credit derivatives (trades #28–30)

| Data point | Source — FREE | Source — Paid |
|---|---|---|
| Single-name CDS spreads per tenor | DTCC public dissemination (per-trade, daily cumulative S3 files — see §4.7) | **Markit / S&P Global Market Intelligence CDS Pricing** ($$$, ~$50–150K) |
| CDX.NA.IG composition + price | Composition: S&P DJI Index Annex PDF (§4.7); price: DTCC public | Markit ($$$), Bloomberg CDSW |
| Recovery rates | Convention: 40% senior unsecured | Markit recovery quotes ($$$) |
| ISDA standard model | ISDA publishes | — |
| Hazard curve | derived via QL bootstrap | — |

### 2.7 Reference / static (all FREE)

- Holiday calendars: SIFMA/NYSE, Taiwan, Japan — QuantLib ships these
- Day-count conventions, business-day rules — QL built-in
- ISDA 2014 Credit Definitions, ISDA 2021 IR Definitions
- CUSIP/ISIN/RIC mapping — paid ($$); OpenFIGI is FREE for FIGI codes

### 2.8 Stress-scenario specific data

> Concrete endpoints and the full scenario-construction methodology are in §4.9. Sources below are the category summary.

**Taiwan geopolitical:**
- TWD spot history, 10+ years — BIS `WS_XRU` + CBC (`lp-4237-2.html`, 2006–present) (FREE); no free NDF-fixing history exists (EMTA retired — see §4.6)
- TSM ADR vs underlying spread history — Yahoo + TWSE (FREE)
- Semi sector beta to TWD / EWT — Yahoo (FREE)
- CDS spread history ORCL/AVGO/TSM — DTCC public dissemination (§4.7)
- Implied-vol skew widening in past episodes — CBOE archives (FREE); OptionMetrics if licensed

**AI capex boom/bust:**
- Hyperscaler quarterly capex — SEC EDGAR XBRL `companyconcept` (§4.8) (FREE)
- NVDA/AVGO/AMD/MU historical drawdowns + IV — Yahoo Finance chart API prices + CBOE VIX/skew archives (FREE; Stooq now broken for automation — see §4.1)
- Corporate bond spread widening in past tech selloffs — FRED IG/HY OAS (`BAMLC0A0CM`, `BAMLH0A0HYM2`, §4.9); **not** TRACE (no free automated access — see §4.5)
- Datacenter power demand / grid constraints — EIA region-data API (§4.9) FREE; S&P Platts ($$)
- GPU price proxies — SemiAnalysis paid tier ($$)

### 2.9 Cost tiers

| Tier | What you get | Annual cost (USD) |
|---|---|---|
| **Bootstrap (FREE + $)** | TRACE, FRED, NY Fed, Yahoo, CBOE DataShop, DTCC CDS, OpenFIGI. Polygon for cleaner equity/options. | $3–8K |
| **Professional single seat** | + Bloomberg Terminal (1 seat) for ad-hoc curves, swaption vols, CDS, FX basis | $30–35K |
| **Production risk system** | + Bloomberg Data License nightly snap, Markit CDS, ICE swaption vols, OptionMetrics | $200–500K+ |

---

## 3. Weekly-Cadence Stress Testing — FREE-First Re-Scope

At weekly cadence with stress-testing focus, **~85–90% of data needs are achievable for $0**. Remaining gaps (swaption vols, cross-currency basis) have free workarounds defensible for stress simulation.

### 3.1 TL;DR — Free coverage by asset class

| Asset class | Free coverage | Primary free source |
|---|---|---|
| Cash equity | 100% | Yahoo Finance chart API (OHLCV + corporate actions; Stooq broken — JS-rendered) |
| Equity options (chains + IV) | ~95% | CBOE delayed + Yahoo chains, compute IV in QL |
| US Treasuries | 100% | Treasury.gov + FRED |
| Corporate bonds | ~70% | CDS-implied spread (DTCC) + SOFR curve → QL pricing; TRACE prints for mark validation via Bond Center web UI (manual only) |
| SOFR curve | ~95% | NY Fed + Pensford/Chatham published curves + DTCC SDR feed |
| Swaption / cap vols | ~60% | DTCC/CFTC SDR swaption & cap prints + MOVE history (proxy) |
| FX spot + forwards | ~95% | ECB cross + Fed H.10 + BoJ (TONAR) + CIP derivation |
| Cross-currency basis | ~50% | Derived from rate differentials |
| Single-name CDS + CDX | ~90% | **DTCC Public Price Dissemination** |
| Reference / calendar | 100% | QuantLib + SEC EDGAR + OpenFIGI + ISDA |

Total annual cost: **$0–$500** (only if backfilling historical option chains from CBOE DataShop).

### 3.2 Re-listed data by category, FREE-first

**Cash equity (trades #1–6) — fully FREE**

| Data point | Source | Cost |
|---|---|---|
| Weekly closing prices + OHLCV | **Yahoo Finance chart API** (primary; Stooq now JS-rendered/broken; Nasdaq Data Link free tier as backup) | FREE |
| Splits, dividends (corporate actions) | Yahoo Finance chart API `?events=div,split` — returns ex-date + amount/ratio inline with prices | FREE |
| ETF NAVs | Issuer (SSGA, Invesco, BlackRock) | FREE |
| ADR ratio | SEC F-6 filing, OpenFIGI | FREE |
| Borrow rate | IBKR SLB page (public) | FREE-ish |

**Equity options (trades #7–14) — mostly FREE**

| Data point | Source | Cost |
|---|---|---|
| Weekly option chains | Yahoo, CBOE delayed (15-min), NASDAQ chain | FREE |
| IV surface | Compute in QL from mid quotes (root-find IV) | FREE |
| Historical IV indices | CBOE publishes VIX, VVIX, VXAPL, VXAZN, VXGOG, VXIBM | FREE |
| Dividend schedule | SEC EDGAR + IR pages | FREE |
| Optional historical chain backfill | CBOE DataShop pay-per-dataset | ~$100–500 one-time |

**US Treasuries (trades #20–21) — fully FREE**

| Data point | Source | Cost |
|---|---|---|
| Par yield curve, CMT, H.15 | Treasury.gov, FRED H.15 | FREE |
| On-the-run UST pricing | Treasury.gov par curve → QL discount curve; TreasuryDirect for CUSIP/reference (per-CUSIP TRACE prices have the §4.5 access limit) | FREE |
| Auction results | TreasuryDirect | FREE |

**SOFR / rates curve (trades #22–25) — ~95% FREE**

| Data point | Source | Cost |
|---|---|---|
| SOFR daily fixing + 30/90/180-day averages | NY Fed, FRED (`SOFR`, `SOFR30DAYAVG`, `SOFR90DAYAVG`, `SOFR180DAYAVG`, `SOFRINDEX`) | FREE |
| SOFR forward curve / front-end path | Pensford Forward Curve (Excel export + archive), Chatham US market rates | FREE |
| SOFR swap par rates (1Y–10Y) | Pensford (1Y/2Y/3Y/5Y/7Y/10Y, exportable); Chatham (view); DTCC/CFTC SDR public feed (median/VWAP par OIS per tenor) | FREE |
| Fed Funds Effective Rate, IORB | NY Fed, FRED | FREE |

> CME's free `CmeWS` SOFR futures/options settlement endpoints are now access-restricted, and CME Term SOFR is a licensed benchmark (not on FRED). Use the published forward curves + SDR feed above instead.

**Build path:** bootstrap SOFR OIS curve in QL from NY Fed SOFR (overnight anchor) + FRED compounded SOFR averages (realized front, ≤6M) + Pensford published SOFR forward curve to pin the 6M–3Y front + Pensford/Chatham par swap nodes (3Y, 5Y, 7Y, 10Y), cross-checked against DTCC SDR-disseminated trades. For the 30Y node (sparse in the SDR feed) use the Pensford/Chatham published long-end rate.

**Swaption / cap vols — the hardest gap, ~60% free**

| Data point | Free workaround | Quality |
|---|---|---|
| ATM swaption vol level | MOVE index history (Yahoo `^MOVE`) as rates-vol level indicator; anchor the level here | Adequate for stress |
| Swaption vol surface shape | Derive from DTCC/CFTC SDR-disseminated swaption premiums (free, Part 43: premium + strike + expiry + underlying tenor) via SABR fit, anchored to MOVE level | Sparse/transaction-driven — defensible for stress, aggregate over the week and interpolate |
| Cap/floor vol surface | Built from DTCC/CFTC SDR-disseminated cap/floor premiums | Sparse but usable |
| Vol floor when SDR is too thin | Realized vol of the SOFR swap rate / UST yields (Pensford / FRED history) × vol-risk-premium multiplier | Crude fallback |
| Historical rates-vol moves | MOVE index history via Yahoo `^MOVE` (not free on FRED) | FREE |

> For research/stress this is fine. For regulated daily VaR you'd license a broker run.

**Corporate bonds (trades #15–19) — ~70% FREE**

> **TRACE access reality:** TRACE data has two separate channels, neither freely automatable:
> 1. The **FINRA developer API** (`api.finra.org`) explicitly excludes TRACE — the `trace` dataset is listed in the catalog but returns 404. FINRA's own docs state: *"TRACE API is not part of the FINRA API Platform."*
> 2. The **TRAQS API** (the actual TRACE system) requires registered FINRA broker-dealer membership — not accessible to individual investors.
> 3. The **FINRA Bond Center web UI** (`finra.org/finra-data/fixed-income/corp-and-agency/trade`) provides free manual CUSIP lookup — adequate for weekly mark validation of 5 bonds, but not automatable.
>
> For *pricing* bonds #15–19 in QL, TRACE prints are not required. Price via: SOFR OIS curve (Pensford/NY Fed) + issuer CDS-implied Z-spread (DTCC public dissemination, already used in §4.7 for ORCL and META; AAPL, MSFT, AMZN CDS are also disseminated). Use TRACE Bond Center manually to validate model prices once a week.

| Data point | Source | Cost |
|---|---|---|
| Model price inputs (discount curve) | SOFR OIS curve — Pensford + NY Fed (see §4.4) | FREE |
| Issuer credit spread (Z-spread input) | DTCC/CFTC public CDS dissemination — all 5 issuers covered | FREE |
| Issuer credit spread curve in QL | `ZeroSpreadedTermStructure` over SOFR curve, calibrated to DTCC CDS spread | FREE |

> **⚠ Reality check (pricer build):** "DTCC/CFTC public CDS dissemination — all 5 issuers covered" overstates the free feed. Usable running single-name spreads for AAPL/MSFT/ORCL/META/AMZN are effectively **absent** (NaN spreads; populated rows are upfront prices for misfiltered LBO entities). The QL build uses a **config per-issuer Z-spread fallback** (over SOFR, including the negative swap spread) instead — see §1.3 Implementation findings. Licensed Markit CDS is the production fix.
| Mark validation — transacted prices per CUSIP | FINRA Bond Center web UI (manual lookup, free FINRA account) | FREE (manual only) |
| Bond reference data (coupon, maturity, CUSIP) | SEC EDGAR submissions API + 424B5 filings (see §4.5) | FREE |
| Issuer credit ratings | S&P/Moody's/Fitch press releases (lagged), EDGAR 8-Ks | FREE |
| Automated per-CUSIP TRACE prices | No free source — TRAQS requires broker-dealer membership | Paid (Bloomberg BVAL, ICE $$$) |

**FX (trades #26–27) — ~95% FREE**

| Data point | Source | Cost |
|---|---|---|
| USD/JPY spot | ECB cross: `D.JPY.EUR` ÷ `D.USD.EUR` (daily CSV) + Fed H.10 `dat00_ja.htm` (weekly backup) | FREE |
| USD/TWD spot | Fed H.10 `dat00_ta.htm` (weekly, Mon release) + CBC Taiwan | FREE |
| USD/JPY forward points | Derive from US SOFR – JPY TONAR via CIP; BoJ publishes TONAR | FREE |
| USD/TWD NDF forward points | CIP-derived: `F = S × (1 + SOFR × T) / (1 + CBC_rate × T)` using rates already in pipeline; historical USD/TWD spot from CBC `lp-4237-2.html` (2006–present) for stress calibration | FREE |
| Cross-currency basis (USD/JPY) | Derive from JPY OIS vs JPY-USD FX forward; BIS quarterly stats | FREE w/ caveats |
| Cross-currency basis (USD/TWD) | No clean free source. Assume small/zero baseline; shock in scenarios | FREE proxy |

**Credit / CDS (trades #28–30) — ~90% FREE at weekly cadence**

| Data point | Source | Cost |
|---|---|---|
| Single-name CDS spreads by tenor | **DTCC Public Price Dissemination** (pddata.dtcc.com) | FREE |
| CDX.NA.IG, CDX.NA.HY index spreads + composition | Spreads: DTCC public (§4.7); composition: S&P DJI Index Annex PDF (semi-annual, §4.7) | FREE |
| Standard recovery rates | ISDA convention: 40% senior unsecured, 20% sub | FREE |
| Hazard curve | Bootstrap in QL from DTCC quotes | FREE |
| ISDA Standard Model parameters | ISDA publishes | FREE |

**Reference / static — fully FREE**

- Holiday calendars: QuantLib built-in
- Day-count conventions: QL built-in
- ISDA definitions: ISDA website
- CUSIP/ISIN: SEC EDGAR + OpenFIGI

### 3.3 Total realistic cost for weekly stress system

| Component | Cost |
|---|---|
| All data sources above | **$0** |
| Optional: CBOE DataShop historical option chain backfill | $100–500 |
| Optional: One academic swaption-vol snapshot | $0–200 |
| Infrastructure: storage + Python/QuantLib | $0 (local) or ~$20/mo (S3 + small EC2) |
| **Total annual** | **~$0–500 + hosting** |

---

## 4. Inspectable URLs / API Endpoints

Concrete endpoints to paste into a browser or `curl` to validate before automating. Free-data endpoints occasionally change paths or add UA/cookie checks — verify each by hand first.

### 4.1 Cash equity — prices, dividends, splits

> **Stooq broken (as of 2026):** All automated requests to `stooq.com/q/d/l/` now return JS-rendered HTML (`<meta name="robots" content="noindex,nofollow">` + `<noscript>` body) regardless of User-Agent or API key. Requires a headless browser (Playwright). **Replaced by Yahoo Finance chart API as primary.**

**Yahoo Finance chart endpoint — confirmed primary (OHLCV + corporate actions in one call)**
```
https://query1.finance.yahoo.com/v8/finance/chart/NVDA?interval=1d&range=10y&events=div,split
```
- **Auth:** real browser `User-Agent` header required (`Mozilla/5.0 ...`) — bare curl/requests with default UA returns HTTP 401
- **Returns JSON:**
  - `chart.result[0].timestamp[]` — Unix epoch seconds
  - `chart.result[0].indicators.quote[0]` — `{open, high, low, close, volume}[]`
  - `chart.result[0].indicators.adjclose[0].adjclose[]` — split/dividend-adjusted close
  - `chart.result[0].events.dividends` — `{<epoch>: {amount, date}}` — ex-date + cash amount
  - `chart.result[0].events.splits` — `{<epoch>: {numerator, denominator, ratio}}`
- `interval=1d` for daily bars; `range=10y` covers our full history window
- Portfolio tickers: `NVDA`, `TSM`, `AVGO`, `MU`, `XLK`, `SMH`
- TSM ADR ratio: 1 ADR = 5 TWD ordinary shares (changed 2023); store in config, no automation needed

**Nasdaq Data Link (Quandl EOD) — backup**
```
https://data.nasdaq.com/api/v3/datasets/EOD/NVDA/data.csv?api_key=YOUR_KEY&start_date=2018-01-01&end_date=2026-06-17
```
- Auth: API key (free signup); free tier: 50 calls/day, 50k/month
- Returns: CSV with `Date, Open, High, Low, Close, Volume, Dividend, Split, Adj_Open, ..., Adj_Close` — includes corporate actions inline
- Use as fallback if Yahoo is rate-limited; same key format as `STOOQ_API_KEY` in `.env`

### 4.2 Equity options — chains and IV

**CBOE delayed JSON (cleanest free options source)**
```
https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json
https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json     # index has underscore prefix
```
- Returns: JSON with `data.options[]`; each entry has `option` (OSI symbol), `bid`, `ask`, `last_trade_price`, `open_interest`, `volume`, `delta`, `gamma`, `theta`, `vega`, `iv`
- Auth: none
- Refresh: ~15-min delayed; perfect for weekly snapshots
- Works for: NVDA, GOOGL, MSFT, META, AAPL, TSLA, AMZN, ORCL, AVGO, AMD, TSM, MU, _SPX, _NDX, XLK, QQQ, SMH

**CBOE volatility index history (for stress calibration)**
```
https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
https://cdn.cboe.com/api/global/us_indices/daily_prices/VVIX_History.csv
```
- Returns: CSV, full history daily; resample to weekly client-side
- VIX, VVIX, VIX9D, VIX3M, VIX6M, SKEW all use same URL pattern

**Yahoo options (fallback)**
```
https://query1.finance.yahoo.com/v7/finance/options/NVDA
https://query1.finance.yahoo.com/v7/finance/options/NVDA?date=1768089600   # specific expiry epoch
```
- Returns: JSON; `optionChain.result[0].options[0].calls/puts[]` with `strike`, `bid`, `ask`, `lastPrice`, `impliedVolatility`, `openInterest`
- Same UA caveat as chart endpoint

### 4.3 US Treasuries — yield curve and CUSIP prices

**Treasury.gov par yield curve (standard)**
```
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/202605?type=daily_treasury_yield_curve&field_tdr_date_value_month=202605&page&_format=csv
```
- Returns: CSV columns `Date, 1 Mo, 2 Mo, 3 Mo, 4 Mo, 6 Mo, 1 Yr, 2 Yr, 3 Yr, 5 Yr, 7 Yr, 10 Yr, 20 Yr, 30 Yr` (par yields)
- Auth: none
- Change `202605` to any year-month; full daily history

**FRED (time series — most reliable rates source)**
```
https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key=YOUR_KEY&file_type=json
https://api.stlouisfed.org/fred/series/observations?series_id=SOFR&api_key=YOUR_KEY&file_type=json&frequency=w
```
- Auth: free API key (instant signup at fred.stlouisfed.org)
- Returns: JSON observations; `frequency=w` resamples weekly
- Series IDs: `DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, DGS20, DGS30` (Treasury yields), `SOFR, EFFR, IORB` (overnight)
- MOVE is **not** free on FRED — use Yahoo `^MOVE` instead
- If the `/fred/series` API returns 504 on a long/high-frequency series (observed on the FX series `DEXJPUS`/`DEXTAUS`), use the no-key CSV path instead: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10` (see §4.9)

**TreasuryDirect**
```
https://www.treasurydirect.gov/TA_WS/securities/announced?format=json
https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json
```
- Returns: JSON list of announced/auctioned securities with CUSIP, type, term, auction date, issue date

### 4.4 SOFR curve — fixings, futures, swap rates

**NY Fed reference rates (SOFR + alternates)**
```
https://markets.newyorkfed.org/api/rates/secured/sofr/last/30.json
https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate=2024-01-01&endDate=2026-05-16
https://markets.newyorkfed.org/api/rates/unsecured/effr/last/30.json
```
- Returns: JSON; `refRates[]` with `effectiveDate`, `rate`, `percentile1/25/75/99`, `volumeInBillions`
- Also `/api/rates/secured/sofrai/...` for SOFR Index, `/api/rates/secured/sofr30day/...` for 30-day average
- Auth: none

> **Deprecated (no longer free):** CME's `CmeWS` SOFR futures/options settlement endpoints are now access-restricted (Akamai bot protection — browser-only, no clean API; the public pages are `.../stirs/one-month-sofr.settlements.html` and `.../three-month-sofr.settlements.options.html`). ICE Swap Rate has no free API. CME Term SOFR is a licensed benchmark and is **not** on FRED. The sources below replace all three.

**Pensford Forward Curve (free SOFR swap rates + forward curve — recommended primary)**
```
https://www.pensford.com/forward-curve
https://www.pensford.com/resources/forward-curve-archive
```
- Shows SOFR swap rates at 1Y, 2Y, 3Y, 5Y, 7Y, 10Y plus the projected SOFR forward curve
- Download: Excel export (vertical/horizontal); archive holds historical weekly snapshots
- Auth: none; HTML + Excel — trivial at weekly cadence
- Use for both the front-end forward path (replaces SOFR futures strip) and the par swap nodes (replaces ICE)

**Chatham Financial — US market rates (free cross-check)**
```
https://www.chathamfinancial.com/technology/us-market-rates   # redirects to cf.com/rates/us
```
- SOFR swap rates, UST yields, and forward curves, free to view
- Caveat: redistribution restricted — manual/view pull, use as a daily sanity check on Pensford

**DTCC / CFTC SDR public price dissemination — rates slice (free primary source)**
```
https://pddata.dtcc.com/                                       # Public Price Dissemination dashboard (RATES asset class)
https://www.cftc.gov/MarketReports/SwapsReports/index.htm      # CFTC weekly swaps report
```
- Returns: every reportable USD OIS/IRS trade disseminated under Dodd-Frank Part 43 — reference rate, effective/maturity dates, tenor, traded rate, capped notional
- Same infra used for CDS in §4.7; filter spot-starting par OIS, take median/VWAP per tenor → par swap nodes
- Caveat: transaction-driven and notional-capped (e.g. >USD 250mm flagged); sparse at 30Y — use Pensford/Chatham for the long-end anchor

**DTCC / CFTC SDR — interest-rate options (free swaption/cap vol proxy)**
```
https://pddata.dtcc.com/                                       # filter RATES asset class for option products (swaptions, caps/floors)
https://www.ecfr.gov/current/title-17/chapter-I/part-43        # Part 43 Appendix A: disseminated fields incl. option premium, strike
```
- Returns: disseminated swaption & cap/floor trades with premium, strike, expiry, underlying swap tenor
- Back out implied vols from premiums → SABR fit; anchor the ATM level to MOVE (`^MOVE` via Yahoo)
- Caveat: scattered points (not a clean cube), capped notionals — aggregate weekly and interpolate; fine for stress, not regulated daily VaR

**NY Fed compounded SOFR averages (front-end realized rates)**
```
https://markets.newyorkfed.org/api/rates/secured/sofrai/last/30.json      # SOFR Index
https://markets.newyorkfed.org/api/rates/secured/sofr30day/last/30.json   # 30-day average
```
- Also on FRED: `SOFR`, `SOFR30DAYAVG`, `SOFR90DAYAVG`, `SOFR180DAYAVG`, `SOFRINDEX`
- Backward-looking realized averages — use to pin the ≤6M front alongside the Pensford forward curve

### 4.5 Corporate & Treasury bonds — TRACE

**FINRA Fixed Income Data API — confirmed working dataset**
```
https://api.finra.org/data/group/fixedIncomeMarket/name/treasuryDailyAggregates
```
- Auth: OAuth2 client credentials (POST to `https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token` with Basic auth, grant_type=client_credentials)
- **Response is CSV (not JSON)** — confirmed by live testing. Use `pd.read_csv(io.StringIO(resp.text))`, not `resp.json()`
- Returns: CSV with `tradeDate, productCategory, yearsToMaturity, benchmark, atsInterdealerCount, atsInterdealerVolume, dealerCustomerCount, dealerCustomerVolume, volumeWeightedAveragePrice`
- Useful for: UST market liquidity context for trades #20–21 (macro stress signal)
- Query via POST with body `{"limit": 5000, "offset": 0, "dateRangeFilters": [{"startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD", "fieldName": "tradeDate"}]}`
- Resample to weekly client-side: `df[df['date'].dt.dayofweek == 4]` (Friday close)

> **FINRA API — datasets that do NOT work with standard credentials:**
> - `corporateDebtMarketBreadth`, `corporateDebtMarketSentiment`, `corporateAndAgencyCappedVolume` → HTTP **404** — require additional entitlement beyond standard free registration
> - `treasuryMonthlyAggregates` → HTTP **400** — uses a different date field name (not `tradeDate`); use `treasuryDailyAggregates` and resample client-side
> - `treasuryWeeklyAggregates` → HTTP **204 No Content** — endpoint exists but FINRA publishes no data to it

> **TRACE on the FINRA API platform:** FINRA's own developer docs state *"TRACE API is not part of the FINRA API Platform."* The `trace` dataset returns 404. The separate TRACE/TRAQS system requires registered FINRA broker-dealer membership. Per-CUSIP transaction prices are only accessible via the Bond Center web UI (free, manual) or paid data vendors.
> Use DTCC/SEC SDR-disseminated CDS spreads (§4.7) as the credit spread input for pricing bonds #15–19 in QL, and validate manually against the Bond Center once a week.

**FINRA Bond Center (per-CUSIP browsable)**
```
https://www.finra.org/finra-data/fixed-income/about-cna-data
https://www.finra.org/finra-data/fixed-income/about-treasuries
```
- Human-readable, search-by-CUSIP for reference data + recent prints
- Note: the paths `/corp-and-agency` and `/treasury` in earlier versions of this doc redirect to these corrected URLs

**EDGAR submissions API — bond reference data (coupon, maturity, call schedule)**
```
https://data.sec.gov/submissions/CIK0000320193.json     # Apple
https://data.sec.gov/submissions/CIK0000789019.json     # Microsoft
https://data.sec.gov/submissions/CIK0001652044.json     # Alphabet
https://data.sec.gov/submissions/CIK0001326801.json     # Meta
https://data.sec.gov/submissions/CIK0001018724.json     # Amazon
https://data.sec.gov/submissions/CIK0001341439.json     # Oracle
https://data.sec.gov/submissions/CIK0001045810.json     # NVIDIA
```
- Returns: JSON; full filing history per company — filter `filings.recent.form` for **`424B5`, `424B2`, `424B3`** to find bond prospectuses. AAPL and ORCL predominantly use `424B2` (shelf supplement); MSFT uses `424B3`; AMZN and ORCL use both `424B5` and `424B2`. Filtering on `424B5` alone misses the majority of AAPL/MSFT filings.
- Auth: **none**, but SEC requires an identifying `User-Agent` header on every request or returns 403. Format: `User-Agent: AppName contact@email.com` — no API key needed
- Rate limit: 10 requests/second
- The `efts.sec.gov/LATEST/search-index` full-text search endpoint (used in earlier drafts) also requires the same `User-Agent` header; the submissions endpoint above is simpler and fully documented

### 4.6 FX — spot, forwards, NDFs

**ECB daily reference rates — USD/JPY primary source (confirmed working)**
```
https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A?format=csvdata
https://data-api.ecb.europa.eu/service/data/EXR/D.JPY.EUR.SP00.A?format=csvdata
```
- Auth: none; returns CSV with `TIME_PERIOD` and `OBS_VALUE` columns; daily, confirmed working
- Derive USD/JPY: `(JPY/EUR) / (USD/EUR)` — both series above, no FRED needed for JPY
- ECB does **not** carry TWD; use Fed H.10 below for USD/TWD

**Fed H.10 Foreign Exchange Rates — USD/TWD primary source (confirmed working)**
```
https://www.federalreserve.gov/releases/h10/hist/dat00_ta.htm    # USD/TWD (TWD per USD)
https://www.federalreserve.gov/releases/h10/hist/dat00_ja.htm    # USD/JPY (JPY per USD) — backup to ECB
```
- Auth: none; returns HTML table with `DD-MMM-YY` and rate columns; updated every Monday 4:15 p.m. ET
- Coverage: 2000 to present; "ND" = no data (holiday)
- Adequate for weekly cadence (parse with pandas `read_html`); use as primary for USD/TWD and backup for USD/JPY


**Bank of Japan — TONAR / Uncollateralized Overnight Call Rate (JPY OIS anchor)**
```
https://www.stat-search.boj.or.jp/api/v1/getDataCode?lang=en&db=FM01&code=STRDCLUCON&startDate=20200101
```
- Series: `db=FM01`, `code=STRDCLUCON` = "Call Rate, Uncollateralized Overnight, Average (Daily)"
- Auth: none; BOJ API launched Feb 2026, free; returns JSON with `SURVEY_DATES[]` and `VALUES[]`; unit: % p.a.; null on non-business days
- Use for CIP forward derivation: `F = S × (1 + r_USD × 90/360) / (1 + r_JPY × 90/360)` where r_JPY = this series, r_USD = SOFR (NY Fed), S = ECB cross

> **Previous BOJ URLs retired:** `mutan/index.htm` (Excel only) and `famecgi2?...&lstSelection=ST01` (interactive form + wrong series code)

**Central Bank of the Republic of China (Taiwan) — TWD discount curve anchor**
```
https://www.cbc.gov.tw/en/np-696-2.html    # Interbank Overnight Call-loan Rate (daily, HTML table)
```
- **Use case:** sole input needed is the TWD discount rate to anchor the flat TWD OIS curve for pricing the TWD leg of trade #26 (5Y USD/TWD NDS Cross-Currency Swap) in QL's `DiscountingSwapEngine`
- **Do not automate at weekly cadence.** The CBC rediscount rate (policy rate, currently 2%) changes ~4×/year at scheduled CBC board meetings — it is headline news when it changes. Store as a config constant (`TWD_DISCOUNT_RATE = 0.020`) and update manually on board-decision days.
- If you want the floating overnight call loan rate (~0.83%), the URL above shows it as an HTML table (no CSV/JSON endpoint exists on any CBC page)
- For stress scenarios, the dominant risk driver in trade #26 is the USD/TWD spot/NDF rate (covered by Fed H.10 + CBC) — the TWD rate differential is a second-order effect and can be held fixed or shocked by a flat parallel shift in stress configs
- Previous URL `lp-645-2.html` was a general overview page; `np-696-2.html` is the dedicated rate page

**CBC historical USD/TWD spot rates (stress scenario calibration)**
```
https://www.cbc.gov.tw/en/lp-4237-2.html
https://www.cbc.gov.tw/en/lp-700-2.html
```
- `lp-4237-2.html` — yearly USD/TWD interbank spot closing rate datasets, 2006–present; use for historical stress calibration (how far TWD devalued in past episodes)
- `lp-700-2.html` — daily NT$/US$ closing rate (paginated table, 3,000+ entries); this is also the **NDF settlement fixing reference** for trade #26 — the CBC interbank closing rate is what USD/TWD NDFs settle against in practice
- Both are HTML tables (no direct CSV API); parse with `pandas.read_html()` at weekly cadence
- **EMTA removed:** The old EMTA URL for TWD NDF templates (`/em-benchmarks/...`) is 404 — EMTA archived all NDF templates into the ISDA 2021 FX Definitions framework and TWD NDF is no longer listed in their documentation. TAIFEX `dailyFXRates` is also 404. No clean free NDF-specific fixing API exists for TWD.
- **Forward points for CCS pricing:** derive via CIP (`F = S × (1 + SOFR × T) / (1 + CBC_rate × T)`) — no separate NDF data source needed

### 4.7 Credit / CDS

**DTCC Public Price Dissemination (PPD) — free CDS source**

> **Single URL needed for automation** — only the S3 direct download below. The two `pddata.dtcc.com` URLs are JS-rendered web UIs (correct dashboard: `pddata.dtcc.com/ppd/cftcdashboard`); `dailyreports` links to real-time intraday Slice Reports which are not appropriate for weekly batch. The S3 Cumulative Reports file is the correct download.

```python
# Automatable download — no auth required
# Replace {YYYY}_{MM}_{DD} with the target business day date
# Confirmed S3 prefix: cftc/eod/ (not slices/ — that path returns 403)
https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_CREDITS_{YYYY}_{MM}_{DD}.zip
https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_RATES_{YYYY}_{MM}_{DD}.zip
```
- **What the CFTC path contains:** Index CDS (iTraxx Europe, CDX) — confirmed by live inspection. `Underlying Asset Name` is empty in the CFTC CREDITS files. US single-name CDS (ORCL, META, AAPL, MSFT, AMZN) are security-based swaps under **SEC jurisdiction** — use the SEC path `sec/eod/SEC_CUMULATIVE_CREDITS_*.zip` instead (see Python sketch below)
- **Availability:** 366 days rolling from today; history starts Dec 29, 2023. Contact GTRSupport@dtcc.com for older data.
- **Cadence:** Download Monday's file weekly — it contains all trades reported on that UTC date

**Fields extracted for our portfolio (from Appendix A, PPD User Guide v2.8):**

| Field | Purpose |
|---|---|
| `Underlying Asset Name` | **Primary filter** — match "ORACLE", "META", "CDX.NA.IG" |
| `UPI Underlier Name` | Secondary/standardised underlier filter (new Phase 2 field) |
| `Expiration Date` | CDS maturity → defines tenor bucket for QL hazard curve |
| `Spread-Leg 1` | **Running CDS spread (bps)** — primary input to `PiecewiseDefaultCurve<HazardRate, BackwardFlat>` |
| `Fixed rate-Leg 1` | Standard coupon (100bp IG / 500bp HY) — confirms ISDA convention |
| `Price` | Upfront points when traded off standard coupon |
| `Execution Timestamp` | Filter to current week's trades |
| `Action type` | Keep `NEW` and `REVI` (latest revision); discard `CANCEL` |
| `Notional amount-Leg 1` | Capped at regulatory threshold — use for weighting median spread |
| `Notional currency-Leg 1` | Confirm USD |

**Python filter sketch:**
```python
import pandas as pd, requests, zipfile, io
from datetime import date, timedelta

# -----------------------------------------------------------------------
# CRITICAL: Two separate S3 paths serve different CDS populations:
#
#   cftc/eod/CFTC_CUMULATIVE_CREDITS_*.zip  — iTraxx Europe index CDS only.
#       'Underlying Asset Name' is EMPTY. Not useful for our US names.
#
#   sec/eod/SEC_CUMULATIVE_CREDITS_*.zip    — US single-name CDS (security-
#       based swaps under SEC jurisdiction). 'Underlying Asset Name' IS
#       populated: "ORACLE CORPORATION", "META PLATFORMS INC.", "Apple Inc.",
#       "Microsoft Corporation", "AMAZON.COM INC", etc.
#
# Use the SEC path for ORCL, META, AAPL, MSFT, AMZN single-name CDS.
# Use the CFTC path for CDX.NA.IG index (via UPI Underlier Name field).
# -----------------------------------------------------------------------

d = date.today()
d -= timedelta(days=d.weekday())  # roll back to Monday

# For single-name US CDS (bonds #15-19 Z-spread + CDS trades #28-29):
url = f"https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod/SEC_CUMULATIVE_CREDITS_{d:%Y_%m_%d}.zip"

r = requests.get(url, headers={"User-Agent": "Faultline/1.0 <your-email>"})
with zipfile.ZipFile(io.BytesIO(r.content)) as z:
    df = pd.read_csv(z.open(z.namelist()[0]))

# Filter on Underlying Asset Name (populated in SEC path)
names = ["ORACLE", "META PLATFORMS", "APPLE", "MICROSOFT", "AMAZON"]
df = df[df["Underlying Asset Name"].astype(str).str.upper().str.contains(
    "|".join(names), na=False
)]
df = df[df["Action type"].isin(["NEW", "MODI", "REVI"])]  # MODI = modification

# Weekly median spread per entity / tenor for QL hazard-curve bootstrap
spreads = (df.groupby(["Underlying Asset Name", "Expiration Date"])["Spread-Leg 1"]
             .median().reset_index())

# For CDX.NA.IG index (trade #30) — use CFTC path, filter UPI Underlier Name:
# url_idx = f"https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_CREDITS_{d:%Y_%m_%d}.zip"
# df_idx = ... filter on df_idx["UPI Underlier Name"].str.contains("CDX", na=False)
```

**Reference:** [DTCC PPD CFTC Dashboard](https://pddata.dtcc.com/ppd/cftcdashboard) · [PPD User Guide v2.8 (PDF)](https://kgc0418-tdw-data-0.s3.amazonaws.com/gtr/static/gtr/docs/RT_PPD_quick_ref_guide.pdf)

**CDX.NA.IG Index Annex — composition, RED codes, weightings (S&P DJI)**
```
# Current on-the-run (Series 46, effective 20 March 2026):
https://www.spglobal.com/spdji/en/documents/index-news-and-announcements/CDX.NA.IG.46-V1%20Index%20Annex.pdf

# URL pattern — series N increments each roll (March 20 and September 20):
https://www.spglobal.com/spdji/en/documents/index-news-and-announcements/CDX.NA.IG.{N}-V1%20Index%20Annex.pdf
```
- **Auth:** none — publicly accessible in browser; S&P Global's CDN blocks automated User-Agents so use `requests` with a real browser UA header
- **Contents:** 125 reference entity names, sub-index classification (BBB, FIN, ENRG, TMT, CONS), RED code, reference obligation CUSIP/ISIN, equal weighting (~0.8% each)
- **Update cadence: semi-annual only** — composition is fixed between rolls. Download once per roll (March 20 and September 20), not weekly
- **Series tracking:** S.44 → 20 Mar 2025; S.45 → 20 Sep 2025; S.46 → 20 Mar 2026 (current); S.47 → 20 Sep 2026
- **Use in pipeline:**
  1. *QL pricing (trade #30):* model as single `CreditDefaultSwap` against flat index-implied hazard curve — index spread comes from DTCC PPD (§4.7), composition not needed for this path
  2. *Portfolio overlap:* verify ORCL and META (trades #28/#29) are CDX.NA.IG constituents — affects stress scenario netting
  3. *Sub-index tilt:* ENRG and TMT sub-annexes (`CDX.NA.IG.ENRG.46-V1`, `CDX.NA.IG.TMT.46-V1`) relevant for AI capex stress sector decomposition
- **Old URL retired:** `spglobal.com/spdji/en/indices/fixed-income/cdx-na-ig/` (404 — S&P DJI landing page reorganised; direct PDF annex URL above is the replacement)

**ISDA CDS Standard Model — one-time developer reference only**
```
https://www.cdsmodel.com/cdsmodel/documentation.html
```
- **Not a data pipeline source** — provides no recurring data input to the weekly pricer
- QuantLib's `IsdaCdsEngine` already implements the ISDA Standard Model natively; the three inputs it needs (SOFR discount curve, hazard curve from DTCC spreads, 40% recovery rate) are all sourced elsewhere in this pipeline
- **Use once at pricer setup for two purposes:**
  1. **Constructor settings:** Download "ISDA Standard Model Settings for Fee Computations" to confirm the exact `IsdaCdsEngine` parameters — accrual bias (`NoBias`) and forwards-in-coupon-period (`Flat`) per model v1.8.2+. Getting these wrong produces prices that deviate from dealer marks even with correct inputs.
  2. **RFR Test Grids:** Run your `IsdaCdsEngine` against the published RFR Test Grids once to verify your QL output matches the ISDA reference implementation. If they agree, your pricer is producing ISDA-compliant / dealer-consensus CDS marks.
- **What to ignore from this page:** Source code (use QL), holiday calendars (QL built-in, see §2.7), "RFR Interest Rates" link (points to paid IHS Markit service), IBOR test grids (LIBOR-era, no longer relevant)

### 4.8 Reference / static

**OpenFIGI mapping (CUSIP/ISIN → FIGI → ticker)**
```
POST https://api.openfigi.com/v3/mapping
Body: [{"idType":"ID_CUSIP","idValue":"037833FB1"}]
```
- Auth: none for low volume (25 req/min); free API key gives 25 req/6sec
- Returns: JSON array with FIGI, name, ticker, exchange
- **Usage: one-time setup only — not in the weekly pipeline loop**
- Builds the static `instruments.json` reference table mapping all 30 trades to their identifiers across data sources (CUSIP for TRACE/EDGAR, ticker for Yahoo/CBOE, FIGI as canonical cross-source key)
- Primary calls: CUSIP → metadata for bonds #15–21 (confirm correct bond before querying TRACE); ticker → FIGI for equities #1–6
- Re-run only when a new trade is added to the portfolio

**SEC EDGAR XBRL company facts (fundamentals + capex for stress)**
```
https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json     # NVDA — full XBRL fact dump
https://data.sec.gov/api/xbrl/companyconcept/CIK0000789019/us-gaap/PaymentsToAcquirePropertyPlantAndEquipment.json  # MSFT capex series
```
- Auth: **none**, but requires `User-Agent: AppName contact@email.com` — omitting returns 403
- **Usage: quarterly update (not weekly) — feeds stress scenario calibration, not mark-to-market pricing**

**Use case 1 — AI capex stress scenario (§2.8):**
Pull `PaymentsToAcquirePropertyPlantAndEquipment` quarterly for MSFT, AMZN, META, GOOGL, AAPL (hyperscalers) and NVDA. This gives the quantitative anchor for the "AI capex bust" shock:
- Current hyperscaler combined capex run-rate and YoY growth rate
- Stress magnitude: "capex cuts by X%" → NVDA revenue down Y% → equity shock on trades #1, #3, #4; credit spread widening on bonds #16–19; CDS basis on #29
- Update after each earnings season (when new 10-Q capex guidance is filed)

**Use case 2 — Bond issuer credit fundamentals (trades #15–19):**
Pull `companyfacts` for AAPL, MSFT, ORCL, META, AMZN. Key XBRL tags: `LongTermDebtNoncurrent`, `Revenues`, `NetIncomeLoss`, `InterestExpense`. Compute per-issuer leverage and interest coverage ratios to calibrate **how much the bond spread should widen in each stress scenario** — feeds the Z-spread shock applied to `ZeroSpreadedTermStructure` in QL for bonds #15–19.

Key tags by use:
| XBRL tag | Used for |
|---|---|
| `PaymentsToAcquirePropertyPlantAndEquipment` | Hyperscaler GPU/capex spend — AI stress calibration |
| `Revenues` | Revenue trajectory — equity + credit stress sizing |
| `LongTermDebtNoncurrent` | Leverage ratio — bond spread stress calibration |
| `NetIncomeLoss` + `InterestExpense` | Interest coverage — issuer credit quality in stress |
| `CashAndCashEquivalentsAtCarryingValue` | Liquidity cushion — downside scenario floor |

### 4.9 Stress scenario history — sources and scenario construction

**Corrected source endpoints:**

```
# BIS — FX devaluation paths + USD funding-stress context (portal moved Jan 2024 to data.bis.org)
https://data.bis.org/static/bulk/WS_XRU_csv_flat.zip      # Bilateral USD exchange rates (USD/TWD, USD/JPY history)
https://data.bis.org/static/bulk/WS_EER_csv_flat.zip      # Effective exchange rates (broad USD, TWD, JPY)
https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip      # Global liquidity indicators (USD cross-border credit = funding-squeeze proxy)
https://data.bis.org/static/bulk/WS_OTC_DERIV2_csv_flat.zip  # OTC derivatives notional/gross market value (systemic context)

# FRED — credit-spread stress, no API key, no 504 (use fredgraph.csv path, not the /fred/series API)
https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2   # ICE BofA US HY OAS (percent), daily
https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLC0A0CM      # ICE BofA US IG corp OAS — closer to our bond issuers
https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS          # VIX close — equity-vol stress for options book (full history, 1990–present)
#   ⚠ ICE-licensing limit (verified Jun 2026): FRED now serves only a ROLLING ~3-YEAR window for the two ICE BofA OAS
#     series — on BOTH fredgraph.csv AND the keyed /fred/series/observations API (not an endpoint quirk). The trailing
#     window covers the AI-capex boom + current spread levels, enough to size Methodology B bust shocks relative to today.
#     Full COVID/2022/GFC OAS history (Methodology A historical-replay ΔOAS anchors) is NOT free from FRED → deferred to
#     the Methodology A stage. VIXCLS is unaffected (not ICE-licensed).

# EIA — datacenter power demand (AI capex demand signal). API key required IN URL + facets.
https://api.eia.gov/v2/electricity/rto/region-data/data/?api_key=KEY&frequency=hourly&data[0]=value&facets[type][]=D&facets[respondent][]=PJM&facets[respondent][]=ERCO&start=2019-01-01&sort[0][column]=period&sort[0][direction]=desc
#   type facet: D=Demand, DF=Day-ahead forecast, NG=Net generation, TI=Total interchange
#   respondent: PJM (N. Virginia "Data Center Alley"), ERCO (Texas), MISO, CISO — the data-center-heavy grids

# MOVE — rates vol (swaptions/cap stress). Yahoo needs a real User-Agent header.
https://query1.finance.yahoo.com/v8/finance/chart/%5EMOVE?interval=1d&range=10y
```

---

**How each source maps to a portfolio shock factor:**

| Source | Risk factor it calibrates | Trades it shocks |
|---|---|---|
| BIS `WS_XRU` | USD/TWD, USD/JPY spot devaluation magnitude | #26 (TWD CCS), #27 (JPY fwd), #2 (TSM ADR FX) |
| BIS `WS_GLI` | USD funding squeeze → cross-currency basis blowout | #26, #27 (basis is the discount-curve adjustment) |
| FRED `BAMLC0A0CM` / `BAMLH0A0HYM2` | Credit-spread widening (IG for our issuers, HY as risk-appetite tail) | #15–19 bonds, #28–30 CDS |
| FRED `VIXCLS` | Equity implied-vol spike | #7–14 options (vega), #1–6 equity (delta) |
| Yahoo `^MOVE` | Rates implied-vol spike | #23, #24 swaptions, #25 cap |
| EIA region demand | Datacenter power-load growth (AI capex proxy) | #1,#3,#4 semis, #16–19 hyperscaler bonds |

---

#### Methodology A — Historical replay (data-driven, the "big historical" scenarios)

Use this to build shock vectors anchored to *what actually happened* in past crises, rather than guessed magnitudes.

1. **Pick the episodes.** For each named crisis, define a start→trough window:
   - GFC (2008-09 → 2009-03), Euro crisis (2011-07 → 2011-10), China deval/oil (2015-08 → 2016-02), COVID (2020-02-19 → 2020-03-23), 2022 rate shock (2022-01 → 2022-10).
2. **Pull peak-to-trough moves per risk factor** from the sources above over each window:
   - Credit: `BAMLC0A0CM` ΔOAS (e.g. COVID IG OAS +270bp, GFC +600bp) → these become the **CS01 spread shocks** for bonds #15–19 and CDS #28–30.
   - Rates vol: `^MOVE` peak level and Δ (e.g. 2022 MOVE → ~160) → **swaption/cap vega shock** for #23–25.
   - Equity vol: `VIXCLS` peak (COVID ~82, GFC ~80) → **vega shock** for #7–14; combine with the underlying equity drawdown for delta/gamma.
   - FX: `WS_XRU` max TWD and JPY depreciation over each window → **FX delta shock** for #26, #27, #2.
   - Funding: `WS_GLI` contraction in USD cross-border credit → proxy for **cross-currency basis widening** (the basis adjustment on the #26/#27 discount curves).
3. **Assemble a shock vector per episode** = {ΔIG_OAS, ΔHY_OAS, ΔMOVE, ΔVIX, ΔUSDTWD, ΔUSDJPY, Δbasis, ΔUST}. Store as a JSON scenario file.
4. **Revalue in QL.** Apply each shock to the relevant curve/surface object (`ZeroSpreadedTermStructure` for credit, `BlackVarianceSurface` for vol, FX spot for the CCS/forward, hazard curve for CDS) and full-revalue the book via the `Observer` chain. Diff vs. base PV = scenario P&L.
5. **Correlate the factors.** Use the historical windows to set realistic *joint* moves (e.g. in risk-off, MOVE↑ + VIX↑ + IG OAS↑ + JPY↑ as carry unwinds) rather than shocking one factor in isolation.

#### Methodology B — AI capex boom/bust (thematic, forward-looking)

This scenario has no single historical analogue, so it is built bottom-up from capex + power data, then cross-checked against the historical factor moves from Methodology A.

1. **Establish the capex run-rate** (from §4.8 EDGAR XBRL): sum `PaymentsToAcquirePropertyPlantAndEquipment` across MSFT, AMZN, META, GOOGL for the trailing 4 quarters and compute YoY growth. This is the "boom" baseline.
2. **Corroborate with independent power data (EIA).** Pull hourly demand for the datacenter-heavy grids (PJM, ERCO, MISO) and compute the YoY growth in baseload. Rising datacenter load that *leads* capex guidance confirms the buildout is real and ongoing; flattening regional demand is an early **bust** signal independent of company PR. Power availability is also the binding physical constraint — if grid interconnection queues cap supply, capex must slow regardless of demand.
3. **Define the two legs:**
   - **Boom:** capex +X% accelerates → NVDA/AVGO/MU revenue up, multiples expand, IG spreads tighten, equity vol low. Bullish for #1/#3/#4 longs, the SMH short (#6) hurts, call options (#7,#9) gain.
   - **Bust:** capex cut by Y% (size Y from the EDGAR YoY growth — a reversion from +60% to flat is a ~40% revenue hit to NVDA) →
     - **Equity:** NVDA/AVGO/MU drawdown (size from the revenue→price elasticity in past semi cycles); SMH short (#6) and SPX/QQQ puts (#12,#13,#14) cushion.
     - **Equity vol:** VIX spike from Methodology A applied to the single-name surfaces (#7–11) and index hedges.
     - **Credit:** hyperscaler + semi IG spreads widen — apply a `BAMLC0A0CM`-scaled ΔOAS to bonds #16 (MSFT), #18 (META), #19 (AMZN); ORCL #17 and the ORCL/META CDS #28/#29 widen; sell-protection CDX #30 loses (mark-to-market on index spread).
     - **Rates:** flight-to-quality rallies UST (#20/#21 gain), but if the selloff is inflation/supply-driven, pair with a MOVE-based swaption vega shock (#23–25).
4. **Layer the Taiwan tail (optional joint scenario).** AI-bust + Taiwan-geopolitical is the worst case: TSM ADR (#2) gaps on both FX (`WS_XRU` TWD deval) and the foundry-supply shock; semis #3/#4 fall further; the USD/TWD CCS (#26) takes FX + basis hits. Combine the BIS FX shock with the AI-bust equity/credit shock.
5. **Express as a QL shock vector** identical in form to Methodology A, so both run through the same revaluation engine.

> **Cadence:** scenario *definitions* are refreshed quarterly (after EDGAR capex + EIA demand update); the weekly run only re-applies the stored shock vectors to the latest curves. Historical source pulls (BIS, FRED, MOVE) are needed once to calibrate, then refreshed monthly.

**Implementation status (Methodology B):** the stress-calibration pulls are pipelined under the `stress` asset class — `configs/marketdata/stress/stress_marketdata.json`, `src/marketdata/stress/` (runner + `fred_macro`, `move_yahoo`, `eia_power` downloaders), verified by `scripts/verify_stress.py`. This pipeline pulls only the stress-*specific* inputs not already owned by another asset class:

| Methodology B input | Source (this pipeline) | Status |
|---|---|---|
| IG/HY corp OAS (ΔOAS credit leg) | FRED `BAMLC0A0CM`, `BAMLH0A0HYM2` | ✅ (rolling ~3y — ICE-licensing limit above) |
| Equity-vol level (VIX) | FRED `VIXCLS` (close); CBOE archives in `options/` (intraday high) | ✅ |
| Rates-vol level (MOVE) | Yahoo `^MOVE` | ✅ |
| Datacenter power demand | EIA v2 daily-region-data (PJM/ERCO/MISO/CISO) | ⚠ needs free `EIA_API_KEY` in `.env` (skips with a warning if unset) |
| Hyperscaler capex run-rate | EDGAR XBRL — **reference** pipeline (`edgar_xbrl_facts.csv`) | ✅ (already downloaded) |
| Semis/hyperscaler equity drawdowns + IV | **equity** + **options** pipelines | ✅ (already downloaded) |
| Single-name CDS widening | **credit** + **bonds** pipelines (DTCC) | ✅ (already downloaded) |
| UST flight-to-quality | **bonds** pipeline (Treasury.gov) | ✅ (already downloaded) |

Methodology A (historical replay) and its BIS `WS_XRU`/`WS_GLI` FX-and-funding history + episode-window shock-vector generation — and the Taiwan joint-tail (step 4), which depends on BIS `WS_XRU` — are a later stage and are intentionally not built here.

### 4.10 Suggested manual inspection order

Validate the stack before any automation by hitting these six URLs in a browser:

1. `https://query1.finance.yahoo.com/v8/finance/chart/NVDA?interval=1d&range=5y&events=div,split` → confirm JSON with `chart.result[0]` OHLCV + events (requires real browser User-Agent; Stooq now returns JS-rendered HTML — broken for automation)
2. `https://cdn.cboe.com/api/global/delayed_quotes/options/NVDA.json` → confirm full option chain
3. `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/202605?type=daily_treasury_yield_curve&field_tdr_date_value_month=202605&page&_format=csv` → confirm yield curve CSV
4. `https://markets.newyorkfed.org/api/rates/secured/sofr/last/30.json` → confirm SOFR JSON
5. `https://www.pensford.com/forward-curve` → confirm SOFR swap rates + forward curve with Excel export (CME futures endpoint is no longer free)
6. `https://pddata.dtcc.com/ppd/cftcdashboard` → confirm dashboard loads; for automation test two S3 paths directly: `https://kgc0418-tdw-data-0.s3.amazonaws.com/cftc/eod/CFTC_CUMULATIVE_CREDITS_{YYYY}_{MM}_{DD}.zip` (index CDS) and `https://kgc0418-tdw-data-0.s3.amazonaws.com/sec/eod/SEC_CUMULATIVE_CREDITS_{YYYY}_{MM}_{DD}.zip` (US single-name CDS)

If those six work in your environment, the remaining sources follow the same auth-free pattern.

---

## 5. Realized Market-Data Consumption in Pricing & Risk (audit, 2026-07-11)

This section closes the loop between what was **downloaded** (§2–§4, ~43 feed CSVs) and
what the pricer/risk code **actually reads**. It answers the recurring question *"why is so
much of the identified data not used in portfolio pricing?"* — and separates the causes into
**data-quality defects** vs **methodology/scope choices** vs **reference data**.

### 5.1 How pricing consumes data
The PV engine (`src/pricer/loader/market_data_loader.py`) resolves every trade into exactly
**8 market-data kinds**; that is the entire PV surface. Each is served by one adapter reading a
specific feed:

| Kind | Adapter | Feed read at PV time | Trades |
|---|---|---|---|
| OIS_CURVE | rates_adapter | `rates/pensford_sofr_forward.csv` | discounting for all rates/bonds(corp)/credit/fx/options |
| NORMAL_VOL | rates_adapter | `stress/move_index.csv` (MOVE-derived level, point-in-time) | #23–25 |
| TREASURY_CURVE | bonds_adapter | `bonds/treasury_par_yields.csv` | #20–21 |
| CREDIT_SPREAD | bonds_adapter | `credit/dtcc_single_name_cds.csv` → config fallback | #15–19 |
| EQUITY_MARKET | equity_adapter | `options/chains/<UND>_chains.csv` | #7–14 |
| EQUITY_SPOT | cash_equity_adapter | `equity/prices_daily.csv` + `equity/dividends.csv` | #1–6 |
| CDS_SPREAD | credit_adapter | `credit/dtcc_single_name_cds.csv`, `credit/dtcc_index_cds.csv` | #28–30 |
| FX | fx_adapter | `fx/ecb_fx_rates.csv`, `fx/fed_h10_fx_rates.csv`, `fx/boj_tonar.csv` | #26–27 |

The risk engines add a **second, disjoint** set of feeds (they are *not* PV inputs): sensitivity
VaR (`src/pricer/risk/var.py`, config `risk.var.factors`) and full-reval VaR
(`src/pricer/risk/factor_history.py`) read `rates/fred_rates.csv`, `stress/fred_stress_macro.csv`,
`stress/move_index.csv`, and the vol indices `options/vol_indices/{VIX,VIX3M,SKEW,VXAPL}_History.csv`.

### 5.2 The three reasons a downloaded feed is "not used in pricing"
1. **Used in risk, not PV** — FRED rates, BofA IG OAS (fred_stress_macro), VIX/VIX3M/SKEW/VXAPL.
   On a pure pricing lens these look idle; they drive VaR/scenarios. (MOVE was here until §5.5 —
   it now also sets the swaption/cap vol level, so it is a PV input too.)
2. **Attempted for PV but bypassed by a data-quality defect** — `bonds/dtcc_corp_cds_spreads.csv`.
   The free DTCC *corporate* single-name CDS feed is ~76% NaN and the populated rows are upfront
   prices for misfiltered LBO entities, so bonds #15–19 previously fell back to configured
   Z-spreads. **This is the one genuine "data let us down" case** (see §5.3 for the fix).
3. **Reference / redundant / roadmap** — never a pricing input by design, or superseded:
   TreasuryDirect (announced/auctioned), FINRA treasury (superseded by Treasury.gov par),
   EDGAR filings/XBRL, OpenFIGI, splits, prices_adj_close (raw close used instead),
   NY-Fed realized SOFR (curve-front cross-check only), pensford_historical, DTCC SDR IRS
   (superseded by Pensford), DTCC SDR IR-options (unusable for a smile — see §5.5),
   CBC TWD NDF fixings (TWD leg held at a constant policy rate — no free basis curve),
   EIA power demand (stress narrative), and the extra vol indices VIX6M/VIX9D/VVIX/VXAZN/VXGOG
   (no factor maps to them; AMZN/GOOG are not option underliers in the book).

**Net:** of ~43 catalog feeds, ~13 are PV inputs, ~7 are risk-only, ~1 is a data-quality
bypass, and the rest are reference/redundant/roadmap by design. The gap is intentional, not a
defect — with the single exception in §5.3.

### 5.3 Bonds credit-spread reconciliation (`bonds_adapter`, 2026-07-11)
Fix for reason (2): the bond book now first derives an issuer **5Y par CDS spread from the
well-populated credit-desk feed** `credit/dtcc_single_name_cds.csv` — the *same* SEC-path file the
CDS trades #28–30 price off — and only falls back to `issuer_fallback_bp` when that feed is too
thin. The CDS par spread (credit-to-Treasury) is lifted to a **Z-spread over SOFR** by adding the
**SOFR–Treasury basis derived from the free curves** — `Treasury par (Treasury.gov) − SOFR OIS par
(Pensford)` at `basis_tenor` (default 10Y, the long end of the free grid — appropriate since both
issuers on this path are long-dated), clamped to a sane band. This replaces the earlier hardcoded
~45bp constant with a live free-data figure (~+42bp at 10Y), keeping the whole path 100% free-data;
`sofr_treasury_swap_spread_bp` remains only as the fallback if a curve/tenor is missing.

Result at 2026-05-29: **ORCL #17** → 177bp CDS + ~42bp basis = **~219bp** Z-spread (`from_cds=true`,
`source=dtcc_single_name`); **META #18** → 73.5bp → **~115bp**. AAPL/MSFT/AMZN have no clean free
single-name quotes and still use the fallback (60/60/75bp). This also fixes a latent bug: the old
path would have used a raw CDS spread directly over SOFR (understating long-dated corporate yields
by the basis — a ~6–7% price error on ORCL 2053). Residuals: the CDS→bond basis is held at zero and
the SOFR–Treasury basis uses one representative tenor (not maturity-interpolated). Tests:
`tests/pricer/test_bonds_real_data.py::test_credit_spread_from_single_name_cds`.

### 5.4 DQ-gate coverage + catalog clarity
Two governance gaps surfaced by the audit are closed:
- **`configs/marketdata/dq_gate.json`** previously declared only the 11 risk-factor + equity-price
  feeds, leaving the core PV feeds (Pensford SOFR, Treasury par, all 7 option chains, single-name
  CDS, dividends) **ungated**. The gate now covers all of them (23 files; single-name CDS gets a
  lenient null threshold since `Spread-Leg 1` is legitimately ~82% null pre-filter).
- The UI catalog's **"Trades" count was asset-class-level** (every feed in a folder inherited the
  same number). It is replaced by real feed→trade consumption (`ui/bff/app/md_consumption.py`):
  a `consumed_by` tag (PV / Risk / both / not-consumed), a one-line role, and the actual PV trades
  a feed prices. The catalog now states explicitly that a DQ verdict of **"ungated" means
  unpoliced, not unusable**.

### 5.5 Swaption/cap vol — MOVE-derived level; smile is data-blocked (`rates_adapter`, 2026-07-11)
Reason (1) improvement + a hard data finding. The swaption/cap normal vol (#23–25) was a **frozen
90bp config constant**, with the MOVE index read only to build a diagnostic string. It is now
**derived live from MOVE**: the ICE BofA MOVE index is quoted in annualized basis points of
Treasury-yield implied vol — which *is* a Bachelier normal-vol level — so `normal_vol =
MOVE_bp/1e4 x move_scale`, taken point-in-time as-of the valuation date and clamped to a sane band.
On 2026-05-29 that is ~70.2bp (vs the old 90bp), and it now **responds to the rates-vol regime**
(a MOVE spike raises the swaption hedge value, where the frozen level did not). `flat_level` remains
the fallback. **Effect:** move_index becomes a PV input (PV+Risk) driving #23–25, closing the
audit's "MOVE only annotates" finding.

**Why not a full SABR surface.** The intended source was `rates/dtcc_sdr_ir_options.csv` (DTCC SDR
disseminated swaption/cap premiums, §2.3). It is **not calibratable**: of 540 rows over 2 years,
only **~20** carry a clean premium + notional + strike together (`Fixed rate-Leg 1` is null in
444/540; notionals are block-capped with a `+`; premiums are often 0), and the feed has **no option
type at all** — `Product name` / `Underlying Asset Name` are 100% empty, so payer/receiver and even
swaption-vs-cap are unknown. Backing out implied vols is impossible. The vol therefore stays **flat
across strike & expiry**; a real smile/cube needs a licensed vol product (ICAP/Tullett, Bloomberg
VCUB). `move_scale` (default 1.0) is the one knob to tune the 1M-MOVE level to the swaption expiry
if a broker mark becomes available.

---

## 6. Next Steps

1. Manually verify the six core endpoints above in a browser
2. Sign up for FRED API key (instant, free) and FINRA developer credentials (instant, free)
3. Decide on storage layer (local Parquet vs. S3) and scheduler (cron vs. Airflow)
4. Build the pipeline scaffold: a Python module per data source that returns a normalized DataFrame
5. Land the weekly snap into a date-partitioned Parquet store
6. Wire QuantLib pricers on top: SOFR OIS curve bootstrap first, then bonds, then equity options, then CDS, then swaptions
7. Calibrate stress shock vectors (Taiwan, AI capex) from historical episodes
8. Run weekly P&L attribution + scenario revaluation

---

*End of document.*
