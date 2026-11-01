# TRADE_30 — 5Y CDX.NA.IG, sell protection (carry / diversifier)

**Asset class:** credit · **Trade type:** `cds` · **Methodology:** [Credit default swaps](../methodology/50-credit-cds.md)

## In plain English

A 5-year position on the CDX.NA.IG index (125 North American investment-grade names) where we **sell
protection** — we collect a running premium and pay out if index constituents default. It's a carry
trade and a diversifier: a bullish credit position that earns income as long as defaults stay low.
Because we're *short* credit risk here, it offsets the *long*-protection single-name hedges elsewhere.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| reference | CDX.NA.IG |
| kind | index |
| protection | sell |
| tenor | 5Y |
| running coupon | 0.01 |
| recovery | 0.4 |
| Currency | USD |

## Market data

**Conceptually required:** the index par spread and a discount curve.

**Actually used (free sources):** the 5Y index par spread from DTCC index CDS dissemination
([`dtcc_index_cds.csv`](../../../data/marketdata/credit/dtcc_index_cds.csv), CFTC path); if too thin,
the **config fallback of 55 bp** (`credit.references."CDX.NA.IG".fallback_bp` — tight, as befits a
diversified IG index). SOFR discount curve (flat-forward). See
[Credit CDS methodology](../methodology/50-credit-cds.md).

## How it's priced

Priced at the **index level** (B4): calibrate a flat hazard to the quoted index par spread, then value
the standardized 100 bp CDS with `ql.IsdaCdsEngine` on the **outstanding notional** = traded ×
`index_factor` (1.0 for the current on-the-run series — the free feed carries no defaulted-name data;
the factor drops below 1 as constituents default). As a **seller**, the sign flips:
`PV = premium leg − protection leg`. This is the market-standard *untranched* index model — no
constituent-curve aggregation or tranche granularity.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $2.53mm**.

| Measure | Value |
|---|---|
| `par_spread_bp` | 51.45 |
| `fair_spread_bp` | 51.45 |
| `running_coupon_bp` | 100 |
| `upfront_pts` | 2.236 |
| `cs01` | -53,162.79 |
| `rec01` | -916.8 |
| `hazard_bp` | 87.22 |
| `survival_to_maturity` | 0.9556 |
| `recovery` | 0.4 |
| `notional` | $113.22mm |
| `curve_tenors` | 1 |
| `index_factor` | 1 |
| `n_constituents` | 125 |

Leg PVs: `coupon` = $5.21mm, `default` = -$2.68mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.
- **Index credit:** CDX.NA.IG (sell protection) takes the 20d IG-credit horizon.

## Limitations & assumptions

Untranched **index-level** model (B4) — flat hazard from the quoted index spread on the outstanding
notional (index_factor 1.0 for the on-the-run; no free defaulted-name data), no tranche or
constituent-curve granularity; fixed 40% recovery; DTCC feed with config fallback.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer + hazard calibration | [`src/pricer/ql/credit/cds_pricer.py`](../../../src/pricer/ql/credit/cds_pricer.py) |
| Market-data adapter | [`src/pricer/loader/credit_adapter.py`](../../../src/pricer/loader/credit_adapter.py) |
| Trade definition | [`src/pricer/input/credit/instruments.py`](../../../src/pricer/input/credit/instruments.py) |
