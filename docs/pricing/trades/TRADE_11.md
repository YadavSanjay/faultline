# TRADE_11 — TSLA 1M ATM straddle (vol/event, American)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A **straddle** on Tesla: buy both a $380 call and a $380 put at the same November 2026 expiry. You make
money if TSLA moves *a lot* in **either** direction — it's a bet on volatility / a big event, not on
direction. The cost is the combined premium of the two options, and you need a move bigger than that
to profit.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| underlier | TSLA |
| structure | straddle |
| contract multiplier | 100 |
| Leg 1 | right=call strike=380 expiry=2026-11-20 exercise=american quantity=1415 |
| Leg 2 | right=put strike=380 expiry=2026-11-20 exercise=american quantity=1415 |
| Currency | USD |

## Market data

**Conceptually required:** spot, strike/expiry, dividend yield, risk-free curve, volatility.

**Actually used (free sources):** TSLA option chain
([`TSLA_chains.csv`](../../../data/marketdata/options/chains/TSLA_chains.csv), CBOE delayed) for the
ATM IV; spot/dividend recovered via put-call parity; SOFR discounting.

## How it's priced

Both legs priced with `ql.FdBlackScholesVanillaEngine` (American) and summed: `PV = PV(call) +
PV(put)`. Near ATM the deltas roughly cancel (low net delta) while the **vega adds** — this is a
long-vol position. See [Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $6.21mm**.

| Measure | Value |
|---|---|
| `delta_shares` | 12,998.56 |
| `delta_cash` | $4.95mm |
| `gamma` | 2,056.06 |
| `vega_1volpt` | $149,830 |
| `spot` | 380.5 |
| `dividend_yield` | -0.538% |
| `discrete_dividend_legs` | 0 |
| `vol_leg0_call_380` | 0.4116 |
| `vol_leg1_put_380` | 0.4116 |

Leg PVs: `leg0_call_380` = $3.28mm, `leg1_put_380` = $2.93mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Continuous dividend yield; ATM IV from a delayed chain; mid quotes; representative strike/expiry; the
straddle is most sensitive to the single ATM vol number, which a flat/sparse free surface estimates
only roughly.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
