# Foundations — the shared pricing envelope

*Applies to every trade in the portfolio.* This page explains the things that are true no
matter which instrument we price: what a "present value" means here, how the valuation date
and market-data snapshot are pinned, and the provenance/quality machinery that wraps every
result. Read this once; the per-instrument docs assume it.

---

## In plain English

Every trade we hold has a **present value (PV)** — what it is worth *today* in dollars. For a
share of stock that's almost trivial (shares × price). For a 10-year swap or a Bermudan
swaption it takes real machinery. But the idea is always the same:

1. Work out the **future cash flows** the trade will pay or receive (and, for options, the
   *probability-weighted* cash flows).
2. **Discount** each future dollar back to today — a dollar in five years is worth less than a
   dollar now, and the discount curve tells us exactly how much less.
3. Add them up. That sum is the PV.

Two dollars of the same trade priced on two different days will differ because the **market
data** moved. So we are very strict about *which* market data went into a number: every PV is
stamped with the exact data snapshot, code version, and config it came from. Re-run the same
inputs and you get the identical number — and you can prove it. That's "reproducibility," and
it's the backbone of a defensible mark.

---

## The discount curve — the one idea behind everything

Almost every PV in this book is **a sum of future cash flows multiplied by discount factors**.
A *discount factor* `DF(t)` is the value today of $1 paid at time `t`; it is below 1 and falls
as `t` grows. The collection of `DF(t)` across all future dates is the **discount curve**.

> **Quantitative detail.** For a stream of known cash flows `C_i` at times `t_i`,
> `PV = Σ_i C_i · DF(t_i)`. The discount factor relates to the continuously-compounded zero
> rate `z(t)` by `DF(t) = exp(−z(t)·t)`. Building the curve = inferring `DF(t)` (equivalently
> `z(t)`) at every date from a handful of liquid market quotes. How we do that for USD rates is
> the subject of [01-sofr-multicurve.md](01-sofr-multicurve.md); it is reused by rates, bonds,
> FX and credit. Equity and FX add their own ingredients (dividend yield, foreign curve, spot)
> on top of the same discounting idea.

For instruments with *uncertain* cash flows (options, swaptions, caps, CDS) we additionally
need a **model of the uncertainty** — a volatility for options, a default-intensity (hazard)
curve for CDS. Those live in the per-instrument docs.

---

## The valuation date

Pricing is always *as of* one date — the **valuation date**. The portfolio default is
`2026-10-06`, a date with an exact Pensford SOFR forward snapshot (our binding curve input; see
[01-sofr-multicurve.md](01-sofr-multicurve.md)). The live figures on the trade pages are valued at
that date; some worked examples in these methodology docs use earlier snapshots (the 2026-05-29
curve, the 2026-06-17 option chains) and say so. You can override with `--valuation-date`.

The valuation date does three things: it sets QuantLib's global evaluation date
(`ql.Settings.instance().evaluationDate`) so every curve and schedule rolls off the same "today";
it selects which market-data row to use ("latest on or before the valuation date"); and it
anchors every time-to-maturity used in discounting and option pricing.

> Source: [`src/pricer/ql/common/base_pricer.py`](../../../src/pricer/ql/common/base_pricer.py)
> sets the evaluation date before every pricing call; the as-of selection is in
> [`src/pricer/loader/staleness.py`](../../../src/pricer/loader/staleness.py) (`pick_snapshot`).

---

## "Free sources only" — and how we cope with the gaps

A deliberate constraint of this platform is that **all market data comes from free, public
sources** (US Treasury, NY Fed, Pensford, ECB, Fed H.10, BOJ, CBOE delayed quotes, DTCC/CFTC
trade dissemination, Stooq, …). That is enough to build a credible book, but free data has
holes that licensed feeds (Bloomberg, Markit) would fill. Where a needed input has no free
source, we make an **explicit, documented assumption** rather than silently dropping the
trade. The recurring ones:

