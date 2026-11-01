# TRADE_12 — SPX 3M ATM put (European, portfolio hedge)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A long put on the S&P 500 index — broad portfolio insurance. If the market sells off, this put gains
and offsets losses across the whole book. SPX options are **European** (exercisable only at expiry)
and cash-settled, so they price with the closed-form Black-Scholes formula.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $30.07mm (core book) |
| underlier | SPX |
| structure | vanilla |
| contract multiplier | 100 |
| Leg 1 | right=put strike=7850 expiry=2027-01-15 exercise=european quantity=40 |
| Currency | USD |

## Market data

**Conceptually required:** index level, strike/expiry, dividend yield, risk-free curve, volatility.

**Actually used (free sources):** SPX option chain
([`SPX_chains.csv`](../../../data/marketdata/options/chains/SPX_chains.csv), CBOE delayed) for the IV;
index level and dividend yield recovered via put-call parity; SOFR discounting.

## How it's priced

European exercise ⇒ closed-form `ql.AnalyticEuropeanEngine` (Black-Scholes-Merton with dividend
yield). No early-exercise solver needed. See
[Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $795,309**.

| Measure | Value |
|---|---|
| `delta_shares` | -1,781.98 |
| `delta_cash` | -$13.93mm |
| `gamma` | 2.777 |
| `vega_1volpt` | $65,042 |
| `spot` | 7,817.39 |
| `dividend_yield` | -0.095% |
| `discrete_dividend_legs` | 0 |
| `vol_leg0_put_7850` | 0.1385 |

Leg PVs: `leg0_put_7850` = $795,309.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Continuous dividend yield; IV from a delayed chain; mid quotes; representative strike/expiry (contract count is the core-book size).

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
