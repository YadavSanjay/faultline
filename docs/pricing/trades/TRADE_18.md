# TRADE_18 — Meta 5.40% 2054 (long-end credit)

**Asset class:** bonds · **Trade type:** `corp_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of Meta's 5.40% bond maturing 2054 — another very long-dated corporate. Like the Oracle
bond, it carries large interest-rate duration and meaningful credit sensitivity. Its issuer credit
risk is separately hedged by the META CDS ([TRADE_29](TRADE_29.md)).

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| issuer | META |
| cusip | 30303M8V7 |
| coupon | 0.054 |
| issue date | 2024-08-15 |
| maturity date | 2054-08-15 |
| coupon frequency | Semiannual |
| day count | 30/360 |
| Currency | USD |

## Market data

**Conceptually required:** a discount curve reflecting Meta's credit, plus the schedule.

**Actually used (free sources):** the SOFR curve — now extended to real 12Y/15Y/20Y/30Y nodes (A1)
so Meta's 2054 cashflows no longer discount on a flat 10Y extrapolation — plus Meta's **real DTCC
single-name CDS 5Y spread** (≈74 bp + the SOFR-Treasury basis → ≈115 bp Z-spread over SOFR). Unlike
Oracle, Meta trades essentially only the on-the-run 5Y, so this stays a **single flat spread** (not
a curve); the config fallback of 95 bp applies only if the feed is too thin. See
[Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine` on `ZeroSpreadedTermStructure(SOFR, issuer Z-spread)`
— a single flat issuer spread (Meta has only the 5Y CDS point, so no term structure to build).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $99.20mm**.

| Measure | Value |
|---|---|
| `clean_price` | 86.84 |
| `dirty_price` | 87.64 |
| `accrued` | 0.795 |
| `ytm` | 6.419% |
| `mod_duration` | 13.22 |
| `convexity` | 269.9 |
| `dv01` | $131,201 |
| `notional` | $113.22mm |
| `credit_spread_bp` | 131.4 |
| `z_spread_bp` | 133.5 |
| `recovery` | 0.4 |
| `credit_curve_tenors` | 2 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **CDS-bond basis (F3):** this META long-end bond is hedged by the #29 META CDS (`funding_basis_squeeze`).
- **Real rates (F6):** 2054 maturity.

## Limitations & assumptions

Config-fallback spread; flat spread/recovery; long-maturity sensitivity; core-book face.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Credit spread / curve | [`src/pricer/loader/bonds_adapter.py`](../../../src/pricer/loader/bonds_adapter.py), [`curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
