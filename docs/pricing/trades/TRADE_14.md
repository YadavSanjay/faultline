# TRADE_14 — QQQ 3M collar overlay (American)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A **collar** on the Nasdaq-100 ETF (QQQ): buy a $730 protective put and sell a $810 call, same
January 2027 expiry. The short call's premium helps pay for the put, so it's cheap downside
protection — but you cap your upside above $810. It's an overlay on a long tech book: protected on
the downside, give up some upside.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $58.54mm (core book) |
| underlier | QQQ |
| structure | collar |
| contract multiplier | 100 |
| Leg 1 | right=put strike=730 expiry=2027-01-15 exercise=american quantity=802 |
| Leg 2 | right=call strike=810 expiry=2027-01-15 exercise=american quantity=-802 |
| Currency | USD |

## Market data

**Conceptually required:** spot, both strikes/expiry, dividend yield, risk-free curve, vol at each
strike.

**Actually used (free sources):** QQQ option chain
([`QQQ_chains.csv`](../../../data/marketdata/options/chains/QQQ_chains.csv), CBOE delayed) — put and
call legs each read their own IV; spot/dividend recovered via put-call parity; SOFR discounting.

## How it's priced

Both legs priced with `ql.FdBlackScholesVanillaEngine` (American), summed by signed quantity:
`PV = PV(long 700 put) − PV(short 780 call)`. As American legs on a dividend payer (QQQ), both use a
**forward-preserving discrete ex-dividend schedule** (B1) for correct early-exercise timing. See
[Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $360,335**.

| Measure | Value |
|---|---|
| `delta_shares` | -50,534.88 |
| `delta_cash` | -$38.40mm |
| `gamma` | -47.07 |
| `vega_1volpt` | -$509 |
| `spot` | 759.9 |
| `dividend_yield` | 0.085% |
| `discrete_dividend_legs` | 2 |
| `vol_leg0_put_730` | 0.2204 |
| `vol_leg1_call_810` | 0.1876 |

Leg PVs: `leg0_put_730` = $1.52mm, `leg1_call_810` = -$1.16mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Discrete ex-dividend schedule on the American legs (B1, forward-preserving); per-leg IV from a delayed chain; mid quotes; representative strikes/expiry (contract count is the core-book size).

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
