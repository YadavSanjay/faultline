# TRADE_35 — SPX 6M ~85% put (European, tail-convexity sleeve)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A deeper-out-of-the-money 6-month put on the S&P 500 — added in the Part-2 rebalance as a dedicated,
**un-thinned convexity sleeve**. Where the existing SPX hedges (#12/#13) sit near-the-money, this
lower-strike put is cheap tail insurance that pays off convexly in a sharp sell-off, pushing the
book's boom/bust profile back toward symmetric. European exercise ⇒ closed-form Black-Scholes.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $12.83mm (core book) |
| underlier | SPX |
| structure | vanilla |
| contract multiplier | 100 |
| Leg 1 | right=put strike=6400 expiry=2026-12-18 exercise=european quantity=20 |
| Currency | USD |

## Market data

**Conceptually required:** index level, strike/expiry, dividend yield, risk-free curve, volatility.

**Actually used (free sources):** the SPX option chain
([`SPX_chains.csv`](../../../data/marketdata/options/chains/SPX_chains.csv), CBOE delayed) for the
skew-aware IV at the ~85% strike; index level and dividend yield via put-call parity; SOFR discounting.

## How it's priced

European exercise ⇒ closed-form `ql.AnalyticEuropeanEngine` (Black-Scholes-Merton with dividend
yield). See [Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $31,871**.

| Measure | Value |
|---|---|
| `delta_shares` | -78.79 |
| `delta_cash` | -$615,951 |
| `gamma` | 0.1774 |
| `vega_1volpt` | $6,262 |
| `spot` | 7,817.39 |
| `dividend_yield` | -0.095% |
| `discrete_dividend_legs` | 0 |
| `vol_leg0_put_6400` | 0.2745 |

Leg PVs: `leg0_put_6400` = $31,871.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Convexity sleeve (Part 2):** a deeper-OTM SPX put carried as an un-thinned tail hedge — pays off convexly in a sharp sell-off, re-symmetrizing the book's boom/bust profile.
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Continuous dividend yield; IV from a delayed chain interpolated/extrapolated to the OTM strike; mid
quotes; representative strike/expiry and contract count (core-book size). Sized as a hedge
(`hedge_scale`) so it is carried, not thinned, across the risk profiles.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
