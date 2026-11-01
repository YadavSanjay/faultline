# TRADE_17 — Oracle 5.55% 2053 (long-end credit)

**Asset class:** bonds · **Trade type:** `corp_bond` · **Methodology:** [Fixed-rate bonds](../methodology/20-bonds.md)

## In plain English

A holding of Oracle's 5.55% bond maturing 2053 — a very long-dated, somewhat wider-spread credit.
Long maturity means it is highly sensitive to interest rates (big duration), and Oracle's wider
spread means more sensitivity to credit. Its issuer credit risk is separately hedged by the ORCL CDS
([TRADE_28](TRADE_28.md)).

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| issuer | ORCL |
| cusip | 68389XCQ6 |
| coupon | 0.0555 |
| issue date | 2023-02-06 |
| maturity date | 2053-02-06 |
| coupon frequency | Semiannual |
| day count | 30/360 |
| Currency | USD |

## Market data

**Conceptually required:** a discount curve reflecting Oracle's credit, plus the schedule.

**Actually used (free sources):** the SOFR curve — now extended to real **12Y/15Y/20Y nodes plus a
30Y** (A1), so Oracle's 2053 cashflows no longer discount on a flat 10Y extrapolation — plus an
**issuer Z-spread curve** derived from Oracle's DTCC single-name CDS term structure (per-tenor CDS
spread + a maturity-interpolated SOFR-Treasury basis). The config fallback of 130 bp
(`issuer_fallback_bp.ORCL`) applies only if the CDS feed is too thin. See
[Fixed-rate bonds methodology](../methodology/20-bonds.md).

## How it's priced

`ql.FixedRateBond` + `ql.DiscountingBondEngine` on a **Z-spread term structure** over SOFR
(`SpreadedLinearZeroInterpolatedTermStructure`, A3): near-term coupons discount at Oracle's lower
front spread (~44 bp at 1Y) rising to the 5Y level (~219 bp), flat beyond the last CDS tenor —
instead of one flat spread applied at every maturity.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $84.31mm**.

| Measure | Value |
|---|---|
| `clean_price` | 73.53 |
| `dirty_price` | 74.49 |
| `accrued` | 0.9558 |
| `ytm` | 7.967% |
| `mod_duration` | 11.56 |
| `convexity` | 215 |
| `dv01` | $97,485 |
| `notional` | $113.22mm |
| `credit_spread_bp` | 242.2 |
| `z_spread_bp` | 290.2 |
| `recovery` | 0.4 |
| `credit_curve_tenors` | 5 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **CDS-bond basis (F3):** this ORCL long-end bond is hedged by the #28 ORCL CDS; the `funding_basis_squeeze` scenario dislocates the CDS-bond basis (a one-sided bump that moves the CDS, not the bond).
- **Real rates (F6):** 2053 maturity — the sharpest real-rate duration in the book.

## Limitations & assumptions

CDS-derived Z-spread term structure (front-loaded), flat-extrapolated past ~5Y since CDS quotes
don't reach 2053; flat 40% recovery; the long maturity makes the mark especially sensitive to both
the SOFR curve and the assumed spread; core-book face.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/bonds/bond_pricer.py`](../../../src/pricer/ql/bonds/bond_pricer.py) |
| Credit spread / curve | [`src/pricer/loader/bonds_adapter.py`](../../../src/pricer/loader/bonds_adapter.py), [`curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Trade definition | [`src/pricer/input/bonds/instruments.py`](../../../src/pricer/input/bonds/instruments.py) |
