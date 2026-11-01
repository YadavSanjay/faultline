# plausibility-unpriced — a zero market-implied probability is "unpriced", not "probability 0"

## Requirement

`src.scenariogen.plausibility.PlausibilityChecker` grades each equity move of a materialized scenario
against the option market (`market_implied`, design B4): it asks the option-signal provider
`market.implied_prob(ticker, move)` for the market-implied probability of a move at least that extreme
and records a **flag-only** finding when that probability is below `market_implied.min_prob` (0.01).

The provider returns `None` when the move falls outside the fitted strike grid, and the check then
records nothing. Inside the grid, the fitted risk-neutral distribution often carries no mass at all in
the far wing, so the provider returns exactly `0.0`. The checker records that as a `market_implied`
finding with `observed = 0.0`, which reads as "the market says this move is impossible". It means only
that the option chain does not resolve a move that large. In the 2026-10-06 scenario run several flags
show `observed = 0` (SMH and XLK at the mild tier).

Decision: a probability of zero is reported as **unpriced** — the move lies beyond what the option chain
prices — as its own finding, not as a probability.

When this change is done:

1. When `market.implied_prob(ticker, move)` returns a value `<= 0.0` for an `eq:<TKR>` move, the
   checker records one finding with `check = "market_unpriced"`, `factor` = the vector key,
   `observed` = the scenario move (decimal, as in the vector), `limit = 0.0`, `severity = "flag"`, and
   no `market_implied` finding for that factor.
2. A probability `> 0.0` and `< min_prob` records the `market_implied` finding exactly as today
   (`observed` = the probability, `limit = min_prob`, `severity = "flag"`).
3. A probability `>= min_prob`, a `None`, a zero move, a non-equity factor, a disabled check or no
   provider record nothing, as today.
4. `market_unpriced` is flag-only like `market_implied`: it sets the status to `"flag"` unless it is
   already `"fail"`, and never makes a scenario non-promotable. The bound and envelope checks are
   unchanged.
5. The `PlausibilityFinding.check` comment lists `"market_implied"` and `"market_unpriced"` alongside
   `"bound"` and `"envelope"` (implementer).

Out of scope: the `min_prob` threshold, the envelope check, the option-signal provider, and how the UI
counts plausible tiers.

## Acceptance criteria

- **C1**: With a provider that returns `0.0` for a move, `check()` returns exactly one finding for that
  factor, `("eq:<TKR>", "market_unpriced", move, 0.0, "flag")`, status `"flag"`, `promotable` true.
  Oracle: the requirement.
- **C2**: With a provider that returns a probability `p` with `0 < p < min_prob`, the finding is
  `("eq:<TKR>", "market_implied", p, min_prob, "flag")`, unchanged from today; for `p >= min_prob` or
  `None` there is no market finding. Property: for any `p` in `[0, 1]` and any non-zero move, the
  market finding is `market_unpriced` exactly when `p <= 0.0`, `market_implied` exactly when
  `0 < p < min_prob`, and absent otherwise; there is never more than one market finding per factor.
  Oracle: the requirement (property check over `p`).
- **C3**: Status and promotability: a scenario whose only finding is `market_unpriced` is `"flag"` and
  promotable; adding a bound breach (`"fail"`) on another factor makes it `"fail"` and not promotable
  regardless of order. Oracle: the existing status rule (fail > flag > pass).

## Interface

No new signatures. The change is in the behaviour of the existing
`PlausibilityChecker._market_implied_check` (reached through `PlausibilityChecker.check`).

## Notes

- Oracles come from the requirement; no `data/`, network or current date.
- The option-signal provider is an I/O boundary (it reads option chains from disk): tests pass a small
  fake object with an `implied_prob(ticker, move)` method as `market=`. The checker also needs a
  `FactorCovariance`; a one-factor synthetic one (e.g. `eq:NVDA`, variance `1e-4`) with the envelope
  check set to `"ignore"` keeps the envelope out of the way.
- `<= 0.0` rather than `== 0.0`: the provider's upside branch clips with `max(0.0, …)`, and comparing a
  computed float for equality is avoided by house rule.
