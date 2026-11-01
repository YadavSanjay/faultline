# European swaptions (normal / Bachelier vol)

*Used by trade 23 (5Y×10Y European payer swaption).* This is the doc the UI's TRADE_23
diagnostics ("engine: BachelierSwaptionEngine … Bachelier (normal-vol) option on the underlying
swap; PV is the premium") point back to.

---

## In plain English

A **swaption** is an option on a swap: the right, but not the obligation, to *enter* a swap at a
pre-agreed fixed rate on a future date. "5Y×10Y payer" means: in 5 years' time (the **expiry**),
you may choose to start a 10-year swap in which you **pay fixed and receive floating**. You'd
exercise only if rates have risen enough that paying the pre-agreed fixed rate is a good deal.
The book holds it as a **rate-rise hedge** — cheap insurance that pays off if long rates spike.

Because the payoff depends on *where rates are in 5 years* — which is unknown today — we need a
**model of how uncertain rates are**: the **volatility**. Given the forward swap rate today, the
strike, the time to expiry and a volatility, an option-pricing formula gives the fair premium.
That premium is the PV.

The model we use is **Bachelier (normal) volatility**, which quotes the uncertainty in *rate
points* (basis points per year) rather than as a percentage. Normal vol is the market standard
for rates because it behaves well even when rates are near (or below) zero.

---

## How it's priced

1. **Build the underlying forward swap** — a 10Y SOFR swap starting in 5Y, using the SOFR curve.
2. **Determine the strike.** The trade is struck ATM (`strike: null`), so the strike is set to the
   underlying's **fair forward swap rate** — we build a probe swap and read its `fairRate()`.
3. **Get the volatility** — a flat normal vol (see below).
4. **Price with `ql.BachelierSwaptionEngine`** (physical settlement, European exercise at expiry).

> **Quantitative detail.** Under the normal (Bachelier) model the forward swap rate `S` is
> assumed to diffuse with constant absolute volatility `σ_N`. A payer swaption is a call on `S`
> struck at `K`, and its value is
> `PV = A(0) · [ (S−K)·Φ(d) + σ_N√T·φ(d) ]`, with `d = (S−K)/(σ_N√T)`, `Φ/φ` the standard normal
> CDF/PDF, `T` the time to expiry, and `A(0) = Σ_i τ_i·DF(t_i)` the **annuity** (PV of the
> underlying swap's fixed-leg accruals). In words: the premium is the annuity times the expected
> in-the-moneyness of the forward rate at expiry. **Vega** (`vega_1bp`) is computed by bumping
> `σ_N` by 1bp and repricing. Code:
> [`src/pricer/ql/rates/swaption_pricer.py`](../../../src/pricer/ql/rates/swaption_pricer.py).

### Where the volatility comes from (the free-data gap)

There is **no free swaption volatility surface**. So we build the vol in two steps. First the
**level** is read **live from the MOVE index** (the Treasury market's "VIX," in
[`move_index.csv`](../../../data/marketdata/stress/move_index.csv)) — MOVE is quoted in annualized
basis points of Treasury-yield implied vol, which *is* a normal (Bachelier) vol level, so
`normal_vol = MOVE_bp/1e4` (point-in-time, clamped). Second (**C1**), that single level is shaped
into an **expiry-dependent term structure** via a config multiplier
(`rates.normal_vol.term_structure`), so each swaption is priced with the vol at **its own expiry**
rather than one number for all expiries. This term-structure shape is **stylized and MOVE-anchored
— a documented modeling assumption, not a market-implied surface** (set `term_structure` to `{}` to
revert to flat). It is still **flat across strike**: the smile/cube needs licensed data (the free
DTCC SDR IR-options feed is not calibratable — ~20 of 540 rows usable, no option type).

The `vol_anchor` diagnostic records the MOVE level the mark sits on, and `normal_vol_bp` reports
the vol actually used at this option's expiry.

---

## Market data

| Input | Source | Notes |
|---|---|---|
| SOFR curve (forward swap + discounting) | Pensford forwards — [01-sofr-multicurve.md](01-sofr-multicurve.md) | builds the underlying swap |
| Normal volatility | config flat level (~90 bp), MOVE-anchored | no free vol surface |
| MOVE index (anchor/diagnostic) | [`move_index.csv`](../../../data/marketdata/stress/move_index.csv) | ICE BofA MOVE |

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `engine` | `BachelierSwaptionEngine` |
| `direction` | payer (right to pay fixed) |
| `expiry_date` / `underlying_maturity` | option expiry (5Y) / end of the underlying swap (15Y total) |
| `atm_or_strike` / `underlying_fair_rate` | the ATM strike = the forward swap rate |
| `normal_vol_bp` | the flat normal vol used (≈90 bp) |
| `vega_1bp` | PV change per 1bp of normal vol |
| `vol_anchor` | the config level + latest MOVE reading |

---

## Limitations & assumptions

- **Stylized vol term structure, flat across strike** — the expiry dependence (C1) comes from a
  MOVE-anchored config shape, not a market surface, and there is still no smile. The single
  biggest modelling simplification for this trade; vega risk is therefore one-dimensional.
- **MOVE is a label, not yet a driver** of the vol level.
- **ATM strike** because the ticket specifies `strike: null`.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/rates/swaption_pricer.py`](../../../src/pricer/ql/rates/swaption_pricer.py) |
| Vol sourcing | [`src/pricer/loader/rates_adapter.py`](../../../src/pricer/loader/rates_adapter.py) (`normal_vol`) |
| Config | `rates.normal_vol.*` in [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json) |
