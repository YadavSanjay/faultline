# TRADE_29 — 5Y META CDS, buy protection (issuer hedge for bond #18)

**Asset class:** credit · **Trade type:** `cds` · **Methodology:** [Credit default swaps](../methodology/50-credit-cds.md)

## In plain English

A 5-year credit default swap on Meta where we **buy protection** — insurance against a Meta default.
We pay a running premium and get paid if Meta defaults. It hedges the issuer credit risk of the long
Meta bond ([TRADE_18](TRADE_18.md)).

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.10mm (core book) |
| reference | META |
| kind | single_name |
| protection | buy |
| tenor | 5Y |
| running coupon | 0.01 |
| recovery | 0.4 |
| Currency | USD |

## Market data

**Conceptually required:** Meta's CDS par spread and a discount curve.

**Actually used (free sources):** 5Y par spread from DTCC single-name CDS
([`dtcc_single_name_cds.csv`](../../../data/marketdata/credit/dtcc_single_name_cds.csv)); if too thin,
the **config fallback of 74 bp** (`credit.references.META.fallback_bp`, tighter than Oracle, reflecting
a stronger credit). SOFR discount curve (flat-forward). See
[Credit CDS methodology](../methodology/50-credit-cds.md).

## How it's priced

Calibrate a **flat hazard rate** to the par spread, then value the standardized 100 bp CDS with
`ql.IsdaCdsEngine`: `PV = protection leg − premium leg` (buyer).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = -$220,165**.

| Measure | Value |
|---|---|
| `par_spread_bp` | 88 |
| `fair_spread_bp` | 88 |
| `running_coupon_bp` | 100 |
| `upfront_pts` | -0.5491 |
| `cs01` | 18,431.11 |
| `rec01` | 103.4 |
| `hazard_bp` | 155.8 |
| `survival_to_maturity` | 0.9221 |
| `recovery` | 0.4 |
| `notional` | $40.10mm |
| `curve_tenors` | 2 |

Leg PVs: `coupon` = -$1.83mm, `default` = $1.61mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.
- **CDS-bond basis (F3):** single-name META CDS hedging bond #18 (`funding_basis_squeeze`); JTD/IDR in the risk engine.

## Limitations & assumptions

Flat hazard from a single 5Y point; fixed 40% recovery; sparse DTCC feed with config fallback; no
CDS-bond basis adjustment.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer + hazard calibration | [`src/pricer/ql/credit/cds_pricer.py`](../../../src/pricer/ql/credit/cds_pricer.py) |
| Market-data adapter | [`src/pricer/loader/credit_adapter.py`](../../../src/pricer/loader/credit_adapter.py) |
| Trade definition | [`src/pricer/input/credit/instruments.py`](../../../src/pricer/input/credit/instruments.py) |
