# TRADE_37 — Intel (INTC) common, long (semis dispersion pair, laggard leg)

**Asset class:** equity · **Trade type:** `stock` · **Methodology:** [Cash equity & ETFs](../methodology/10-cash-equity.md)

## In plain English

The long leg of a **market-neutral semis dispersion pair** (N5,
[portfolio_rebalance_review.md](../../design/portfolio_rebalance_review.md)): long Intel — the AI-cycle
*laggard* — against a short in AMD, the *leader* (#38). The pair bets the valuation/performance gap
between them narrows (laggard catch-up / leader de-rating). It is **beta-neutral** (net-zero market
exposure), so it adds relative-value alpha without adding to the book's AI-beta. Worth shares × price.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $63.62mm (core book) |
| ticker | INTC |
| quantity | 579,164 |
| Currency | USD |

## Market data

**Conceptually required:** the current INTC share price.

**Actually used (free sources):** the latest daily close on/before the valuation date from
[`prices_daily.csv`](../../../data/marketdata/equity/prices_daily.csv) (Yahoo; full 2018+ history).

## How it's priced

`PV = quantity × close`. No discounting, no QuantLib engine — see
[Cash equity methodology](../methodology/10-cash-equity.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $65.16mm**.

| Measure | Value |
|---|---|
| `spot` | 112.5 |
| `quantity` | 579,164 |
| `delta_shares` | 579,164 |
| `cash_delta` | $65.16mm |
| `gross_notional` | $65.16mm |
| `dividend_yield` | 0.000% |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 10d — large-cap equity / major rates & FX; a `√(10/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Semis dispersion pair (N5):** INTC long, the laggard leg of the beta-neutral INTC/AMD pair (#38 short). Own factor (`EQ_INTC`); the pair expresses intra-semis relative value (laggard catch-up vs the crowded leader) with net-zero market beta — orthogonal alpha, not more AI-beta.

## Limitations & assumptions

Raw close; quantity is the core-book size. **Risk factor:** INTC has its own VaR/Σ factor (`EQ_INTC`)
— essential for a pair, since a shared proxy would net the correlated long/short to ~zero and erase the
dispersion risk. The pair's short leg (#38) is pinned beta-neutral to this long by the sizing
`market_neutral_pairs` feature.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/cash_equity/stock_pricer.py`](../../../src/pricer/ql/cash_equity/stock_pricer.py) |
| Market-data adapter | [`src/pricer/loader/cash_equity_adapter.py`](../../../src/pricer/loader/cash_equity_adapter.py) |
| Trade definition | [`src/pricer/input/cash_equity/instruments.py`](../../../src/pricer/input/cash_equity/instruments.py) |
