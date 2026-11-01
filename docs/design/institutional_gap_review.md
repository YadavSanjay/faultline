# Pricing data and methodology: our gaps vs a bank

> **Status: outline.** Key points and provisional numbers (valuation date 2026-10-06, core book).
> Sections are filled in as the data-pipeline enhancements land (see "Refine after" in each).
>
> **Purpose.** For each asset class in the book, compare what a medium or large financial
> institution uses (licensed data, larger methodology and quant teams, internal data such as
> collateral terms) with what we use under our constraints (free public data only, small team).
> Measure what each gap is worth on our actual trades, and rank the fixes.
>
> Related: [AITrade_risk_data_gap_analysis.md](AITrade_risk_data_gap_analysis.md) covers the risk
> module's factor histories; this document covers **pricing** inputs and methodology.

**Each asset-class section follows the same shape:** what a bank uses → what we use → measured
impact on our trades → fixes (with or without a licence).

## 1. Summary (write last)

- One line per asset class: size of the gap in $ on our book, and the top fix.
- Current headline points:
  - **Rates:** USD curve within ~1 bp to 20Y; ~11 bp too high at 30Y (our extrapolation), ≈ +$0.9M,
    almost all in the two long corporate bonds.
  - **FX:** the largest gap relative to trade value, on the USD/TWD swap (#26).
  - **Credit:** much of "free data is too thin" was our ingest bug, now fixed.

## 2. Cross-cutting gaps

- **Licensing:** no Bloomberg, Refinitiv, Markit, ICE, CME or broker data. Free public sources only,
  with some vendor projections we can't audit (e.g. Pensford).
- **Timeliness:** end-of-day or delayed snapshots, sometimes weekly (option chains); a bank marks
  from intraday or official close data.
- **Point-in-time history:** files keep only the latest version of a corrected record, so pricing a
  past date sees later corrections.
- **Collateral terms:** no counterparty collateral data, so everything discounts at SOFR. A bank
  chooses the discount curve per collateral agreement.
- **Quotes vs trades:** a bank marks to dealer quotes and composites; we infer levels from
  disseminated trades (DTCC), which are noisier but real.
- **Lesson from the DTCC fixes:** check that ingest is complete before blaming the free source.
- *Refine after:* the asset-class sections; keep only what is genuinely cross-cutting.

## 3. Rates: the SOFR curve

**How we build it vs a bank**
- **Ours:** NY Fed SOFR fixing + Pensford monthly forwards (~20Y) → 14 par rates → one QuantLib
  curve for discounting and forecasting; last forward held flat past ~20Y.
  Detail: [01-sofr-multicurve.md](../pricing/methodology/01-sofr-multicurve.md).
- **Bank:**
  - SOFR futures and meeting-dated swaps at the front, with a convexity adjustment calibrated to vol;
  - swap rates 1Y–50Y from broker composites and clearing-house curves;
  - Fed Funds/SOFR basis and Term SOFR curves;
  - collateral-specific discounting.
- **Construction is not the problem:** the curve reproduces Pensford's forwards to ~1.7 bp RMS (<1Y),
  and rebuilding from raw discount factors changes the book by < $50k.

**Multi-curve for this book**
- Tenor basis: not applicable; all rates trades are on overnight SOFR.
- Index basis: only if the cap (#25) were Term SOFR; ~$3k.
- Collateral basis: < $10k per trade (Fed Funds/SOFR); uncollateralised at +50 bp ≈ $65k on #22.
- Cross-currency: yes; see FX (section 6).
- The code already separates discount and forecast curves; what's missing is data, not structure.

**Measured accuracy**
- Benchmark: median of new, spot-starting, standard SOFR swaps on the DTCC tape (465 trades,
  2026-10-06).
- Ours − market: −0.7 to −1.5 bp at 1Y–20Y; **+11.4 bp at 30Y**. The market's 20s30s is inverted
  ~8 bp; ours slopes up.
- Impact:
  - #18 META 2054 bond: +$0.69M;
  - #17 ORCL 2053 bond: +$0.33M;
  - everything else < ±$70k;
  - book ≈ +$0.88M (key-rate DV01s on 2026-10-08 data; was ≈ +$1.0M before the credit re-ingest widened the long bonds' spreads).
- Note to keep: an earlier +$5.1M estimate rested on two prints from the collapsed DTCC file and is
  withdrawn.

**Rates trades #22–25**
- Curve error ~1 bp, so little exposure. Booking issue: the swaptions and cap have no strike and are
  re-struck daily, which hides risk (#23: −$1.6k/bp as booked vs +$7.2k/bp with a fixed strike).

*Refine after:* `sofr-sdr-curve` (long end from DTCC trades; repricing and left-out-trade checks as
the accuracy evidence).

## 4. Rates volatility (swaptions, caps)

- **Bank:** full swaption/cap vol cube (expiry × tenor × strike), SABR or similar, from brokers.
- **Ours:** one MOVE-index level shaped by expiry; flat across strike.
- To measure: vega and smile exposure of #23–25.
- *Refine after:* re-assessing the DTCC options tape, now complete since `sdr-dedup` (option type
  and strike are now kept).

## 5. Bonds: corporate and government

- **Bank:** prices per bond (TRACE, BVAL/ICE, dealer runs); issuer curves from bonds and CDS; Treasury
  prices per bond (BrokerTec, Tradeweb).
- **Ours:**
  - corporates: SOFR + issuer Z-spread, where the spread = CDS + the SOFR-Treasury basis;
  - ORCL/META spreads from DTCC CDS trades; AAPL/MSFT/AMZN hand-set;
  - Treasuries: Treasury's fitted par-yield curve.
- Exposure: $31k–98k per bp per corporate bond. The 30Y curve error (section 3) dominates the long
  bonds. 2 bp of on-the-run richness ≈ $110k on #20.
- *Refine after:* adding AAPL/MSFT/AMZN to the CDS feed; Treasury per-bond prices; `sofr-sdr-curve`.

## 6. FX: forwards and cross-currency swaps

- **Bank:** foreign OIS curves (JPY TONA, TWD), FX forward points, cross-currency basis swaps;
  offshore NDF-implied curve for TWD.
- **Ours:** one flat foreign rate (overnight TONAR; TWD central-bank policy rate 2.00%), zero basis.
- Exposure:
  - #26 USD/TWD swap: ±$95k per 10 bp of TWD rate or basis on a $0.9M trade; −100 bp ⇒ −$0.97M.
  - #27 USD/JPY forward: ±$20k per 10 bp of basis on $80M; a typical −25 bp ≈ −$51k.
- *Refine after:* FX curve work (licensed points/basis, or DTCC FX/cross-currency trades if coverage
  allows).

## 7. Credit: CDS

- **Bank:** Markit composite par curves by tenor; ISDA standard rates curve; index constituents.
- **Ours:** DTCC public trades (now complete), new trades only; flat or piecewise hazard by tenor.
- Status: spreads from 89–178 trades per name (CDX ~27,000) instead of 1–4. The curve barely matters
  (< $0.5k/bp); spreads dominate.
- Known limits: running spreads are often blank on single names (~1 in 4 usable); corrected trades are
  excluded; no CDS-bond basis.
- *Refine after:* `credit-sdr-dedup` merge; decide on widening the issuer list.

## 8. Equity: cash and options

- **Bank:** consolidated real-time or official close prices; corporate-action and dividend feeds;
  full option surfaces (all strikes and expiries, bid/ask); borrow and repo rates.
- **Ours** (from the methodology docs):
  - cash equity: raw daily close, with splits not applied by the pricer;
  - options: CBOE delayed chains, weekly snapshots, so the surface is sparse and may be older than
    the curve;
  - spot and dividend yield are backed out of each chain (put-call parity); American legs use a
    projected discrete dividend schedule;
  - no borrow/repo cost; mid prices only, no bid/ask.
- To measure: sensitivity of #7–14 and #35 to surface staleness, the dividend method and borrow cost.
- *Refine after:* equity pipeline work.

## 9. Fixed so far

- `sdr-dedup` (merged): DTCC rates tape complete (~5–7k SOFR records a day, was one).
- `credit-sdr-dedup` (merged): CDS records complete; new trades only; broken bonds feed retired;
  CDS #28–30 and bonds #17/#18 rebaselined.
- *Append each pipeline change as it merges.*

## 10. Remaining gaps, ranked across asset classes

| # | Gap | Asset class | Fix | Licence? | Value |
|---|---|---|---|---|---|
| 1 | 30Y extrapolation (+11 bp) | Rates | Long end from DTCC swap trades (`sofr-sdr-curve`) | No | ≈ $1.0M |
| 2 | TWD/JPY curves, basis | FX | Licensed points/basis, or DTCC FX trades | Likely | ±$95k per 10 bp on #26 |
| 3 | Swaption/cap booking | Rates | Real strikes and start dates | No | Correct rate risk |
| 4 | Rates vol cube | Rates vol | DTCC options tape, else licensed | Maybe | Vega of #23–25 |
| 5 | AAPL/MSFT/AMZN spreads | Bonds / credit | Add to CDS feed | No | #15/16/19 |
| 6 | Treasury per-bond prices | Bonds | Treasury's free end-of-day prices | No | ~$100k on #20 |
| 7 | Collateral terms | All derivatives | Record per counterparty | No (internal) | Small unless uncollateralised |
| — | Equity gaps | Equity | To assess | — | — |

## Method (to write)

- Production pricing engine at 2026-10-06; one-at-a-time bumps (key-rate, discount-only,
  forecast-only, FX basis).
- Market swap rates: DTCC filter (`NA/Swap OIS USD`, no package/spread, spot-starting,
  anniversary-dated, ≥ 10 trades per tenor).
- Bank practice described generically, not from a specific institution.
- **Reproduce the numbers** (repo root, py312; outputs to `data/analysis/gap_review/<date>/`):
  1. [`curve_sensitivities.py`](../../scripts/analysis/gap_review/curve_sensitivities.py): base PVs,
     curve fidelity, discount/forecast split, key-rate DV01s, FX basis, long-end scenarios.
  2. [`curve_vs_market.py`](../../scripts/analysis/gap_review/curve_vs_market.py): market SOFR par
     rates from the DTCC tape vs our curve (needs the tape for that date on disk).
  3. [`gap_impact.py`](../../scripts/analysis/gap_review/gap_impact.py): $ impact per trade of
     moving our curve onto the market.
  
  Each takes `--valuation-date` (default: the book's default date). Re-run them when refining a
  section, since the numbers move with the data.
