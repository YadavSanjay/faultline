# TRADE_2 — TSM ADR, long (foundry exposure)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A long position in Taiwan Semiconductor's US-listed ADR, giving the book exposure to the world's
leading chip foundry. Worth shares × price. Its TWD/USD currency risk is separately hedged by the
USD/TWD cross-currency swap ([TRADE_26](TRADE_26.md)).

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $75.87mm (core book) |
| ticker | TSM |
| quantity | 182,099 |
| Currency | USD |

## Market data

**Conceptually required:** the current TSM ADR price.

**Actually used (free sources):** latest daily close from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Stooq); trailing dividend
yield from [`dividends.csv`](../../../data/marketdata/equity/dividends.csv) (diagnostic).

## How it's priced

`PV = quantity × close` — see [Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $87.83mm**.

| Measure | Value |
|---|---|
| `spot` | 482.3 |
| `quantity` | 182,099 |
| `delta_shares` | 182,099 |
| `cash_delta` | $87.83mm |
| `gross_notional` | $87.83mm |
| `dividend_yield` | 0.799% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Taiwan (F8):** this TSM ADR is the exposure the #26 TWD swap hedges; `taiwan_geopolitical` is amplified by the GPR-TWN severity multiplier (×1.32, trigger live) — both legs move together.
- **Crowding (F5)** + single-name semis leg of the `basis:SMH_SEMI` dispersion (F3).

## Limitations & assumptions

ADR is priced in USD (no FX conversion in the equity pricer — the FX risk is carried as a separate
hedge trade). Raw close; core-book quantity.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
