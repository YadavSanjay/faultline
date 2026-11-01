# TRADE_28 — 5Y ORCL CDS, buy protection (issuer hedge for bond #17)

**Asset class:** credit · **Trade type:** `cds` · **Methodology:** [Credit default swaps](../methodology/50-credit-cds.md)

## In plain English

A 5-year credit default swap on Oracle where we **buy protection** — insurance against an Oracle
default. We pay a small running premium; if Oracle defaults we get paid for the loss. It hedges the
issuer credit risk of the long Oracle bond ([TRADE_17](TRADE_17.md)). The PV reflects how the cost of
that protection has moved since inception.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.10mm (core book) |
| reference | ORCL |
| kind | single_name |
| protection | buy |
| tenor | 5Y |
| running coupon | 0.01 |
| recovery | 0.4 |
| Currency | USD |

## Market data

**Conceptually required:** Oracle's CDS par spread (to imply default probability) and a discount
curve.

**Actually used (free sources):** a **term structure of par spreads** from DTCC single-name CDS
dissemination ([`dtcc_single_name_cds.csv`](../../../data/marketdata/credit/dtcc_single_name_cds.csv)) —
Oracle trades enough tenors (a 1Y and 5Y, sometimes a 3Y) to build a real credit curve; if the feed
is too thin, the **config fallback of 175 bp** (`credit.references.ORCL.fallback_bp`). SOFR discount
curve (flat-forward interpolation). See [Credit CDS methodology](../methodology/50-credit-cds.md).

## How it's priced

Because Oracle has ≥2 clean tenors, we **bootstrap a piecewise-flat hazard curve** (A2) — solving
each tenor's hazard so a par CDS to that tenor reprices to zero, walking from the short end out —
rather than assuming one flat hazard. This captures Oracle's steep IG credit curve (near-term
default risk much lower than at 5Y). We then value the standardized 100 bp CDS with
`ql.IsdaCdsEngine`: `PV = protection leg − premium leg` (buyer). (Thin names and the CDX index still
use a single flat hazard.)

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $1.75mm**.

| Measure | Value |
|---|---|
| `par_spread_bp` | 198.8 |
| `fair_spread_bp` | 198.7 |
| `running_coupon_bp` | 100 |
| `upfront_pts` | 4.369 |
| `cs01` | 17,009.72 |
| `rec01` | -1,809.26 |
| `hazard_bp` | 358.5 |
| `survival_to_maturity` | 0.8297 |
| `recovery` | 0.4 |
| `notional` | $40.10mm |
| `curve_tenors` | 5 |

Leg PVs: `coupon` = -$1.77mm, `default` = $3.53mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.
- **CDS-bond basis (F3):** the `basis:CDS_BOND` bump moves this single-name ORCL CDS (it hedges bond #17), not the bond — the hedge basis in `funding_basis_squeeze`.
- **Jump-to-default / IDR** is carried in the risk engine.

## Limitations & assumptions

Term structure only where the feed supports it — Oracle gets ~2–3 tenors and the hazard is
extrapolated flat past the last quoted tenor; fixed 40% recovery; DTCC feed sparse/noisy with
config fallback; no CDS-bond basis adjustment.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer + hazard calibration | [`src/pricer/ql/credit/cds_pricer.py`](../../../src/pricer/ql/credit/cds_pricer.py) |
| Market-data adapter | [`src/pricer/loader/credit_adapter.py`](../../../src/pricer/loader/credit_adapter.py) |
| Trade definition | [`src/pricer/input/credit/instruments.py`](../../../src/pricer/input/credit/instruments.py) |
