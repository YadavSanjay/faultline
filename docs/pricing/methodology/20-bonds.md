# Fixed-rate bonds — corporate & Treasury

*Used by trades 15–19 (corporate: AAPL, MSFT, ORCL, META, AMZN), 20–21 (on-the-run UST 10Y
and 2Y), and 36 (UST 1Y liquidity buffer).*

---

## In plain English

A fixed-rate bond is a loan with a schedule: the issuer pays you a fixed **coupon** twice a year
and returns your **principal** at maturity. Its value today is just the sum of all those future
payments, each **discounted** back to today. The catch is *which* discount rate to use:

- A **US Treasury** is (effectively) risk-free, so we discount it on the **Treasury curve** built
  from official par yields.
- A **corporate** bond can default, so investors demand extra yield. We discount it on the
  **SOFR curve plus a credit spread** for that issuer — the riskier the issuer, the bigger the
  spread, the lower the price.

Bonds are quoted as a price per 100 of face. The **clean price** excludes the coupon interest
that has built up since the last payment; the **dirty price** includes it. You pay dirty, you
quote clean, and the difference is the **accrued interest**.

---

## How it's priced

Both kinds use `ql.FixedRateBond` priced with `ql.DiscountingBondEngine`: build the coupon +
redemption schedule, discount every cash flow on the relevant curve, and read off price,
accrued, yield and risk.

> **Quantitative detail.** `dirty_price = Σ_i coupon·DF(t_i) + 100·DF(T)` (per 100 face);
> `clean_price = dirty_price − accrued`; `PV = dirty_price/100 × notional`. From the model price
> QuantLib also returns **yield to maturity** (the single rate that reprices the bond), **modified
> duration**, **convexity**, and **DV01** (`= modified_duration × dirty_value × 1e-4`, the dollar
> loss per 1bp parallel yield rise). For corporates we additionally back out the **Z-spread** — the
> constant spread over SOFR that reproduces the model price. Code:
> [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py).

### Treasury: bootstrap a par curve

The UST curve is bootstrapped from the official daily par-yield table. Each tenor's par yield is
treated as the coupon of a par bond (price = 100) via `ql.FixedRateBondHelper`, and QuantLib
fits a `PiecewiseLogCubicDiscount` curve through them. Conventions: `ActualActual(Bond)` day
count, Semiannual coupons, `UnitedStates.GovernmentBond` calendar, T+1 settlement. Par bonds
reprice to ≈100 under the fitted curve — the self-consistency check.

### Corporate: SOFR + issuer credit curve

We take the SOFR curve (see [01-sofr-multicurve.md](01-sofr-multicurve.md)) and overlay an issuer
credit spread so every zero rate becomes `SOFR(t) + spread(t)`. Conventions: `30/360` day count,
Semiannual coupons, T+2 settlement, recovery 40%.

> **Term structure (A2/A3).** Where the DTCC single-name CDS feed gives the issuer **two or more
> clean tenors** (currently ORCL), we build a **Z-spread term structure** — the CDS par spread at
> each tenor plus the **maturity-interpolated** SOFR-vs-Treasury basis — and discount on a
> `ql.SpreadedLinearZeroInterpolatedTermStructure` (linear in the zero-rate spread between quoted
> tenors, flat-extrapolated past the last one). This respects the shape of the issuer's credit
> curve: a steep IG curve (e.g. ORCL ~44 bp at 1Y rising to ~219 bp at 5Y) discounts near-term
> coupons at a much lower spread than the 5-year point, which a single flat spread would over-
> penalise. For issuers with only one clean tenor (META) or none (AAPL/MSFT/AMZN → config
> fallback), we overlay a **single flat spread** with `ql.ZeroSpreadedTermStructure`, exactly as
> before. Because CDS quotes reach only ~5Y while these bonds run to 2053/2054, the spread is held
> **flat beyond the last quoted tenor**.

