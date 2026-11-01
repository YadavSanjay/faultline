# TRADE_23 — 5Y×10Y European payer swaption (rate-rise hedge)

**Asset class:** rates · **Trade type:** `european_swaption` · **Methodology:** [European swaptions](../methodology/31-swaptions.md)

## In plain English

The right — exercisable once, in 5 years — to enter a 10-year swap in which we **pay fixed and
receive floating**. We exercise only if rates have risen by then, so it's a **rate-rise hedge**:
cheap insurance that pays off if long rates spike. The PV is the option premium today.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.10mm (core book) |
| direction | payer |
| option tenor | 5Y |
| swap tenor | 10Y |
| settlement | physical |
| Currency | USD |

## Market data

**Conceptually required:** the SOFR curve (to build the forward swap and discount) **and** a
volatility for the forward swap rate.

**Actually used (free sources):** SOFR curve from Pensford forwards; a **MOVE-anchored normal
(Bachelier) vol** — the level is read live from the **MOVE index**
([`move_index.csv`](../../../data/marketdata/stress/move_index.csv)) and shaped into an
expiry-dependent term structure (C1), so this 5Y-expiry swaption uses the vol at its own expiry
(~97% of the MOVE level). The shape is a stylized MOVE-anchored assumption, not a market surface;
still flat across strike (no free swaption vol cube). See
[European swaptions methodology](../methodology/31-swaptions.md).

## How it's priced

`ql.BachelierSwaptionEngine` on a normal-vol model: the premium equals the swap annuity times the
expected in-the-moneyness of the forward swap rate at expiry. The ATM strike is the underlying's
`fairRate()`.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $2.24mm**.

| Measure | Value |
|---|---|
| `atm_or_strike` | 0.05191 |
| `normal_vol_bp` | 102 |
| `vega_1bp` | 21,956.78 |
| `notional` | $40.10mm |
| `underlying_fair_rate` | 0.05191 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.

## Limitations & assumptions

Vol is MOVE-anchored with a stylized (assumed) expiry term structure (C1) and is flat across strike
(no smile) — the dominant simplification, pending a licensed swaption cube; ATM strike;
core-book notional.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/rates/swaption_pricer.py`](../../../src/pricer/ql/rates/swaption_pricer.py) |
| Vol sourcing | [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py) (`normal_vol`) |
| Trade definition | [`src/pricer/input/rates/instruments.py`](../../../src/pricer/input/rates/instruments.py) |
