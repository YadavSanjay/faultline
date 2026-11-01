# TRADE_15 — Apple 4.75% 2035 senior unsecured

**Asset class:** bonds · **Trade type:** `corp_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of Apple's 4.75% senior bond maturing 2035. It pays a 4.75% coupon twice a year and
returns principal in 2035. Its value today is all those future payments discounted back — using the
SOFR curve plus a small Apple credit spread, since Apple (unlike the US Treasury) can in principle
default.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| issuer | AAPL |
| cusip | 037833FB1 |
| coupon | 0.0475 |
| issue date | 2025-05-12 |
| maturity date | 2035-05-12 |
| coupon frequency | Semiannual |
| day count | 30/360 |
| Currency | USD |

## Market data

**Conceptually required:** a discount curve reflecting Apple's credit, plus the bond's schedule.

**Actually used (free sources):** SOFR curve (Pensford forwards) overlaid with an issuer credit
spread. The free DTCC single-name CDS feed is too sparse for AAPL, so we use the **config fallback
spread of 60 bp** (`credit_spread.issuer_fallback_bp.AAPL`). See
[Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine`, discounting on
`ZeroSpreadedTermStructure(SOFR, 60bp)`. PV is the dirty value; clean price, accrued, YTM, duration
and DV01 fall out.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $109.90mm**.

| Measure | Value |
|---|---|
| `clean_price` | 95.17 |
| `dirty_price` | 97.1 |
| `accrued` | 1.926 |
| `ytm` | 5.461% |
| `mod_duration` | 6.787 |
| `convexity` | 56.18 |
| `dv01` | $74,609 |
| `notional` | $113.22mm |
| `credit_spread_bp` | 60 |
| `z_spread_bp` | 61.55 |
| `recovery` | 0.4 |
| `credit_curve_tenors` | 1 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Real rates (F6):** an IG corporate whose long-duration discounting is decomposed against real vs breakeven rates in `real_rate_repricing`.

## Limitations & assumptions

Credit spread is a config fallback (free CDS unusable); flat spread (not a credit curve); flat 40%
recovery; face is the core-book size.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Credit spread / curve | [`src/pricer/loader/bonds_adapter.py`](../../../src/pricer/loader/bonds_adapter.py), [`curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
