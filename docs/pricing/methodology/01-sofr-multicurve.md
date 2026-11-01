# The SOFR curve — discounting & forecasting USD rates

*Used by rates (22–25), bonds (15–21, 36), equity options (7–14, 35), FX (26–27) and credit (28–30).*
This is the single most reused piece of machinery in the book: the USD discount/forecast curve.
Get this right and most of the portfolio falls out of it.

---

## In plain English

To value any future dollar we need a **discount curve** — a schedule telling us what $1 paid on
any future date is worth today. For US dollars, the market's risk-free building block is
**SOFR** (the Secured Overnight Financing Rate), the rate banks pay to borrow cash overnight
against Treasuries. The whole curve of interest rates — overnight out to 10 years and beyond —
is built from SOFR.

We don't have a paid market-data terminal, so we get the SOFR forward curve from **Pensford**, a
firm that publishes a free forward-SOFR projection. From those projected forward rates we
reconstruct what the market is implying for borrowing over 1 month, 3 months, … 10 years, and
hand those to QuantLib, which "bootstraps" a smooth curve of discount factors. Every USD trade
then reads its discount factors off that one curve.

> **Where the Pensford curve actually comes from (important).** Pensford's public *archive*
> page only publishes **month-end** Excel files, and it lags — at the 2026-10-06 ingest the newest
> archived file was still 2026-05-29. The *live* `/forward-curve` page ("Updated Daily") is backed by a public
> **Supabase** database (`forward_curve` = current curve, `archive_forward_curve` = history),
> which carries the full daily forward path. We ingest the **full monthly path** from that source
> via [`scripts/rebuild_pensford_from_supabase.py`](../../../scripts/rebuild_pensford_from_supabase.py).
> This matters because the curve builder needs the **whole path**, not one point (see the caveat
> under Step 1).

A subtlety worth knowing: SOFR is an *overnight* rate, so the same curve serves two jobs at
once — it **discounts** future cash flows *and* it **forecasts** the floating SOFR a swap will
pay. That's the "single-curve OIS" world. (A full dealer system keeps several curves — a
discount curve and separate forecast/basis curves — and the code is structured to support that;
today we use one because free data doesn't give us the basis spreads.)

---

## What "SOFR OIS" means

An **OIS** (Overnight Indexed Swap) exchanges a fixed rate for the compounded overnight rate
(SOFR) over the life of the trade. SOFR-OIS rates are the cleanest available proxy for the USD
risk-free curve, which is why post-LIBOR they are *the* discounting standard. Our curve is built
to reprice par OIS instruments, and the same curve both discounts and projects SOFR.

---

## Step 1 — from Pensford forwards to par-OIS rates

The raw input is
[`data/marketdata/rates/pensford_sofr_forward.csv`](../../../data/marketdata/rates/pensford_sofr_forward.csv),
a monthly grid of projected forward SOFR (`as_of_date, settlement_date, forward_sofr, …`,
Act/360 compounding). We turn that grid into clean discount factors and then into **par-OIS
rates** at standard tenors.

> **Quantitative detail.** Starting from `DF(t_0)=1`, march along the monthly grid compounding
> each forward over its accrual `τ_i = (settle_{i+1} − settle_i)/360`:
> `DF_{i+1} = DF_i / (1 + f_i · τ_i)`. Interpolate `DF` (log-linear) to each standard tenor
> date, then invert to a par-OIS rate: for tenor `T` with annual coupons,
> `par(T) = (1 − DF(T)) / Σ_k τ_k·DF(t_k)` (the numerator is the floating-leg value, the
> denominator the fixed-leg annuity). For sub-1Y tenors it's a single payment at maturity.
> Code: [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py).

Default tenors (config `rates.par_tenors`): **1M, 3M, 6M, 9M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y,
12Y, 15Y, 20Y, 30Y**.

