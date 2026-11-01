# TRADE_3 — AVGO common stock, long (networking / custom silicon)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A long position in Broadcom, the book's networking and custom-silicon exposure. Worth shares ×
current price.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $68.66mm (core book) |
| ticker | AVGO |
| quantity | 151,046 |
| Currency | USD |

## Market data

**Conceptually required:** current AVGO share price.

**Actually used (free sources):** latest daily close from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Stooq); trailing dividend
yield from [`dividends.csv`](../../../data/marketdata/equity/dividends.csv) (diagnostic).

## How it's priced

`PV = quantity × close` — see [Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $56.76mm**.

| Measure | Value |
|---|---|
| `spot` | 375.8 |
| `quantity` | 151,046 |
| `delta_shares` | 151,046 |
| `cash_delta` | $56.76mm |
| `gross_notional` | $56.76mm |
| `dividend_yield` | 0.692% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Crowding (F5)**, `style:MOM` (F4), a single-name leg of `basis:SMH_SEMI` (F3), and the F9 `ai_bust` throughput multiplier.

## Limitations & assumptions

Raw close; trailing dividend diagnostic; core-book quantity.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
