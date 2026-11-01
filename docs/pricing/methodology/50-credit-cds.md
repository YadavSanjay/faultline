# Credit default swaps (ISDA model)

*Used by trades 28–30 (single-name ORCL & META buy protection; CDX.NA.IG index sell protection).*

---

## In plain English

A **credit default swap (CDS)** is insurance against a borrower defaulting. The **protection
buyer** pays a small running premium (a coupon, e.g. 100 bp/year) and, if the reference entity
defaults, receives a payout for the loss. The **protection seller** collects the premium and pays
out on default. Buying protection is a *hedge* (or a bearish credit bet); selling protection earns
*carry* (a bullish credit bet).

Two of our trades **buy** protection on single names (ORCL, META) to hedge the corresponding
corporate bonds; one **sells** protection on the **CDX.NA.IG** index (125 investment-grade names)
for carry/diversification.

Pricing a CDS means weighing two streams against each other:

- the **premium leg** — the coupons you pay (or receive), but only for as long as the name hasn't
  defaulted (so each coupon is weighted by the **survival probability**), and
- the **protection leg** — the expected payout on default, `(1 − recovery)`, discounted and
  weighted by the probability of defaulting in each period.

The PV is the difference. The crucial unknown is **how likely default is** — the **hazard rate** —
which we back out from the market's quoted **par spread**.

---

## How it's priced — calibrate a hazard, then value the legs

### 1. From par spreads to a hazard curve

The market quotes a **par spread** (the fair running premium) at each traded tenor. We convert
these into a **hazard rate** `λ` — the instantaneous probability of default per year — such that a
par CDS reprices to zero.

When the free DTCC feed gives us **two or more clean tenors** for a name (as it does for ORCL:
a 1Y and 5Y, sometimes a 3Y), we bootstrap a **piecewise-flat hazard curve** — a real *term
structure* of default risk — instead of assuming one flat hazard across all horizons. Where the
feed is thin (META, only the on-the-run 5Y trades) or the reference is an **index** (CDX), we fall
back to a single flat hazard.

> **Quantitative detail (flat).** The "credit triangle" gives a seed `λ ≈ s/(1−R)` (spread `s`,
> recovery `R`). We refine `λ` by a secant root-find so a par CDS (coupon = par spread) priced
> under the ISDA engine has NPV ≈ 0. Survival `Q(t) = exp(−λ·t)`. Built as `ql.FlatHazardRate`.
> Code: `calibrate_flat_hazard`.
>
> **Quantitative detail (piecewise, A2).** Walking the par spreads by **ascending tenor**, we
> solve each segment's constant forward hazard `λ_k` so that a par CDS to that tenor's standard
> maturity reprices to zero under the ISDA engine, **holding the shorter segments fixed** — a
> sequential secant bootstrap. The survival curve is a BackwardFlat `ql.HazardRateCurve`
> (constant forward hazard per bucket, extrapolated flat past the last quoted tenor). Because a
> flat *spread* curve implies a mildly non-flat *hazard*, this is not the same as one flat hazard —
> a steep credit curve (low near-term, higher forward hazard) reshapes the survival probabilities
> and the risky annuity. The 5Y node is pinned to the tight-window headline spread so the
> fair-spread == par-spread round-trip still holds. Code: `calibrate_piecewise_hazard`.
> [`src/pricer/ql/credit/cds_pricer.py`](../../../src/pricer/ql/credit/cds_pricer.py).

### 2. Value with the ISDA engine

With the hazard (survival) curve and the SOFR discount curve, the standardized CDS is valued with
`ql.IsdaCdsEngine` at the market convention of **100 bp running coupon, 40% recovery**.

> **Quantitative detail.**
> `PV_premium = coupon · Σ_i τ_i·DF(t_i)·Q(t_i)` (the "risky annuity"),
> `PV_protection = (1−R)·∫ DF(t)·(−dQ(t))` over the life.
> For a **buyer**, `PV = PV_protection − PV_premium`; for a **seller** the sign flips. Because the
> traded coupon (100 bp) differs from the par spread, the trade settles with an **upfront**
> (`upfront_pts = PV/notional × 100`). **CS01** is the PV change for a 1bp bump in the par spread
> (recalibrate λ, reprice). Schedule: standard ISDA 2015 quarterly IMM dates, `Act/360`,
> WeekendsOnly calendar, Following.

### Discount curve note

The ISDA engine requires a **flat-forward** discount curve, so for CDS the SOFR par rates are
bootstrapped with `PiecewiseFlatForward` interpolation rather than the log-cubic used elsewhere
(see [01-sofr-multicurve.md](01-sofr-multicurve.md)).

---

## Market data

