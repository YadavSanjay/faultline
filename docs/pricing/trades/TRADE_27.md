# TRADE_27 — 3M USD/JPY FX forward (carry overlay, buy USD)

**Asset class:** fx · **Trade type:** `fx_forward` · **Methodology:** [FX forwards & cross-currency swaps](../methodology/40-fx.md)

## In plain English

A 3-month agreement to **buy USD / sell JPY** at a locked rate of 156.94. It's a carry overlay —
holding USD (higher-yielding) against JPY (lower-yielding). Its value today is the difference between
the rate we locked and the current fair forward rate, discounted back. If the yen weakens past our
locked rate, the position gains.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| foreign ccy | JPY |
| usd notional | $113.22mm |
| strike | 156.9 |
| maturity date | 2027-01-08 |
| direction | long_usd |
| Currency | USD |

## Market data

**Conceptually required:** USD and JPY discount factors to maturity, plus the current USD/JPY spot.

**Actually used (free sources):** USD discounting on the SOFR curve (Pensford); JPY discounting on a
**flat curve at BOJ TONAR** ([`boj_tonar.csv`](../../../data/marketdata/fx/boj_tonar.csv)); spot
USD/JPY from [`ecb_fx_rates.csv`](../../../data/marketdata/fx/ecb_fx_rates.csv) (ECB cross-rate), Fed
H.10 as backup. See [FX methodology](../methodology/40-fx.md).

## How it's priced

Covered interest parity: `PV = N·DF_usd(T) − N·K·DF_jpy(T)/spot`, and the **fair forward** is
`F = spot·DF_usd/DF_jpy`. In-the-money when the strike beats the fair forward.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $943**.

| Measure | Value |
|---|---|
| `spot` | 158.1 |
| `strike` | 156.9 |
| `fair_forward` | 156.9 |
| `fx_delta_1pct` | 1,109,341.24 |
| `usd_notional` | $113.22mm |
| `foreign_rate` | 0.01227 |
| `xccy_basis_bp` | 0 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.

## Limitations & assumptions

XCCY basis via a config hook, default 0 (CIP assumed to hold exactly until a licensed basis is set); flat JPY curve at TONAR; representative terms (core-book size).

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/fx/fx_pricer.py`](../../../src/pricer/ql/fx/fx_pricer.py) (`FxForwardPricer`) |
| Market-data adapter | [`src/pricer/loader/fx_adapter.py`](../../../src/pricer/loader/fx_adapter.py) |
| Trade definition | [`src/pricer/input/fx/instruments.py`](../../../src/pricer/input/fx/instruments.py) |
