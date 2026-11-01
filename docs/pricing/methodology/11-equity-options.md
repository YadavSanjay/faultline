# Equity options

*Used by trades 7–14 and 35: vanilla calls/puts, call/put spreads, a straddle, a collar and a
deep-OTM tail-convexity put (#35), on single names (AAPL, NVDA, MSFT, META, TSLA) and indices/ETFs
(SPX, QQQ), American and European.*

---

## In plain English

An option is the *right* (not the obligation) to buy (call) or sell (put) the underlying at a
fixed **strike** price by a fixed **expiry**. Because the holder only exercises when it pays to,
an option's value has two parts: the **intrinsic value** (what you'd get exercising today) and
the **time value** (the chance the market moves further in your favour before expiry). The more
the stock can swing — its **volatility** — the more that "chance" is worth.

To price one we need four things: where the stock is now (**spot**), the **strike** and
**expiry** (from the contract), how much the stock pays in **dividends** (which drag on a call
and help a put), the **interest rate** to discount, and the **volatility**. Plug those into the
Black-Scholes-Merton model and out comes a fair premium.

Two wrinkles this book handles:
- **American vs European.** A European option can only be exercised *at* expiry (closed-form
  Black-Scholes). An American option can be exercised *any time*, which is slightly more
  valuable; that early-exercise right needs a small numerical solver rather than a formula.
- **Spot and dividends from the chain.** We have no paid spot feed, so we *recover* the spot
  price and the dividend yield from the option chain itself using put-call parity — the prices
  of calls and puts together pin down where the underlying must be.

---

## How it's priced

The underlying follows Black-Scholes-Merton dynamics under the risk-neutral measure:

> **Quantitative detail.** `dS/S = (r − q) dt + σ dW`, where `r` is the SOFR rate, `q` the
> continuous dividend yield, and `σ` the implied volatility. For a **European** option the
> price is the closed-form Black-Scholes-Merton value (`ql.AnalyticEuropeanEngine`):
> `C = S·e^{−qT}·N(d₁) − K·e^{−rT}·N(d₂)` (call; put by parity), with
> `d₁ = [ln(S/K) + (r − q + ½σ²)T]/(σ√T)`, `d₂ = d₁ − σ√T`. For an **American** option we solve
> the same PDE on a finite-difference grid (`ql.FdBlackScholesVanillaEngine`, 100×100) with an
> early-exercise boundary, capturing the extra value of exercising before expiry. Day count
> `Actual365Fixed`. Code:
> [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py).

**Multi-leg structures** (spreads, straddles, collars) are priced **leg-by-leg and summed**:
`PV = Σ_legs (quantity × contract_multiplier × per-share value)`, with `quantity` signed (long
> 0, short < 0) and `contract_multiplier = 100`. Greeks (delta, gamma) add linearly across legs;
vega is computed by a +1 vol-point bump-and-reprice across the whole structure.

### Recovering spot & dividend yield from the chain