> **Long-end nodes (added 2026-07-12).** The grid used to stop at 10Y, so anything longer
> — the ORCL 2053 (#17) and META 2054 (#18) corporate bonds, and the ~15Y underlying of the
> 5Y×10Y swaption (#23) — was discounted on a curve that ran **flat** past the 10Y node,
> systematically over-valuing those long cashflows. Each Pensford snapshot actually carries
> ~241 monthly forwards out to ~2046 (~20Y), so **12Y / 15Y / 20Y are now real bootstrapped
> nodes**. Beyond the ~20Y data horizon the discount factor is extended by **constant
> instantaneous-forward extrapolation** (the log-DF is continued along the slope of the last
> monthly segment — i.e. the final forward is held flat), which synthesises a meaningful
> **30Y** node to bracket the 27–28Y bonds, instead of `np.interp`'s flat clamp (which would
> freeze the DF and imply a ~0% forward past 20Y). Effect at 2026-05-29: the long end steepens
> ~4.03% (10Y) → 4.28% (20Y) → 4.35% (30Y); META-2054 −1.6%, ORCL-2053 −1.4%, swaption −0.3%.

> **⚠️ The forward path must be complete — a single point degenerates the curve to ~0%.**
> The compounding above needs *many* settlement rows per snapshot. If a snapshot has only one
> forward point, the `DF_{i+1}` loop never runs, `DF ≡ 1.0` everywhere, and every par-OIS rate
> comes out **0%** — so swaps/bonds/CDS silently discount at zero (a $50mm 10Y payer then "prices"
> at ≈ −fixed·annuity·notional, a pure artifact). This was a real bug: the archive-Excel
> downloader ([`src/marketdata/rates/pensford_downloader.py`](../../../src/marketdata/rates/pensford_downloader.py))
> collapses the monthly path because `upsert_csv(date_col="as_of_date")` de-dups on `as_of` alone.
> **Fix:** the archive downloader now de-dups on `(as_of_date, settlement_date)` (not `as_of`
> alone), so `--sources pensford` no longer collapses the path. But the Pensford *archive* only
> publishes month-end files and lags (newest still 2026-05-29 at the 2026-10-06 ingest), so the
> **current** curve is ingested from the live page's Supabase source via
> `python scripts/rebuild_pensford_from_supabase.py`. After the fix the curve is a real ~4%
> (1M 3.68% → 10Y 4.14% at 2026-07-10); at the 2026-10-06 default the forward path runs from
> 3.92% (first reset) to 5.18% (10Y out).

## Step 2 — bootstrap with QuantLib

Each par-OIS rate becomes a `ql.OISRateHelper` on a `ql.Sofr` index; QuantLib bootstraps a
`PiecewiseLogCubicDiscount` curve (log-cubic spline on discount factors) through them.

> **Quantitative detail / conventions.** Settlement T+2, `ql.UnitedStates.GovernmentBond`
> calendar, ModifiedFollowing roll, `Annual` pillar choice, curve day-count `Actual365Fixed`,
> par-rate accrual `Act/360` (SOFR convention). Extrapolation is enabled so we can read past the
> last pillar. The bootstrap finds the discount factors that exactly reprice every input OIS
> helper to par. Code:
> [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py)
> (`build_ois_curve`).

**One exception — credit.** The ISDA CDS engine requires a flat-forward curve, so for CDS we
bootstrap the *same* par rates with `PiecewiseFlatForward` interpolation instead of log-cubic
(see [50-credit-cds.md](50-credit-cds.md)).

## Step 3 — read discount factors off the curve

Once built, the curve answers two questions for any future date `t`: **`DF(t)`** (for
discounting) and the **forward SOFR** over any future accrual period (for projecting a floating
leg). Both come from the same object — that's the single-curve OIS setup.

---

## Single-curve today, multi-curve ready

The loader returns a *discount* curve and a *forecast* curve. Today they are the same object
(we have no free OIS-vs-SOFR or cross-currency basis data), so
[`base_pricer.py`](../../../src/pricer/ql/common/base_pricer.py) detects identical inputs and
builds one curve, handing the same handle to both roles. The moment a basis curve becomes
available, the loader returns two different curve definitions and both are built and cached
independently — **no pricer code changes**. This is what "multi-curve ready" means in practice.

## Curve caching

Bootstrapping is the expensive step, so it is **memoised per run** on the *content* of the curve
data (not object identity): the first rates/bonds/FX/credit trade in a worker builds the SOFR
curve, every subsequent trade reuses it. On a rates-heavy book this turns ~30 curve builds into
a handful. Code:
[`src/pricer/ql/common/curve_cache.py`](../../../src/pricer/ql/common/curve_cache.py).

---

## Free-data limitations

- **Staleness.** Pensford publishes intermittently; on a valuation date well after the last
  snapshot you'll see a `warn`-level staleness flag (e.g. "19d old, tolerance 7d"). The curve is
  still used — the policy is `warn`, not `block`. **Credit is the exception**: the ISDA CDS engine
  requires the discount-curve reference date to *equal* the valuation date, so CDS only prices when
  a snapshot `as_of` == the valuation date (nearest-on-or-before elsewhere). The book therefore
  values at a date with an exact Pensford snapshot — currently **2026-10-06** (the live Supabase
  curve), with 2026-07-10 and 2026-05-29 also on file.
- **Archive lags; live curve via Supabase.** The Pensford archive only posts month-end files
  (newest still 2026-05-29 at the 2026-10-06 ingest); the current curve comes from the live page's Supabase DB and is
  ingested by `scripts/rebuild_pensford_from_supabase.py`.
- **No basis curves.** Single SOFR curve for discount and forecast; no SOFR-vs-Fed-funds basis,
  no cross-currency basis. Adequate for a USD-centric book; a known simplification for
  multi-currency or basis trades.
- **No traded swaption/cap vol surface.** Volatility for rate options is a separate gap handled
  with a flat normal vol — see [31-swaptions.md](31-swaptions.md) and [33-caps.md](33-caps.md).

## Where this lives in the code

| Component | Path |
|---|---|
| Par-OIS construction from Pensford forwards | [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py) |
| Curve bootstrap (`build_ois_curve`, `build_treasury_curve`, `build_zero_spreaded_curve`) | [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Single-/multi-curve resolution | [`src/pricer/ql/common/base_pricer.py`](../../../src/pricer/ql/common/base_pricer.py) |
| Curve cache | [`src/pricer/ql/common/curve_cache.py`](../../../src/pricer/ql/common/curve_cache.py) |
| Input file | [`data/marketdata/rates/pensford_sofr_forward.csv`](../../../data/marketdata/rates/pensford_sofr_forward.csv) |
| Config | `rates.*` in [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json) |
