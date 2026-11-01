# TRADE_33 — Alphabet (GOOGL) common, long (hyperscaler / AI monetization)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A long position in Alphabet — added in the Part-2 rebalance to diversify the book from AI *supply*
(semiconductors) toward AI *demand / monetization*: Gemini, Cloud, and custom TPU silicon. A clean
mega-cap tech long. Worth shares × price; moves with GOOGL.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $90.89mm (core book) |
| ticker | GOOGL |
| quantity | 254,474 |
| Currency | USD |

## Market data

**Conceptually required:** the current GOOGL share price.

**Actually used (free sources):** the latest daily close on/before the valuation date from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Yahoo; full 2018+ history).

## How it's priced

`PV = quantity × close`. No discounting, no QuantLib engine — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $88.48mm**.

| Measure | Value |
|---|---|
| `spot` | 347.7 |
| `quantity` | 254,474 |
| `delta_shares` | 254,474 |
| `cash_delta` | $88.48mm |
| `gross_notional` | $88.48mm |
| `dividend_yield` | 0.247% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Hyperscaler (Part 2):** Alphabet long — AI demand/monetization; own factor (`EQ_GOOGL`).

## Limitations & assumptions

Raw close; quantity is the core-book size. **Risk factor:** GOOGL has its own VaR/Σ factor (`EQ_GOOGL`).

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