We don't buy a spot feed; we infer it. For each expiry, put-call parity says the implied forward
at strike `K` is `F(K) = K + e^{rT}·(C(K) − P(K))`. Averaging across liquid strikes gives a
forward per expiry; regressing `ln F` against `T` gives **spot** (`= e^{intercept}`) and the
**continuous dividend yield** (`q = r − slope`). Tested to ≈0.07% on NVDA. Code:
[`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py)
(`_recover_spot_and_div`).

### Volatility — skew-aware, per leg

Each leg gets its **own** implied volatility, read from the chain at that leg's strike and
expiry. The surface interpolates linearly in strike *within* an expiry and in **total variance**
(`σ²·t`) *across* expiries, so the skew (puts richer than calls) and the term structure are both
respected. IV is floored at 3% and capped at 250%. Code: `EquityVolSurface.implied_vol` in
[`src/pricer/input/common/market_data.py`](../../../src/pricer/input/common/market_data.py).

> **The full surface, and why we don't feed it to the FD engine as local vol (B2).** We build a
> rectangular `ql.BlackVarianceSurface` (strike × expiry) from the chain and **display it as a
> heatmap** on the Explain screen (with each leg marked), so you can see the exact smile point each
> leg prices off. We deliberately **do not** hand that surface to the American FD engine as a
> *local-volatility* surface: `FdBlackScholesVanillaEngine` would derive a Dupire local vol whose
> strike-curvature and short-dated forward-variance are dominated by the noise of the sparse, delayed
> free chain — in testing that over-priced a 1-month ATM straddle by ~8%. Instead each leg is priced
> with its own exact (strike, expiry) IV from the surface, which is the correct first-order skew
> treatment; the second-order effect of skew on the American early-exercise *boundary* is a documented
> data limitation (it needs a clean, liquid surface to be stable). Builder:
> `build_equity_black_variance_surface` in
> [`src/pricer/ql/common/vol_builder.py`](../../../src/pricer/ql/common/vol_builder.py).

---

## Market data

**Conceptually required:** spot, strike/expiry (contract), dividend schedule, risk-free curve,
volatility surface.

**Actually used (free sources):**

| Input | Source | Notes |
|---|---|---|
| Option chain (per name) | CBOE delayed quotes → `data/marketdata/options/chains/<name>_chains.csv` | weekly snapshots; supplies per-strike IV |
| Spot + dividend yield | **recovered** from the chain via put-call parity | not an external feed |
| Risk-free discount | SOFR OIS curve — see [01-sofr-multicurve.md](01-sofr-multicurve.md) | |
| Vol indices — VIX term complex (VIX9D/VIX/VIX3M/VIX6M), SKEW, VXAPL/… | CBOE | **risk factors, not pricing inputs** (C3) — see below |

---

## Reading the diagnostics

| Field | Meaning |
|---|---|
| `delta_shares` / `delta_cash` | sum of leg deltas (per-share and ×spot) |
| `gamma` | sum of leg gammas |
| `vega_1volpt` | PV change for +1 vol point across all legs |
| `spot` / `dividend_yield` | recovered from the chain (not external) |
| `vol_leg<i>_<right>_<strike>` | the implied vol used for each leg |
| `engines` | `AnalyticEuropean (Eur) / FdBlackScholes (Amer)` |
| `spot_div_source` | `option_chain_put_call_parity` |
| `leg_pvs.leg<i>_…` | per-leg PV (signed by quantity) |

---

## Limitations & assumptions

- **Chain snapshots are delayed and weekly** (CBOE free tier) — the IV surface is sparse and the
  snapshot may be older than the curve.
- **Discrete dividends on American legs (B1).** American legs on dividend payers (AAPL, NVDA,
  MSFT, META, QQQ) now price with a **projected discrete ex-dividend schedule** so the FD engine
  can time early exercise to each ex-date — the effect a continuous yield smears out. The ex-dates
  are real (rolled forward from the dividend feed at its observed quarterly cadence, latest amount
  held); the amounts are **scaled by one factor so their discounted sum equals the chain-implied
  dividend value** `S·(1−e^{−qT})`, which keeps the option's forward — and therefore its
  European-equivalent value — identical to the continuous-yield model. Only the American
  early-exercise value moves (e.g. the ATM AAPL LEAPS call picks up its early-exercise premium).
  Non-payers (TSLA), the SPX index (no per-share dividend feed), and all European legs keep the
  continuous chain-implied yield.
- **No borrow/repo cost** modelled; mid quotes used (no bid-ask).
- **The VIX term complex is not a pricing input (C3).** Each underlier's own option chain already
  supplies the full skew *and* term structure the pricer uses (per-leg IV; the B2 surface), so the
  index vol-term indices (VIX9D/VIX/VIX3M/VIX6M for SPX, VXAPL for AAPL, SKEW, …) add nothing to
  pricing — they would only help *anchor/extrapolate* a chain that was too sparse at some expiry.
  They are consumed only as **risk vega factors** (`EQVOL_*`, `EQVOL_TERM = VIX3M−VIX`, `EQVOL_SKEW`
  in the risk config). This is a deliberate boundary, not a gap: the chain is authoritative for the
  mark. (A cheap sanity cross-check — SPX chain 30d ATM IV vs VIX — is a data-quality idea, not a
  pricing change.)
- **Strikes/expiries/contract counts are representative placeholders** chosen near recovered spot
  on real chain dates.

## Where this lives in the code

| Component | Path |
|---|---|
| Pricer | [`src/pricer/ql/equity/option_pricer.py`](../../../src/pricer/ql/equity/option_pricer.py) |
| Spot/div recovery + vol surface | [`src/pricer/loader/equity_adapter.py`](../../../src/pricer/loader/equity_adapter.py) |
| Vol-surface interpolation | [`src/pricer/input/common/market_data.py`](../../../src/pricer/input/common/market_data.py) |
| Trade definition | [`src/pricer/input/equity/instruments.py`](../../../src/pricer/input/equity/instruments.py) |
| Config | `options.*` in [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json) |
