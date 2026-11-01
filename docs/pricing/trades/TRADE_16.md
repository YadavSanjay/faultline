# TRADE_16 — Microsoft 4.10% 2037

**Asset class:** bonds · **Trade type:** `corp_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of Microsoft's 4.10% bond maturing 2037 — a long-dated, high-grade corporate. It pays a
4.10% coupon semiannually and returns principal in 2037; its value is those cash flows discounted on
SOFR plus a small Microsoft credit spread.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $59.17mm (core book) |
| issuer | MSFT |
| cusip | 594918BZ6 |
| coupon | 0.041 |
| issue date | 2017-02-06 |
| maturity date | 2037-02-06 |
| coupon frequency | Semiannual |
| day count | 30/360 |
| Currency | USD |

## Market data

**Conceptually required:** a discount curve reflecting Microsoft's credit, plus the schedule.

**Actually used (free sources):** SOFR curve + issuer spread. DTCC single-name CDS is too sparse, so
the **config fallback of 60 bp** (`issuer_fallback_bp.MSFT`) is used. See
[Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine` on `ZeroSpreadedTermStructure(SOFR, 60bp)`.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $53.04mm**.

| Measure | Value |
|---|---|
| `clean_price` | 88.95 |
| `dirty_price` | 89.65 |
| `accrued` | 0.7061 |
| `ytm` | 5.517% |
| `mod_duration` | 8.103 |
| `convexity` | 79.33 |
| `dv01` | $42,986 |
| `notional` | $59.17mm |
| `credit_spread_bp` | 60 |
| `z_spread_bp` | 61.56 |
| `recovery` | 0.4 |
| `credit_curve_tenors` | 1 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Real rates (F6):** long-duration IG credit, real/breakeven decomposition in `real_rate_repricing`.

## Limitations & assumptions

Config-fallback spread; flat spread and 40% recovery; core-book face.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Credit spread / curve | [`src/pricer/loader/bonds_adapter.py`](../../../src/pricer/loader/bonds_adapter.py), [`curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
