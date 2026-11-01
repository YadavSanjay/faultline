# TRADE_32 — Vistra (VST) common, long (AI-power / IPP diversifier)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A long position in Vistra, a large independent power producer (IPP) with a nuclear + gas fleet. The
second leg of the Part-2 AI-power sleeve alongside Constellation (#31): datacenter electricity demand
is the growth story, and IPPs sell into it. Worth shares × price; moves with VST.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $54.68mm (core book) |
| ticker | VST |
| quantity | 344,234 |
| Currency | USD |

## Market data

**Conceptually required:** the current VST share price.

**Actually used (free sources):** the latest daily close on/before the valuation date from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Yahoo; full 2018+ history).

## How it's priced

`PV = quantity × close`. No discounting, no QuantLib engine — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $55.25mm**.

| Measure | Value |
|---|---|
| `spot` | 160.5 |
| `quantity` | 344,234 |
| `delta_shares` | 344,234 |
| `cash_delta` | $55.25mm |
| `gross_notional` | $55.25mm |
| `dividend_yield` | 0.569% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **AI-power diversifier (Part 2):** Vistra (IPP) long, own factor (`EQ_VST`) — same as #31.

## Limitations & assumptions

Raw close; quantity is the core-book size. **Risk factor:** VST has its own VaR/Σ factor (`EQ_VST`) —
sized on its own single-name risk, not as XLK beta.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
