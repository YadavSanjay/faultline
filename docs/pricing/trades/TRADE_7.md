# TRADE_7 — AAPL Jan-27 LEAPS call (American)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A long, long-dated call option on Apple — the right to buy AAPL at $320 any time before January
2027. It is a leveraged bullish bet: it gains if Apple rises well above $320, and the most it can
lose is the premium paid. "LEAPS" just means a long-dated listed option.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $60.91mm (core book) |
| underlier | AAPL |
| structure | vanilla |
| contract multiplier | 100 |
| Leg 1 | right=call strike=320 expiry=2027-01-15 exercise=american quantity=1903 |
| Currency | USD |

## Market data

**Conceptually required:** spot, strike/expiry (contract), dividend yield, risk-free curve,
volatility.

**Actually used (free sources):** the AAPL option chain
([`AAPL_chains.csv`](../../../data/marketdata/options/chains/AAPL_chains.csv), CBOE delayed) supplies
the per-strike implied vol and, via put-call parity, the **recovered spot and dividend yield**.
Discounting on the SOFR curve.

## How it's priced

American exercise ⇒ `ql.FdBlackScholesVanillaEngine` (100×100 finite-difference grid) on a
Black-Scholes-Merton process with the chain's implied vol at K=320. Because AAPL pays dividends and
this call is American, it prices with a **projected discrete ex-dividend schedule** (B1) rather than
a continuous yield, so the grid can time early exercise to each ex-date; the dividend amounts are
scaled to preserve the chain-implied forward, so only the early-exercise premium (which an ATM call
like this one genuinely carries) is added. See
[Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $5.20mm**.

| Measure | Value |
|---|---|
| `delta_shares` | 129,176.32 |
| `delta_cash` | $43.08mm |
| `gamma` | 1,529.02 |
| `vega_1volpt` | $120,159 |
| `spot` | 333.5 |
| `dividend_yield` | -0.140% |
| `discrete_dividend_legs` | 0 |
| `vol_leg0_call_320` | 0.2547 |

Leg PVs: `leg0_call_320` = $5.20mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

American call on a payer now uses a forward-preserving discrete ex-dividend schedule (B1), so the
early-exercise premium is captured; delayed/weekly chain; mid IVs; strike/expiry representative
(contract count is the core-book size).

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
