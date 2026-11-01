# TRADE_20 — UST 10Y on-the-run (4.375% 2036-05-15)

**Asset class:** bonds · **Trade type:** `ust_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of the current 10-year US Treasury note. Treasuries are (effectively) default-free, so
there is no credit spread — the bond is discounted purely on the **Treasury curve** built from the
official daily par-yield table. Its value is the coupons and principal discounted on that curve.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| term | 10Y |
| cusip | 91282CQQ7 |
| coupon | 0.04375 |
| issue date | 2026-05-15 |
| maturity date | 2036-05-15 |
| coupon frequency | Semiannual |
| day count | ActualActual.Bond |
| Currency | USD |

## Market data

**Conceptually required:** the risk-free Treasury discount curve and the bond's schedule.

**Actually used (free sources):** the Treasury par-yield curve from
[`treasury_par_yields.csv`](../../../data/marketdata/bonds/treasury_par_yields.csv) (US Treasury),
bootstrapped to discount factors. No SOFR, no credit spread. See
[Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine` on the bootstrapped UST par curve
(`PiecewiseLogCubicDiscount`, ActualActual(Bond), T+1 settlement).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $107.51mm**.

| Measure | Value |
|---|---|
| `clean_price` | 93.24 |
| `dirty_price` | 94.96 |
| `accrued` | 1.724 |
| `ytm` | 5.281% |
| `mod_duration` | 7.533 |
| `convexity` | 69.11 |
| `dv01` | $80,991 |
| `notional` | $113.22mm |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Swap-spread basis (F3):** this UST is hedged by the #22 SOFR IRS; `funding_basis_squeeze`'s swap-spread bump moves the Treasury curve, not the swap (bonds discount off OIS).
- **Real rates (F6):** on-the-run 10Y — the belly of the real-rate move.

## Limitations & assumptions

UST curve from par yields with front bills excluded; core-book face. No credit risk by construction.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Treasury curve bootstrap | [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) (`build_treasury_curve`) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
