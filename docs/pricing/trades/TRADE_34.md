# TRADE_34 — SPDR Gold Shares (GLD) ETF, long (gold ballast / crisis diversifier)

**Asset class:** equity · **Trade type:** `stock` (ETF) · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A long position in the SPDR Gold ETF — the book's genuine **ballast**. Gold is low-to-negatively
correlated with tech equity, so it tends to hold or rally when the AI book sells off. Of the Part-2
sleeves it is the one that most *reduces* the book's concentration in the risk model rather than
adding to it. Worth shares × price; moves with the gold price (via GLD).

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $62.08mm (core book) |
| ticker | GLD |
| quantity | 164,658 |
| Currency | USD |

## Market data

**Conceptually required:** the current GLD share price.

**Actually used (free sources):** the latest daily close on/before the valuation date from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Yahoo; full 2018+ history).

## How it's priced

`PV = quantity × close`. No discounting, no QuantLib engine — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $62.94mm**.

| Measure | Value |
|---|---|
| `spot` | 382.3 |
| `quantity` | 164,658 |
| `delta_shares` | 164,658 |
| `cash_delta` | $62.94mm |
| `gross_notional` | $62.94mm |
| `dividend_yield` | 0.000% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Gold ballast (Part 2):** GLD's own factor (`EQ_GLD`) is low/negatively correlated to tech, so it *reduces* book risk in an AI-equity drawdown (a negative risk contribution) — the strongest diversifier of the new sleeves.

## Limitations & assumptions

Raw close; quantity is the core-book size. **Risk factor:** GLD has its own VaR/Σ factor (`EQ_GLD`).
Its low/negative correlation to tech makes it the book's genuine ballast — a proxy to XLK would
wrongly make gold move *with* tech in a crisis; the own factor keeps the diversification.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
