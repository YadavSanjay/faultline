# TRADE_21 — UST 2Y on-the-run (4.00% 2028-05-31)

**Asset class:** bonds · **Trade type:** `ust_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of the current 2-year US Treasury note. Like the 10Y, it is default-free and discounted on
the Treasury curve — but being short-dated it has much smaller interest-rate sensitivity (low
duration), so its price barely moves when rates shift.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| term | 2Y |
| cusip | 91282CQS3 |
| coupon | 0.04 |
| issue date | 2026-06-01 |
| maturity date | 2028-05-31 |
| coupon frequency | Semiannual |
| day count | ActualActual.Bond |
| Currency | USD |

## Market data

**Conceptually required:** the risk-free Treasury discount curve and the schedule.

**Actually used (free sources):** the Treasury par-yield curve from
[`treasury_par_yields.csv`](../../../data/marketdata/bonds/treasury_par_yields.csv), bootstrapped to
discount factors. See [Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine` on the bootstrapped UST par curve.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $113.37mm**.

| Measure | Value |
|---|---|
| `clean_price` | 98.76 |
| `dirty_price` | 100.1 |
| `accrued` | 1.391 |
| `ytm` | 4.792% |
| `mod_duration` | 1.551 |
| `convexity` | 3.226 |
| `dv01` | $17,590 |
| `notional` | $113.22mm |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Swap-spread basis (F3)** vs the #22 SOFR IRS; real-rate sensitivity (F6, front-end).

## Limitations & assumptions

UST curve from par yields; core-book face; default-free by construction.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Treasury curve bootstrap | [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) (`build_treasury_curve`) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
