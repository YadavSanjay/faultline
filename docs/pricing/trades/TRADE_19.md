# TRADE_19 — Amazon 4.70% 2032

**Asset class:** bonds · **Trade type:** `corp_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of Amazon's 4.70% bond maturing 2032 — a high-grade, intermediate-maturity corporate. It
pays 4.70% semiannually and returns principal in 2032; valued as those cash flows discounted on SOFR
plus a small Amazon credit spread.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| issuer | AMZN |
| cusip | 023135CR5 |
| coupon | 0.047 |
| issue date | 2022-12-01 |
| maturity date | 2032-12-01 |
| coupon frequency | Semiannual |
| day count | 30/360 |
| Currency | USD |

## Market data

**Conceptually required:** a discount curve reflecting Amazon's credit, plus the schedule.

**Actually used (free sources):** SOFR curve + issuer spread. The **config fallback of 75 bp**
(`issuer_fallback_bp.AMZN`) applies when the DTCC feed is too thin. See
[Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine` on `ZeroSpreadedTermStructure(SOFR, 75bp)`.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $110.09mm**.

| Measure | Value |
|---|---|
| `clean_price` | 95.6 |
| `dirty_price` | 97.26 |
| `accrued` | 1.658 |
| `ytm` | 5.553% |
| `mod_duration` | 5.156 |
| `convexity` | 31.85 |
| `dv01` | $56,778 |
| `notional` | $113.22mm |
| `credit_spread_bp` | 75 |
| `z_spread_bp` | 76.94 |
| `recovery` | 0.4 |
| `credit_curve_tenors` | 1 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Real rates (F6):** IG credit, real/breakeven decomposition.

## Limitations & assumptions

Config-fallback spread; flat spread/recovery; core-book face.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Credit spread / curve | [`src/pricer/loader/bonds_adapter.py`](../../../src/pricer/loader/bonds_adapter.py), [`curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
