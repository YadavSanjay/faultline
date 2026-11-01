# TRADE_10 — META 6M put spread (skew play, American)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A **put spread** on Meta: buy a $580 put and sell a $500 put, same December 2026 expiry. This is a
defined-risk bearish/hedge structure — it pays if META falls toward $500, with the protection
capped below $500 (and partly financed by selling the lower put). It's also a "skew play":
out-of-the-money puts trade at higher implied vols, and the structure expresses a view on that
skew.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| underlier | META |
| structure | put_spread |
| contract multiplier | 100 |
| Leg 1 | right=put strike=580 expiry=2026-12-18 exercise=american quantity=1952 |
| Leg 2 | right=put strike=500 expiry=2026-12-18 exercise=american quantity=-1952 |
| Currency | USD |

## Market data

**Conceptually required:** spot, both strikes/expiry, dividend yield, risk-free curve, vol at each
strike (the skew is the whole point).

**Actually used (free sources):** META option chain
([`META_chains.csv`](../../../data/marketdata/options/chains/META_chains.csv), CBOE delayed) — the
$580 and $500 puts each read their own skew-aware IV; spot/dividend recovered via put-call parity;
SOFR discounting.

## How it's priced

Each leg priced with `ql.FdBlackScholesVanillaEngine` (American), summed by signed quantity:
`PV = PV(long 580 put) − PV(short 500 put)`. As American legs on a dividend payer (META), both use a
**forward-preserving discrete ex-dividend schedule** (B1), which slightly reshapes the put
early-exercise value vs a continuous yield. See
[Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $692,819**.

| Measure | Value |
|---|---|
| `delta_shares` | -10,317.85 |
| `delta_cash` | -$7.63mm |
| `gamma` | 137 |
| `vega_1volpt` | $58,816 |
| `spot` | 739.3 |
| `dividend_yield` | -0.206% |
| `discrete_dividend_legs` | 0 |
| `vol_leg0_put_580` | 0.4108 |
| `vol_leg1_put_500` | 0.4567 |

Leg PVs: `leg0_put_580` = $923,532, `leg1_put_500` = -$230,713.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Discrete ex-dividend schedule on the American legs (B1, forward-preserving); per-leg IV from a sparse delayed chain; mid quotes; representative
strikes/expiry.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
