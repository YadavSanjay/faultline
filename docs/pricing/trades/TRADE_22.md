# TRADE_22 — 10Y SOFR payer IRS (rates hedge for the bond book)

**Asset class:** rates · **Trade type:** `ois_swap` · **Methodology:** [Interest-rate swaps](../methodology/30-rates-swaps.md)

## In plain English

A 10-year interest-rate swap where we **pay a fixed 3.8%** and **receive floating SOFR** on a $25mm
notional. It is the book's rates hedge: if interest rates rise, the long bonds lose value but this
payer swap gains, offsetting. No principal changes hands — only the net interest.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.10mm (core book) |
| direction | payer |
| swap tenor | 10Y |
| fixed rate | 3.800% |
| forward start | 0D |
| Currency | USD |

## Market data

**Conceptually required:** a curve that both projects future SOFR and discounts — the SOFR OIS curve.

**Actually used (free sources):** the SOFR curve bootstrapped from Pensford forwards — see
[SOFR multi-curve](../methodology/01-sofr-multicurve.md). It is the only market input.

## How it's priced

`ql.MakeOIS` + `DiscountingSwapEngine`: project the floating leg and discount both legs on the SOFR
curve; `PV = PV_float − PV_fixed`. See [Interest-rate swaps methodology](../methodology/30-rates-swaps.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $3.40mm**.

| Measure | Value |
|---|---|
| `fair_rate` | 4.876% |
| `fixed_rate` | 3.800% |
| `dv01` | $31,606 |
| `fixed_leg_bps` | -31,605.66 |
| `notional` | $40.10mm |

Leg PVs: `fixed` = -$12.01mm, `float` = $15.41mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.
- **Swap-spread basis (F3):** the `basis:SWAP_SPREAD` overlay moves only the Treasury/OIS curve, repricing the USTs it hedges (#20/#21), not this swap — the hedge basis widening in `funding_basis_squeeze`.
- **Collateral:** cleared, so a rate shock drives a large variation-margin line (F2).

## Limitations & assumptions

Single-curve OIS (same curve discounts and forecasts; no basis); curve-staleness warning propagates;
fixed rate is representative; the notional is the core-book size.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/rates/swap_pricer.py`](../../../src/pricer/ql/rates/swap_pricer.py) |
| Curve | [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Trade definition | [`src/pricer/input/rates/instruments.py`](../../../src/pricer/input/rates/instruments.py) |
