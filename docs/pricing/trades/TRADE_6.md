# TRADE_6 — SMH ETF, short (relative-value hedge)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

A **short** position in the VanEck Semiconductor ETF (SMH), held as a relative-value hedge against
the single-name semiconductor longs elsewhere in the book. Because the share count is negative, the
position has a **negative** present value — a short makes money when SMH falls.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| ticker | SMH |
| quantity | -192,475 |
| Currency | USD |

## Market data

**Conceptually required:** current SMH price.

**Actually used (free sources):** latest daily close from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Stooq); trailing dividend
yield from [`dividends.csv`](../../../data/marketdata/equity/dividends.csv) (diagnostic).

## How it's priced

`PV = quantity × close` with `quantity < 0` ⇒ negative PV — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = -$121.74mm**.

| Measure | Value |
|---|---|
| `spot` | 632.5 |
| `quantity` | -192,475 |
| `delta_shares` | -192,475 |
| `cash_delta` | -$121.74mm |
| `gross_notional` | $121.74mm |
| `dividend_yield` | 0.175% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.
- **Dispersion basis DRIVER (F3):** this SMH short is the ETF leg of `basis:SMH_SEMI` (SMH − single-name semis) — the `semis_dispersion` scenario is the hedge *failing* (SMH holds while the single names fall), a risk a name-level Σ nets to ~flat.
- **Crowding + squeeze (F5):** a *short*, so a crowded SMH short is **squeeze / upside-tail** risk a long-book stress framework never flags — surfaced via the short-squeeze flag when days-to-cover is at its historical tail. (It is the one cash-equity leg tagged margined, F2, because it is short.)

## Limitations & assumptions

No borrow cost modelled on the short; raw close; core-book quantity.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
