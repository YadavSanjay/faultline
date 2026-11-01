# Interest-rate swaps (SOFR OIS)

*Used by trade 22 (10Y SOFR payer IRS).*

---

## In plain English

An interest-rate swap exchanges two streams of interest payments on the same notional. In a SOFR
swap, one side pays a **fixed** rate (agreed at the start) and the other pays **floating** SOFR
(whatever the overnight rate turns out to be, compounded). No principal changes hands — only the
interest. A **payer** swap pays fixed and receives floating; it gains value when rates rise
(because the floating you receive grows while your fixed payment is locked).

Our trade is a payer swap held as a **hedge for the bond book**: if rates rise, the bonds lose
value but this swap gains, offsetting.

Pricing is pure discounting — there's no optionality. We project the floating SOFR payments off
the SOFR curve, compare them to the known fixed payments, and discount the net to today. If the
fixed rate we agreed equals today's fair market rate, the swap is worth ≈0; the PV is essentially
the present value of the difference between our fixed rate and the current fair rate.

---

## How it's priced

Built with `ql.MakeOIS` and valued with QuantLib's `DiscountingSwapEngine` (set automatically),
using the SOFR curve for **both** projection and discounting (single-curve OIS).

> **Quantitative detail.** `PV_payer = PV_float − PV_fixed`, where
> `PV_fixed = N · Σ_i K·τ_i·DF(t_i)` (fixed rate `K`, accrual `τ_i`) and the float leg is the
> present value of compounded-SOFR coupons projected off the same curve. Equivalently
> `PV ≈ N · (fair_rate − K) · Annuity`, with `Annuity = Σ_i τ_i·DF(t_i)`. The **DV01** is the
> dollar value of a 1bp change in the fixed rate — essentially the annuity × notional × 1e-4 — and
> `fair_rate` is the fixed rate that would make the swap worth zero today. Conventions: Annual
> fixed and float, `Act/360`, ModifiedFollowing, `UnitedStates.GovernmentBond` calendar, 2-day
> payment lag, T+2 start. Code:
> [`src/pricer/ql/rates/swap_pricer.py`](../../../src/pricer/ql/rates/swap_pricer.py).

---

## Market data

**Conceptually required:** a curve that both *projects* future SOFR and *discounts* — i.e. the
SOFR OIS curve.

**Actually used (free sources):** the SOFR curve bootstrapped from Pensford forwards —
see [01-sofr-multicurve.md](01-sofr-multicurve.md). That's the only market input.

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `fair_rate` | the par/break-even fixed rate for this swap today |
| `fixed_rate` | the contractual fixed rate (3.8%) |
| `dv01` | PV change per 1bp move in the fixed rate |
| `fixed_leg_bps` | sign-aware fixed-leg sensitivity (negative = payer liability on the fixed leg) |
| `leg_pvs.fixed` / `leg_pvs.float` | the two leg PVs (net = trade PV) |
| `engine` / `direction` / `start_date` / `maturity_date` | `DiscountingSwapEngine`, payer, schedule ends |

If `fair_rate > fixed_rate`, a payer swap is in-the-money (you locked in paying less than the
market now demands).

---

## Limitations & assumptions

- **Single-curve OIS** — same curve discounts and forecasts; no OIS/SOFR basis.
- **Curve staleness** propagates as a `warn` (see foundations).
- **Notional ($50mm) and fixed rate are representative.**
- **No realized-SOFR fixings wired (C4) — latent, not a live gap.** Every rate trade in the book
  **spot-starts** (T+2 lag; the swaption underlyings are forward-starting), so no coupon period has
  begun accruing and the whole float leg is projected off the forward curve — no past fixing is
  needed. A *seasoned, in-accrual* OIS would need its current period valued on the **published**
  realized SOFR. That data is available for free
  ([`nyfed_sofr_rates.csv`](../../../data/marketdata/rates/nyfed_sofr_rates.csv),
  `pensford_historical_sofr.csv`) and would be wired via `ql.IndexManager` (`Sofr.addFixing`) at
  pricing time; it is deliberately not loaded today because no current trade exercises that path.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/rates/swap_pricer.py`](../../../src/pricer/ql/rates/swap_pricer.py) |
| Curve | [`src/pricer/ql/common/curve_builder.py`](../../../src/pricer/ql/common/curve_builder.py) |
| Trade definition | [`src/pricer/input/rates/instruments.py`](../../../src/pricer/input/rates/instruments.py) |
