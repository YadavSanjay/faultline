# TRADE_38 — Advanced Micro Devices (AMD) common, short (semis dispersion pair, leader leg)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

The short leg of the **market-neutral semis dispersion pair** (N5): short AMD — an AI-GPU *leader*
priced for perfection — against the long in Intel, the laggard (#37). A negative share quantity, so its
value is negative and it *gains* when AMD falls. Sized to be **beta-neutral** against the INTC long, so
the pair is a pure relative-value (dispersion) bet with no net market exposure. Worth shares × price.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $40.46mm (core book) |
| ticker | AMD |
| quantity | -72,522 |
| Currency | USD |

## Market data

**Conceptually required:** the current AMD share price.

**Actually used (free sources):** the latest daily close on/before the valuation date from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Yahoo; full 2018+ history).

## How it's priced

`PV = quantity × close` (quantity is negative for a short). No discounting, no QuantLib engine — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = -$47.10mm**.

| Measure | Value |
|---|---|
| `spot` | 649.4 |
| `quantity` | -72,522 |
| `delta_shares` | -72,522 |
| `cash_delta` | -$47.10mm |
| `gross_notional` | $47.10mm |
| `dividend_yield` | 0.000% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Semis dispersion pair (N5):** AMD short, the leader leg. Own factor (`EQ_AMD`); the sizing `market_neutral_pairs` feature pins it to 0.636× the INTC long (= β_INTC/β_AMD) so the pair is beta-neutral.

## Limitations & assumptions

Raw close; the short quantity is set by the sizing `market_neutral_pairs` feature, which pins it to
0.636× the INTC long (#37) notional — the ratio `β_INTC / β_AMD` (1.05 / 1.65) that makes the pair
**beta-neutral**. **Risk factor:** AMD has its own VaR/Σ factor (`EQ_AMD`), so the pair's dispersion
risk registers rather than netting to zero.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