> **Where the spread comes from.** We try the free DTCC single-name CDS feed first (filtered to
> the issuer, sane 5–500 bp band, ≥3 observations, **new-trade prints only** — see
> [50-credit-cds.md](50-credit-cds.md)), lifted to a Z-spread over SOFR by adding the derived
> SOFR-vs-Treasury basis. The feed covers ORCL and META (on 2026-10-06: 5Y CDS ≈199 bp and ≈88 bp).
> AAPL, MSFT and AMZN aren't in the feed's issuer list yet, so they use a **per-issuer config
> spread** (`credit_spread.issuer_fallback_bp`): AAPL/MSFT 60 bp, AMZN 75 bp. These fallbacks
> already fold in the SOFR-vs-Treasury swap spread. The diagnostic `credit_spread_from_cds` tells
> you which path was used.

---

## Market data

**Conceptually required:** a discount curve appropriate to the issuer's credit, the bond's
contractual schedule, and (for corporates) a credit spread / default assumption.

**Actually used (free sources):**

| Input | Source file | Free source | Notes |
|---|---|---|---|
| UST par yields | [`treasury_par_yields.csv`](../../../data/marketdata/bonds/treasury_par_yields.csv) | US Treasury daily par curve | tenors 6M…30Y |
| SOFR curve (corp discount base) | Pensford forwards | see [01-sofr-multicurve.md](01-sofr-multicurve.md) | |
| Issuer credit spread | DTCC single-name CDS → else config fallback | DTCC SDR | per-tenor median (Z-spread curve where ≥2 tenors, else flat 5Y); fallback per issuer |
| Bond terms (coupon, maturity, CUSIP) | `portfolio.json` / bonds config | TreasuryDirect, EDGAR | |

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `clean_price` / `dirty_price` / `accrued` | per 100 face |
| `ytm` | yield to maturity (decimal) |
| `mod_duration` / `convexity` | price sensitivity to yield (1st / 2nd order) |
| `dv01` | dollar loss per 1bp parallel yield rise |
| `credit_spread_bp` / `z_spread_bp` | input spread / recovered Z-spread (corp only) |
| `recovery` | assumed recovery (40%, corp only) |
| `credit_spread_source` / `credit_spread_from_cds` | `dtcc` or `config-fallback` |
| `engine` / `discount` | `DiscountingBondEngine`; UST par-curve vs `ZeroSpreadedTermStructure(SOFR, spread)` |

---

## Limitations & assumptions

- **Corporate spreads are mostly config fallbacks** — the free single-name CDS feed is too sparse
  for clean daily marks. Production would use Markit or TRACE-implied spreads.
- **Bullet bonds only; no callable/OAS (C6).** All seven bonds in the book are non-callable
  bullets (no call schedule on any ticket), so we price plain `ql.FixedRateBond` and report a
  Z-spread — there is no optionality to model. A callable / make-whole corporate would instead use
  `ql.CallableFixedRateBond` with a short-rate-model engine (e.g. `TreeCallableFixedRateBondEngine`,
  the same Hull-White machinery as the Bermudan swaption) and report an **OAS** rather than a
  Z-spread. This is deliberately not built until a callable enters the book (no dead, untested
  code path).
- **Credit curve only where the CDS feed supports it** — ORCL gets a ~2–3 tenor Z-spread term
  structure; thin names (META) and fallback issuers still use a single flat spread, and every
  issuer's spread is held flat past ~5Y (CDS quotes don't reach the 2053/2054 maturities).
  **Flat 40% recovery**; CDS→bond basis held at zero.
- **UST curve from par yields**, front bills excluded to avoid repo-basis noise. The on-the-run
  UST marks are **cross-checked against FINRA TRACE** traded prices (C5): the model clean price of
  the 10Y (#20) and 2Y (#21) is validated against FINRA's on-the-run VWAP for the matching maturity
  bucket (a loose sanity gate — the FINRA figure is a bucket VWAP, not the exact CUSIP). The DTCC
  cleared-swap SDR feed was evaluated as an independent check on the *SOFR* curve but is **too
  sparse** (≈1 clean spot-start 10Y quote near a valuation date); the SOFR curve is instead
  validated against the realized SOFR fixing and the 10Y SOFR-vs-UST swap spread.
- **Face amounts ($50mm) are placeholders.**

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer (corp + UST) | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Curve builders (`build_treasury_curve`, `build_zero_spreaded_curve`) | [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Market-data adapter (treasury curve, credit spread) | [`src/pricer/loader/bonds_adapter.py`](../../../src/pricer/loader/bonds_adapter.py) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
| Config | `bonds.*`, `credit_spread.*` in [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json) |