| Missing free data | What we do instead | Affects |
|---|---|---|
| Swaption / cap volatility surface | **Flat normal (Bachelier) vol**, ~90 bp, MOVE-index-anchored | swaptions 23–24, cap 25 |
| Single-name CDS term structure | **5Y par spread only → flat hazard**; config fallback if feed is sparse | credit 28–30, corp bonds 15–19 |
| Clean issuer credit spreads | **Config fallback Z-spread per issuer** (free CDS feed too noisy) | corp bonds 15–19 |
| TWD / cross-currency basis curve | **Flat foreign rate** (CBC policy rate), **zero XCCY basis** | FX swap 26 |
| Discrete dividend calendar | **Continuous flat dividend yield** recovered from the option chain | options 7–14 |

Each per-instrument doc spells out its own assumptions. The principle: **the number is always
reproducible and the simplification is always stated.**

---

## The result envelope: every PV carries its pedigree

Whatever the instrument, the pricer returns the same `PricingResult` shape
([`src/pricer/ql/common/pricing_result.py`](../../../src/pricer/ql/common/pricing_result.py)):

- **`pv`** — the present value in trade currency (USD throughout this book).
- **`leg_pvs`** — PV broken out by leg (e.g. fixed vs float on a swap), where meaningful.
- **`measures`** — risk and analytic numbers (DV01, delta, vega, par/fair rate, hazard, …).
- **`diagnostics`** — human-readable strings describing the engine, dates, and data source —
  this is exactly what the UI's *Diagnostics* panel shows.
- **`warnings`** — data-quality flags (e.g. "SOFR snapshot is 19d stale, tolerance 7d").
- **`provenance`** — the pedigree (see below).

### Provenance & the market-data snapshot

Each result is stamped by `RunProvenance`
([`src/common/provenance.py`](../../../src/common/provenance.py)) with: a `run_id`, the
`git_sha` of the code, the `quantlib_version` (1.42.1), the `python_version`, a `config_hash`,
the `priced_at` UTC timestamp, the `pricer_class` used, and a
**`market_data_snapshot_id`**. The snapshot
([`src/pricer/loader/snapshot.py`](../../../src/pricer/loader/snapshot.py)) SHA-256-fingerprints
every input CSV the run touched and combines them into one id. Same code + same snapshot ⇒
byte-identical PVs. This is what makes a mark *defensible*: you can point to the exact file
contents that produced it.

### Data-quality gate

Before pricing, inputs are checked for staleness/availability against the `data_quality` policy
in [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json). Each data
*kind* has an action — `warn` (price anyway, attach a warning), `block` (refuse to price), or
`substitute`. In this book most kinds are `warn` with a 7-day staleness tolerance, which is why
you often see the stale-SOFR warning when valuing on a date later than the last curve snapshot.

---

## How a single trade flows through the system

```
portfolio.json ─▶ Trade object ─▶ market_data_requirements()
                                       │
                                       ▼
                          MarketDataLoader.resolve()  ──reads──▶ data/marketdata/*.csv
                                       │                          (snapshot-fingerprinted)
                                       ▼
                          MarketDataSet  ─▶  get_pricer(trade)  ─▶  <Instrument>Pricer
                                                                        │ (QuantLib)
                                                                        ▼
                                              PricingResult  ◀── provenance stamp
```

The orchestration lives in [`src/pricer/engine.py`](../../../src/pricer/engine.py)
(`PricingEngine.price` / `price_portfolio`). One failing trade yields a `FAILED:`/`BLOCKED:`
outcome rather than killing the run, and a shared **curve cache** means the SOFR curve is
bootstrapped once and reused across all the trades in a worker chunk.

---

## Where this lives in the code

| Component | Path |
|---|---|
| Orchestration / provenance stamping | [`src/pricer/engine.py`](../../../src/pricer/engine.py) |
| Result envelope | [`src/pricer/ql/common/pricing_result.py`](../../../src/pricer/ql/common/pricing_result.py) |
| Base pricer (sets evaluation date, propagates warnings) | [`src/pricer/ql/common/base_pricer.py`](../../../src/pricer/ql/common/base_pricer.py) |
| Market-data snapshot / fingerprint | [`src/pricer/loader/snapshot.py`](../../../src/pricer/loader/snapshot.py) |
| As-of / staleness selection | [`src/pricer/loader/staleness.py`](../../../src/pricer/loader/staleness.py) |
| Provenance | [`src/common/provenance.py`](../../../src/common/provenance.py) |
| Config (data-quality, risk, per-asset params) | [`configs/pricer/pricer_config.json`](../../../configs/pricer/pricer_config.json) |
