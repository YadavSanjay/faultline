# TRADE_25 — 1Y SOFR cap, ATM (tail hedge on funding)

**Asset class:** rates · **Trade type:** `cap` · **Methodology:** [Caps](../methodology/33-caps.md)

## In plain English

A 1-year interest-rate **cap** — insurance against short-term rates rising. It pays out each quarter
that SOFR exceeds the strike, so it caps the book's funding cost. A 1-year quarterly cap is four
small options ("caplets"), one per quarter; the PV is their combined premium.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.10mm (core book) |
| cap tenor | 1Y |
| caplet frequency | Quarterly |
| Currency | USD |

## Market data

**Conceptually required:** the SOFR curve (forward rates + discounting) and a volatility for each
caplet.

**Actually used (free sources):** SOFR curve from Pensford forwards; the same **MOVE-anchored normal
vol**, taken at the cap's 1Y expiry from the term structure (C1) — no free cap vol surface. See
[Caps methodology](../methodology/33-caps.md).

## How it's priced

`ql.BachelierCapFloorEngine` values each caplet as a normal-vol call on its period's forward SOFR;
the cap PV is the sum of discounted caplet values. The ATM strike is `cap.atmRate(discount_curve)`.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $106,451**.

| Measure | Value |
|---|---|
| `atm_or_strike` | 0.04384 |
| `normal_vol_bp` | 104.1 |
| `vega_1bp` | 786.8 |
| `notional` | $40.10mm |
| `n_caplets` | 4 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.

## Limitations & assumptions

MOVE-anchored vol at the cap expiry (stylized term-structure shape, no caplet-by-caplet stripping, flat across strike); forward-caplet approximation of compounded SOFR;
ATM strike; core-book notional.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/rates/cap_pricer.py`](../../../src/pricer/ql/rates/cap_pricer.py) |
| Vol sourcing | [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py) |
| Trade definition | [`src/pricer/input/rates/instruments.py`](../../../src/pricer/input/rates/instruments.py) |
