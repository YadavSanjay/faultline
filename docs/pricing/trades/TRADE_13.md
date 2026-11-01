# TRADE_13 — SPX 6M 90–80% put spread (European)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A **put spread** on the S&P 500: buy a higher-strike put ($6800) and sell a lower-strike put
($6050), same December 2026 expiry. This is cheaper tail protection than an outright put — it
protects against a moderate sell-off but caps the payout if the market crashes below the lower
strike. European, cash-settled.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $30.07mm (core book) |
| underlier | SPX |
| structure | put_spread |
| contract multiplier | 100 |
| Leg 1 | right=put strike=6800 expiry=2026-12-18 exercise=european quantity=40 |
| Leg 2 | right=put strike=6050 expiry=2026-12-18 exercise=european quantity=-40 |
| Currency | USD |

## Market data

**Conceptually required:** index level, both strikes/expiry, dividend yield, risk-free curve, vol at
each strike.

**Actually used (free sources):** SPX option chain
([`SPX_chains.csv`](../../../data/marketdata/options/chains/SPX_chains.csv), CBOE delayed) — each leg
reads its own skew-aware IV; index level/dividend recovered via put-call parity; SOFR discounting.

## How it's priced

Both legs priced with the closed-form `ql.AnalyticEuropeanEngine`, then summed by signed quantity:
`PV = PV(long 6800 put) − PV(short 6050 put)`. See
[Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $60,658**.

| Measure | Value |
|---|---|
| `delta_shares` | -179.1 |
| `delta_cash` | -$1.40mm |
| `gamma` | 0.4479 |
| `vega_1volpt` | $10,889 |
| `spot` | 7,817.39 |
| `dividend_yield` | -0.095% |
| `discrete_dividend_legs` | 0 |
| `vol_leg0_put_6800` | 0.2317 |
| `vol_leg1_put_6050` | 0.3133 |

Leg PVs: `leg0_put_6800` = $104,314, `leg1_put_6050` = -$43,656.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Continuous dividend yield; per-leg IV from a delayed chain; mid quotes; representative strikes/expiry.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