| Input | Source file | Free source | Notes |
|---|---|---|---|
| Single-name par spreads (term structure) | [`dtcc_single_name_cds.csv`](../../../data/marketdata/credit/dtcc_single_name_cds.csv) | DTCC SDR (SEC path) | 5Y headline on a tight window; 1/2/3/7/10Y buckets on a wider window feed the hazard-curve bootstrap (median per tenor) |
| Index 5Y par spread | [`dtcc_index_cds.csv`](../../../data/marketdata/credit/dtcc_index_cds.csv) | DTCC SDR (CFTC path) | CDX.NA.IG |
| Fallback spreads | config `credit.references.*.fallback_bp` | — | ORCL 175, META 74, CDX.NA.IG 55 |
| SOFR discount curve | Pensford forwards | [01-sofr-multicurve.md](01-sofr-multicurve.md) | flat-forward interpolation |

**Only new trades price.** The DTCC files keep every disseminated record (keyed on the CFTC
Dissemination Identifier, with corrections and cancellations applied — spec
[`credit-sdr-dedup`](../../specs/credit-sdr-dedup.md)), so the medians use only **price-forming
prints**: `Action type` `NEWT` with `Event type` `TRAD`. Lifecycle events, novations, clearing
records and corrections are excluded. Prints are windowed by **execution date**, and nothing
disseminated after the valuation date is used. On 2026-10-06 the 5Y bucket held 178 ORCL, 89 META
and ~27,000 CDX.NA.IG new trades over the 120-day window.

If fewer than 3 usable spreads are found in the lookback window, the config fallback is used; the
`spread_from_dtcc` diagnostic records which path applied.

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `engine` | `IsdaCdsEngine + calibrated flat hazard` |
| `reference` / `kind` / `protection` | name/index, single-name vs index, buy/sell |
| `par_spread_bp` / `fair_spread_bp` | market par spread / model fair spread (equal by construction) |
| `running_coupon_bp` | standardized coupon (100 bp) |
| `upfront_pts` | upfront settlement as % of notional |
| `cs01` | PV change per 1bp of par-spread (signed; negative when short credit) |
| `rec01` | PV change per +1pt recovery, hazard re-calibrated to the same spread (recovery risk) |
| `hazard_bp` / `survival_to_maturity` | calibrated hazard / `Q(5Y)` |
| `recovery` | assumed recovery (40%) |
| `leg_pvs.coupon` / `leg_pvs.default` | premium-leg and protection-leg PVs |

---

## Limitations & assumptions

- **Sparse term structure** — since A2 (2026-07-12) single names bootstrap a piecewise-flat hazard
  *curve* where the feed supports it (ORCL gets ~2–3 tenors), but the free DTCC feed trades mostly
  the on-the-run 5Y, so thin names (META) and the index (CDX) still price on a single flat hazard,
  and the hazard is extrapolated flat past the last quoted tenor.
- **Fixed 40% recovery** — no issuer-specific recovery data. Its risk is now measured by **`rec01`**
  (B5): the PV change for +1pt recovery with the hazard re-calibrated to the same observed spread.
  This is small by construction (holding the spread fixed, a higher recovery is offset by a higher
  hazard), which is why the *mark* is insensitive to the recovery assumption — the large recovery
  effect is the **default loss**, captured separately by the jump-to-default add-on in the risk engine.
- **Index priced at the index level (B4)** — CDX.NA.IG is valued as a flat hazard from the quoted
  index spread with the standard fixed coupon + upfront, on the **outstanding notional** (traded ×
  `index_factor`). The `index_factor` is **1.0** for the current on-the-run series (the free feed
  carries no defaulted-name data) and is a hook that drops below 1 as constituents default;
  `n_constituents` (125) is reported. This is the market-standard *untranched* index-level model —
  there is **no constituent-curve aggregation or tranche granularity** (a documented simplification;
  it needs per-name curves / tranche data to go further).
- **Running spreads are often blank** on single-name prints (the economics ride on upfront `Price`), so
  only ~1 in 4 single-name new trades carries a usable spread; fallbacks fill a name only when its
  window is thin. Corrected trades are excluded (a `CORR` doesn't say what it corrected). No CDS-bond
  basis adjustment. (Before 2026-10-08 the feed looked "sparse" largely because ingest kept one row per
  day — fixed in `credit-sdr-dedup`.)

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer + hazard calibration | [`src/pricer/ql/credit/cds_pricer.py`](../../../src/pricer/ql/credit/cds_pricer.py) |
| Market-data adapter (DTCC filtering) | [`src/pricer/loader/credit_adapter.py`](../../../src/pricer/loader/credit_adapter.py) |
| Trade definition | [`src/pricer/input/credit/instruments.py`](../../../src/pricer/input/credit/instruments.py) |
| Config | `credit.*` in [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json) |
