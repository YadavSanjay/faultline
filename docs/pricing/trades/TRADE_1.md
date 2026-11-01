# TRADE_1 — NVDA common stock, long (core AI beta)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A straightforward long position in NVIDIA shares — the book's core "AI beta." It is worth the
number of shares times the current share price, full stop. It gains and loses dollar-for-dollar
with NVDA's stock price.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $91.77mm (core book) |
| ticker | NVDA |
| quantity | 440,486 |
| Currency | USD |

## Market data

**Conceptually required:** the current NVDA share price.

**Actually used (free sources):** the latest daily close on/before the valuation date from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Stooq). A trailing
dividend yield is also computed from [`dividends.csv`](../../../data/marketdata/equity/dividends.csv)
as a diagnostic only.

## How it's priced

`PV = quantity × close`. No discounting, no QuantLib engine — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $105.38mm**.

| Measure | Value |
|---|---|
| `spot` | 239.2 |
| `quantity` | 440,486 |
| `delta_shares` | 440,486 |
| `cash_delta` | $105.38mm |
| `gross_notional` | $105.38mm |
| `dividend_yield` | 0.217% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Crowding (F5):** scaled by NVDA days-to-cover percentile (~×1.30 recently — short interest at its own highs) on the dual-RAG idiosyncratic leg, composed with the news multiplier.
- **Drivers:** a single-name semis leg of the `basis:SMH_SEMI` dispersion (F3, hedged by #6) and the `style:MOM` momentum crash (F4); amplified in `ai_bust` by the F9 throughput multiplier and re-priced against real rates (F6).
- **News (P3):** 8-K item codes + GDELT tone clamp-scale the idiosyncratic shock; a 5.02 filing fires `keyman_nvda` live.

## Limitations & assumptions

Raw close (not split-adjusted by the pricer); dividend yield is a trailing diagnostic; share
quantity is the core-book size.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
