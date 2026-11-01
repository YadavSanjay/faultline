# TRADE_24 — 2Y×5Y Bermudan receiver swaption

**Asset class:** rates · **Trade type:** `bermudan_swaption` · **Methodology:** [Bermudan swaptions](../methodology/32-bermudan-swaption.md)

## In plain English

The right — exercisable on **several** dates starting in 2 years — to enter a 5-year swap in which
we **receive fixed and pay floating** (a *receiver*). We'd exercise when rates have **fallen** enough
that locking in the fixed receive rate is attractive. Having multiple exercise dates makes it worth
more than a one-shot European, but it's harder to price: we must value the choice "exercise now vs.
wait" at every date, which needs a dynamic interest-rate model on a lattice.

## The trade (economics)

Contractual terms from the active default book (**core** sizing — `portfolio_core.json`):

| Term | Value |
|---|---|
| Notional | $113.22mm (core book) |
| direction | receiver |
| option tenor | 2Y |
| swap tenor | 5Y |
| exercise frequency | Annual |
| settlement | physical |
| Currency | USD |

## Market data

**Conceptually required:** the SOFR curve and a volatility for the rate options used to calibrate the
model.

**Actually used (free sources):** SOFR curve from Pensford forwards; the same **MOVE-anchored normal
vol term structure** (C1) as the European swaption — each co-terminal calibration target now uses
the vol at **its own expiry**, so the vol slope identifies the Hull-White mean reversion (instead of
one flat number). Stylized shape, flat across strike. See
[Bermudan swaptions methodology](../methodology/32-bermudan-swaption.md).

## How it's priced

Three stages: (1) build a **Hull-White** one-factor model off the SOFR curve; (2) **calibrate** its
mean-reversion `a` and volatility `σ` to the co-terminal strip of European swaptions; (3) price the
Bermudan by backward induction on a 100-step trinomial **tree** (`ql.TreeSwaptionEngine`), with a
finite-difference engine as cross-check.

## Reading the live diagnostics

Live valuation at **2026-10-06** on the **core** book (real ~4% SOFR curve): **PV = $3.26mm**.

| Measure | Value |
|---|---|
| `atm_or_strike` | 0.04862 |
| `normal_vol_bp` | 102.6 |
| `notional` | $113.22mm |
| `hw_mean_reversion` | 0.02731 |
| `hw_sigma` | 0.01088 |
| `pv_tree` | 3,263,522.78 |
| `pv_fd_crosscheck` | 3,247,117.35 |
| `tree_fd_rel_diff` | 0.005027 |
| `calibration_rmse` | 0.0002837 |
| `n_exercise_dates` | 5 |

_Regenerated from the stored per-trade result (`data/pricer/PV/<asset>/<trade>/2026-10-06.json`) by `scripts/pricer/gen_trade_docs.py`._

## Risk & stress treatment

*How this trade is treated in the **risk & stress** engines (market-intelligence P0–P7). Pricing (PV) is unchanged — these are scenario / VaR dimensions, disabled-reversible and stamped into the scenario report.*

- **Liquidity horizon (F1):** 20d — IG credit / rates vol; a `√(20/10)` scaler deepens adverse P&L into an exit-value estimate, plus a bid-ask haircut.
- **Collateral (F2):** **margined** — a MtM loss posts variation margin (in the cash-outflow line), and an IM-increase proxy is added under a vol shock.

## Limitations & assumptions

One-factor Hull-White (level, not curve-shape, optionality); calibrated to a MOVE-anchored vol term
structure whose shape is a stylized assumption (C1) and is flat across strike (no smile); lattice
discretisation guarded by the FD cross-check; ATM
strike; core-book notional.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer (calibration + tree + FD cross-check) | [`src/pricer/ql/rates/swaption_pricer.py`](../../../src/pricer/ql/rates/swaption_pricer.py) |
| Vol sourcing | [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py) |
| Trade definition | [`src/pricer/input/rates/instruments.py`](../../../src/pricer/input/rates/instruments.py) |
