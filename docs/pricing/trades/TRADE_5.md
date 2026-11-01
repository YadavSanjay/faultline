# TRADE_5 — XLK ETF, long (sector hedge basket)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A long position in the Technology Select Sector SPDR (XLK), a diversified tech-sector basket held
as a broad sector exposure / hedge basket. Priced exactly like a single stock: shares × price.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| ticker | XLK |
| quantity | 588,747 |
| Currency | USD |

## Market data

**Conceptually required:** current XLK price.

**Actually used (free sources):** latest daily close from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Stooq); trailing dividend
yield from [`dividends.csv`](../../../data/marketdata/equity/dividends.csv) (diagnostic).

## How it's priced

`PV = quantity × close` — see [Cash equity methodology](../methodology/10-cash-equity.md). An ETF is
priced on its own traded price, not by looking through to its holdings.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $118.93mm**.

| Measure | Value |
|---|---|
| `spot` | 202 |
| `quantity` | 588,747 |
| `delta_shares` | 588,747 |
| `cash_delta` | $118.93mm |
| `gross_notional` | $118.93mm |
| `dividend_yield` | 0.414% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Systematic proxy:** `eq:XLK` is the `ai_bust` systematic driver leg and the default proxy for unmapped underliers; carries `style:MOM` (F4) and real-rate (F6) sensitivity.

## Limitations & assumptions

ETF marked on its own close (no look-through); raw close; dividend yield tracked at the ETF level,
not per holding.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
