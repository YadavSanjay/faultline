# TRADE_9 — MSFT 3M 25-delta call spread (American)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A **call spread** on Microsoft: buy a $545 call and sell a $585 call, same January 2027 expiry.
This is a cheaper, capped bullish bet — you profit if MSFT rises toward $585, but give up the
upside beyond it (and recoup some premium by selling the higher call). The value is the long call
minus the short call.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $50.33mm (core book) |
| underlier | MSFT |
| structure | call_spread |
| contract multiplier | 100 |
| Leg 1 | right=call strike=545 expiry=2027-01-15 exercise=american quantity=1258 |
| Leg 2 | right=call strike=585 expiry=2027-01-15 exercise=american quantity=-1258 |
| Currency | USD |

## Market data

**Conceptually required:** spot, both strikes/expiry, dividend yield, risk-free curve, vol at each
strike.

**Actually used (free sources):** MSFT option chain
([`MSFT_chains.csv`](../../../data/marketdata/options/chains/MSFT_chains.csv), CBOE delayed) — each
leg reads its **own** skew-aware IV (the $545 and $585 strikes get different vols); spot/dividend
recovered via put-call parity; SOFR discounting.

## How it's priced

Each leg priced independently with `ql.FdBlackScholesVanillaEngine` (American), then
**summed by signed quantity**: `PV = PV(long 545) − PV(short 585)`. Greeks net across legs. As
American legs on a dividend payer (MSFT), both use a **forward-preserving discrete ex-dividend
schedule** (B1) so early exercise is timed to each ex-date. See
[Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $1.69mm**.

| Measure | Value |
|---|---|
| `delta_shares` | 23,617.40 |
| `delta_cash` | $12.51mm |
| `gamma` | 81.8 |
| `vega_1volpt` | $18,424 |
| `spot` | 529.7 |
| `dividend_yield` | 0.206% |
| `discrete_dividend_legs` | 2 |
| `vol_leg0_call_545` | 0.2764 |
| `vol_leg1_call_585` | 0.2727 |

Leg PVs: `leg0_call_545` = $3.33mm, `leg1_call_585` = -$1.64mm.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Discrete ex-dividend schedule on the American legs (B1, forward-preserving); per-leg IV from a
sparse delayed chain; mid quotes; representative strikes/expiry.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
