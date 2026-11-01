# faultline — oracle catalogue for spec tests

Where a spec test's expected value may come from. Every test names its oracle in an
`# oracle: <source>` comment. Never use a value obtained by running the code under test, and never
use QuantLib as the oracle for a QuantLib-based calculation's numerics (it can only check wiring).
Design: tdd-spec `design/plan.md` §8.3–§8.4.

## Strongest first

| Kind | faultline examples | Proves | Typical tolerance |
|---|---|---|---|
| **Closed form in the test** (numpy/scipy, not QuantLib) | Black-Scholes price/Greeks; Bachelier (normal) option price; put-call parity; discount factor `exp(-z·t)`; simple/compounded forward from two DFs `(DF1/DF2 − 1)/τ`; par swap rate `(1 − DF_n)/Σ τ_k·DF_k`; par bond yield = coupon; FX forward by covered interest parity `S·DF_usd/DF_for` | The numerics, independently | `rel=1e-10` exact algebra; `abs=1e-8` (0.0001bp) through a solver or bootstrap |
| **Published worked example / standard grid** | ISDA CDS Standard Model test grid; worked examples in a cited textbook or paper (cite edition + page) | Numerics and conventions against an outside reference | As stated by the source, else its printed precision |
| **Hand calculation shown in the test** | Notional-weighted median of five prints; day-count fractions for given dates; VaR / ES on a ten-point return series | That case, to the shown precision | Printed precision of the hand calc |
| **QuantLib test-suite values** | Reference values from QuantLib's own `test-suite/` for a pricer we wrap | Wiring only (day counts, calendars, settlement, argument order) | The test-suite's own tolerance |
| **Greek consistency** | Analytic or engine Greek vs bump-and-reprice of the same pricer | Greek code agrees with the pricer | State bump size and tolerance together, e.g. 1bp bump, `rel=1e-4` |
| **Invariants** (property / metamorphic) | Price falls as yield rises; positive convexity (option-free bond); call price monotone in spot, bounded by spot; DF in (0, 1] and decreasing; P&L explain closes to zero; a curve reprices its own inputs; shifting every input quote by +x shifts the fitted par rate by +x | Necessary conditions, not levels | Exact sign/ordering; residual `abs` stated |
| **Market reconciliation** against a dated snapshot | Model vs a public-domain snapshot (Treasury par yields) | Realism within quote noise | Quote-noise sized (bp); **only** when no stronger oracle exists, and accepted at lock time |

Each criterion needs an oracle from the first three rows where one exists; otherwise the spec says
why, and the weaker oracle is accepted explicitly at lock time.

## Data rules for spec tests

- **Committed inputs only.** No `data/`, no network, no `date.today()` / `datetime.now()`. "Today" is
  the profile's `valuation_date`.
- **Public-domain or synthetic only.** U.S. Treasury yield data (a U.S. government work) is fine;
  the shared snapshot is in `tests/spec/_shared/snapshots/`. **DTCC/CFTC SDR, FINRA TRACE, ICE,
  Pensford, CME, CBOE and DTCC credit data are not committed** — build synthetic records with the
  same schema inside the spec's own fixtures, generated from a known curve so the expected answer
  is closed form.
- **Generated-from-truth fixtures.** For curve and quote-extraction specs, generate inputs from a
  known closed-form curve (e.g. flat or piecewise-flat forward), then assert the code recovers it.
  The generator lives in the test, uses numpy only, and is simple enough to read as the oracle.
- **Rates are decimals** (0.0475, not 4.75) unless a field is named `_pct` or `_bp`; say which in
  every fixture.

## Conventions the oracles must state

Day count (Act/360 SOFR, Act/365F curve time, 30/360 corporate, ActAct(Bond) UST), calendar,
settlement lag, payment frequency and compounding. A closed form that ignores one of these doesn't
prove it — write a separate wiring test for it.
