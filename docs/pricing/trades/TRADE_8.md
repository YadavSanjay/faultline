# TRADE_8 — NVDA 6M ATM put (American, protective)

**Asset class:** options · **Trade type:** `equity_option` · **Methodology:** [Equity options](../methodology/11-equity-options.md)

## In plain English

A long, roughly at-the-money put on NVIDIA — the right to **sell** NVDA at $205 before December
2026. It is downside insurance on the large NVDA long ([TRADE_1](TRADE_1.md)): if NVDA falls, the
put gains and cushions the loss.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $32.88mm (core book) |
| underlier | NVDA |
| structure | vanilla |
| contract multiplier | 100 |
| Leg 1 | right=put strike=205 expiry=2026-12-18 exercise=american quantity=1604 |
| Currency | USD |

## Market data

**Conceptually required:** spot, strike/expiry, dividend yield, risk-free curve, volatility.

**Actually used (free sources):** NVDA option chain
([`NVDA_chains.csv`](../../../data/marketdata/options/chains/NVDA_chains.csv), CBOE delayed) for the
per-strike IV and put-call-parity-recovered spot and dividend yield; SOFR curve for discounting.

## How it's priced

American put ⇒ `ql.FdBlackScholesVanillaEngine` (finite-difference, captures early-exercise value,
which matters more for puts) on a Black-Scholes-Merton process. As an American leg on a (small)
dividend payer, it uses a **forward-preserving discrete ex-dividend schedule** (B1) rather than a
continuous yield; NVDA's dividend is tiny so the effect is small. See
[Equity options methodology](../methodology/11-equity-options.md).

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $517,336**.

| Measure | Value |
|---|---|
| `delta_shares` | -23,438.22 |
| `delta_cash` | -$5.61mm |
| `gamma` | 927 |
| `vega_1volpt` | $39,765 |
| `spot` | 239.2 |
| `dividend_yield` | -0.220% |
| `discrete_dividend_legs` | 0 |
| `vol_leg0_put_205` | 0.3729 |

Leg PVs: `leg0_put_205` = $517,336.

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 40d — equity vol / single-name CDS; a `√(40/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** unmargined — moves the mark but calls no cash (so net cash outflow can differ in sign from P&L).
- **Options intelligence (B1–B4):** a per-name option-implied vol/skew overlay and a market-implied tail-probability plausibility check (the scenario's equity move is graded against the RND, e.g. market-implied P(≤ −30%)); long-duration options also carry real-rate (F6) sensitivity.

## Limitations & assumptions

Discrete ex-dividend schedule on the American FD grid (B1; NVDA's dividend is small); delayed/weekly
chain; mid IVs; representative strike/expiry (contract count is the core-book size).

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
