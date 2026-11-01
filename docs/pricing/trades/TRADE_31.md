# TRADE_31 — Constellation Energy (CEG) common, long (AI-power / nuclear diversifier)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A long position in Constellation Energy — the largest US nuclear-power operator. Added in the
Part-2 rebalance ([portfolio_rebalance_review.md](../../design/portfolio_rebalance_review.md)) to
express the "AI needs electricity" leg of the theme: hyperscaler datacenters are power-constrained,
and nuclear/IPP names are the supply. It is worth shares × price and moves dollar-for-dollar with CEG.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.70mm (core book) |
| ticker | CEG |
| quantity | 161,891 |
| Currency | USD |

## Market data

**Conceptually required:** the current CEG share price.

**Actually used (free sources):** the latest daily close on/before the valuation date from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Yahoo). History starts at the
Feb-2022 Exelon spin-off (~4.5y), which comfortably covers the risk lookback windows.

## How it's priced

`PV = quantity × close`. No discounting, no QuantLib engine — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $48.63mm**.

| Measure | Value |
|---|---|
| `spot` | 300.4 |
| `quantity` | 161,891 |
| `delta_shares` | 161,891 |
| `cash_delta` | $48.63mm |
| `gross_notional` | $48.63mm |
| `dividend_yield` | 0.556% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **AI-power diversifier (Part 2):** Constellation (nuclear) long, with its OWN VaR/Σ factor (`EQ_CEG`) — the model credits its real (lower) correlation to semis *and* its higher single-name vol, so it is sized on its own risk rather than as XLK beta (which had over-sized it).

## Limitations & assumptions

Raw close (not split-adjusted by the pricer); quantity is the core-book size. **Risk factor:** CEG has
its OWN VaR/Σ factor (`EQ_CEG`, from its own price series) — the model credits its real (lower)
correlation to semis and its higher single-name vol, so it is sized on its own risk (the earlier
shared XLK proxy had over-sized it). See the rebalance review.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
