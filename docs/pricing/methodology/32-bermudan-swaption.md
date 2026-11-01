# Bermudan swaptions (Hull-White, lattice pricing)

*Used by trade 24 (2Y×5Y Bermudan receiver swaption).* This is the most model-intensive trade in
the book, so it gets the longest treatment.

---

## In plain English

A **Bermudan** swaption is like a European swaption (the right to enter a swap) except you can
exercise on **several dates**, not just one. Our trade: starting in 2 years, and on each
anniversary after that, you may choose to enter a 5-year swap where you **receive fixed and pay
floating** (a *receiver*). You'd exercise when rates have *fallen* enough that locking in the
fixed receive rate is attractive.

The extra exercise dates make it strictly more valuable than a one-shot European — but also much
harder to price. The difficulty is the **decision** at each date: "exercise now, or wait for a
possibly better opportunity?" Answering that requires simulating how rates can evolve and, at
every node, comparing the value of exercising against the value of holding on. You can't do this
with a single formula; you need a **dynamic model of the whole yield curve** and a **lattice**
(a tree of possible future rate paths) to roll the decision backwards from the last date to today.

We use the **Hull-White** model — a clean, widely-used "one-factor" description of how the short
rate wanders over time, mean-reverting toward the forward curve. We **calibrate** it so that it
reproduces the prices of the related European swaptions (so the Bermudan is consistent with the
vanilla market it lives in), then price the Bermudan on a tree.

---

## How it's priced — three stages

### 1. Hull-White model

> **Quantitative detail.** Hull-White assumes the short rate `r(t)` follows
> `dr = [θ(t) − a·r] dt + σ dW`: it mean-reverts at speed `a` toward a time-dependent level `θ(t)`
> (chosen so the model exactly fits today's discount curve) with volatility `σ`. Two free
> parameters, `a` (mean reversion) and `σ` (volatility), control the dynamics. The model is built
> off the SOFR discount curve: `ql.HullWhite(discount_handle)`.

### 2. Calibration to co-terminal European swaptions

We pick the strip of European swaptions that *co-terminate* with the Bermudan (expiries 2Y, 3Y,
4Y, 5Y, 6Y, each into a swap ending at the common 7Y final maturity), price them with the flat
normal vol **at each helper's expiry** from the MOVE-anchored term structure (C1 — see
[31-swaptions.md](31-swaptions.md); each co-terminal target gets its own vol, so the calibration
sees a real vol slope, which is what identifies the mean reversion `a`), and tune
`a` and `σ` so Hull-White reproduces those European prices as closely as possible.

> **Quantitative detail.** Each calibration instrument is a `ql.SwaptionHelper` priced under
> Hull-White by `ql.JamshidianSwaptionEngine`; the fit minimises relative price error via
> `ql.LevenbergMarquardt`. The result is reported as `calibration_rmse` (a good fit is ~1e-4).
> Typical output: `a ≈ 0.056`, `σ ≈ 0.0105`.

### 3. Lattice pricing with backward induction

With `a` and `σ` fixed, build a trinomial **tree** of short-rate states over the 7-year horizon
(`ql.TreeSwaptionEngine`, 100 steps) and roll the Bermudan's value backward: at each exercise
date and each node, `value = max(exercise now, expected discounted value of holding)`. The value
at the root, today, is the PV.

> **Cross-check.** The same model is re-priced with a finite-difference engine
> (`ql.FdHullWhiteSwaptionEngine`); the two should agree closely (`tree_fd_rel_diff`, typically
> well under 1%), which is our internal sanity check that the lattice is converged. Code:
> [`src/pricer/ql/rates/swaption_pricer.py`](../../../src/pricer/ql/rates/swaption_pricer.py).

---

## Market data

| Input | Source | Notes |
|---|---|---|
| SOFR curve (model base + underlying swaps) | Pensford forwards — [01-sofr-multicurve.md](01-sofr-multicurve.md) | drives `θ(t)` and discounting |
| Normal volatility (calibration targets) | config flat level (~90 bp), MOVE-anchored | same gap as European swaptions |

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `engine` | `TreeSwaptionEngine(steps=100) + HullWhite co-terminal` |
| `crosscheck_engine` | `FdHullWhiteSwaptionEngine` |
| `atm_or_strike` | ATM forward swap rate (the strike) |
| `hw_mean_reversion` (`a`) / `hw_sigma` (`σ`) | the calibrated Hull-White parameters |
| `calibration_rmse` | calibration fit quality (smaller is better) |
| `pv_tree` / `pv_fd_crosscheck` / `tree_fd_rel_diff` | the two engine PVs and their relative gap |
| `n_exercise_dates` | how many exercise opportunities (annual, 2Y→6Y) |
| `first_exercise` / `final_maturity` | first callable date / common terminal date |

---

## Limitations & assumptions

- **One-factor Hull-White** — captures the level of rates but not curve-shape (steepening/twist)
  optionality; adequate for a single-callable structure, less so for spread-sensitive Bermudans.
- **Calibrated to a MOVE-anchored, stylized vol term structure** (C1) — the co-terminal targets now
  carry an expiry-dependent vol slope (which identifies the mean reversion), but the shape is a
  documented modeling assumption, not a market surface, and is still flat across strike (no skew).
  A real cube would give the targets a market term structure and skew.
- **Lattice discretisation** (100 steps) — the FD cross-check guards against under-convergence.
- **ATM strike**, representative notional.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer (calibration + tree + FD cross-check) | [`src/pricer/ql/rates/swaption_pricer.py`](../../../src/pricer/ql/rates/swaption_pricer.py) |
| Vol sourcing | [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py) |
| Trade definition | [`src/pricer/input/rates/instruments.py`](../../../src/pricer/input/rates/instruments.py) |
