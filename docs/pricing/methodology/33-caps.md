# Interest-rate caps (Bachelier caplets)

*Used by trade 25 (1Y SOFR cap, quarterly, ATM).*

---

## In plain English

A **cap** is insurance against rates rising. It pays you whenever the floating rate (SOFR) over a
period exceeds an agreed **strike** — so your effective borrowing cost is "capped." A 1-year cap
with quarterly resets is really a bundle of four small options, one per quarter, called
**caplets**. Each caplet pays `max(SOFR − strike, 0)` on its period's interest. The book holds it
as a **tail hedge on funding cost** — cheap protection if short rates spike.

The value of each caplet depends, again, on how uncertain SOFR is — its **volatility** — plus the
forward SOFR rate for that period and a discount factor. Sum the four caplet values and you have
the cap's premium, which is the PV.

---

## How it's priced

Build a quarterly SOFR leg over the cap's life, set the strike (ATM by default), and price the
bundle of caplets with `ql.BachelierCapFloorEngine` (normal-vol caplets), summing their values.

> **Quantitative detail.** Each caplet is a Bachelier (normal-vol) call on the forward rate `L`
> for its period: `caplet = DF(T_pay)·N·τ·[ (L−K)·Φ(d) + σ_N√T·φ(d) ]`, `d=(L−K)/(σ_N√T)`, with
> `N` the notional, `τ` the period accrual (~0.25), `K` the strike, `σ_N` the normal vol, `T` the
> time to the caplet's fixing. The **ATM strike** (when `strike: null`) is the cap's forward par
> rate, `cap.atmRate(discount_curve)`. **Vega** is a 1bp normal-vol bump-and-reprice. Code:
> [`src/pricer/ql/rates/cap_pricer.py`](../../../src/pricer/ql/rates/cap_pricer.py).

The same MOVE-anchored normal vol used for swaptions is used here, taken at the cap's expiry from
the term structure (C1) — see
[31-swaptions.md](31-swaptions.md) for how that level is sourced.

> **Modelling note.** SOFR caplets are built on an `ql.IborLeg` over `ql.Sofr`, i.e. each
> overnight-compounded caplet is approximated as a forward-rate caplet. This trades a little
> compounding precision for simplicity and is flagged in the `caplet_note` diagnostic.

---

## Market data

| Input | Source | Notes |
|---|---|---|
| SOFR curve (forward rates + discounting) | Pensford forwards — [01-sofr-multicurve.md](01-sofr-multicurve.md) | |
| Normal volatility | config flat level (~90 bp), MOVE-anchored | no free cap vol surface |

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `engine` | `BachelierCapFloorEngine` |
| `atm_or_strike` | the cap strike (= forward par rate if ATM) |
| `normal_vol_bp` | the flat normal vol used (≈90 bp) |
| `vega_1bp` | PV change per 1bp of normal vol |
| `n_caplets` | number of caplets (4 for a 1Y quarterly cap) |
| `vol_anchor` / `caplet_note` | config level + MOVE; the forward-caplet approximation note |

---

## Limitations & assumptions

- **Expiry-level vol from a stylized MOVE-anchored term structure (C1), flat across strike** — one
  vol at the cap's expiry (no caplet-by-caplet stripping), no strike smile.
- **Forward-caplet approximation** of overnight-compounded SOFR caplets.
- **ATM strike**, representative notional.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/rates/cap_pricer.py`](../../../src/pricer/ql/rates/cap_pricer.py) |
| Vol sourcing | [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py) |
| Config | `rates.normal_vol.*` in [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json) |
