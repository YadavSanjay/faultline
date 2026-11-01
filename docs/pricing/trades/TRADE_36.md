# TRADE_36 — UST 1Y note, long (short-duration liquidity buffer / dry powder)

**Asset class:** bonds · **Trade type:** `ust_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of a 1-year US Treasury note — added in the Part-2 rebalance as a **cash / liquidity buffer**
(dry powder) behind a levered book. Default-free and very short-dated, so its price barely moves with
rates (low DV01); it is ballast and a margin buffer, not a directional rate view.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| term | 1Y |
| coupon | 0.04 |
| issue date | 2026-06-15 |
| maturity date | 2027-06-15 |
| coupon frequency | Semiannual |
| day count | ActualActual.Bond |
| Currency | USD |

## Market data

**Conceptually required:** the risk-free Treasury discount curve and the schedule.

**Actually used (free sources):** the Treasury par-yield curve from
[`treasury_par_yields.csv`](../../../data/marketdata/bonds/treasury_par_yields.csv) (which carries
1M–30Y nodes, so the 1Y point is a real quote), bootstrapped to discount factors. See
[Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine` on the bootstrapped UST par curve.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $114.47mm**.

| Measure | Value |
|---|---|
| `clean_price` | 99.87 |
| `dirty_price` | 101.1 |
| `accrued` | 1.246 |
| `ytm` | 4.188% |
| `mod_duration` | 0.6648 |
| `convexity` | 0.7721 |
| `dv01` | $7,611 |
| `notional` | $113.22mm |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Liquidity buffer (Part 2):** short-duration UST held as dry powder. Low front-end DV01, default-free; carries real-rate (F6) front-end sensitivity; negligible stress P&L.

## Limitations & assumptions

Representative 1Y note (coupon/dates are placeholders; no CUSIP — it stands in for a T-bill/short-UST
cash sleeve); UST curve from par yields; face is the core-book size; default-free by construction.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Treasury curve bootstrap | [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) (`build_treasury_curve`) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
