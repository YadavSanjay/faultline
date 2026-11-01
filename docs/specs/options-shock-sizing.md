# options-shock-sizing — option-implied tiers for the largest-name event, and an option-tail check

## Requirement

Step 10 of `docs/design/scenario_set_and_ablation_design.md` §9 (gap G7: options don't shape
shocks; §5.3 "Market intelligence (options)"). Today:

- `OptionSignalProvider` reads each name's **latest** option snapshot, whatever the run's as-of
  date. A run as of 2026-10-06 reads 2026-10-16 snapshots: look-ahead.
- The option-implied checks only flag; no scenario is sized by the option market.
- `OptionSignalProvider.severity_multiplier` is never called.

This change sizes scenario #10 (`keyman_nvda`, the largest-name event) from the point-in-time
option-implied distribution of its lead name. It uses the option tail as a severity check on every
scenario, falls back to a CBOE single-name vol index where no chain snapshot qualifies, and removes
`severity_multiplier`. The new logic lives in a new module `src/scenariogen/options/sizing.py`, as
pure functions.

For #10 only, the option-implied tiers replace the history anchors of §8. This is a deliberate
exception: an event scenario is sized by the distribution the market prices for that event, not by
the name's worst move since the proxy history begins. The design's other settled parameters are
unchanged.

When this change is done:

1. **Point-in-time snapshot.** `select_snapshot(dates, as_of, *, max_staleness_days)` returns the
   latest date in `dates` (ISO strings; only the first 10 characters are read) that is on or before
   `as_of` and at most `max_staleness_days` days before it, as a 10-character string, or `None`
   when there is none. Dates after `as_of` never count. `max_staleness_days < 0` raises
   `ValueError`; 0 means same-day only.

2. **Risk-neutral quantile.** `rnd_quantile(strikes, cdf, spot, q)` returns the return
   `K_q / spot − 1`, where `K_q` is the strike at which the risk-neutral CDF equals `q`, by linear
   interpolation between the two grid points that bracket `q` (`strikes` ascending, `cdf`
   non-decreasing, same length ≥ 2). It returns `None` when `q < cdf[0]` or `q > cdf[-1]` (outside
   the fitted grid: no extrapolation); `q` equal to an end value is inside. With `i` the first index
   where `cdf[i] ≥ q`: if `cdf[i] = q`, `K_q = strikes[i]` (so where several consecutive grid points
   share the value `q`, the first such strike is used); otherwise `K_q` interpolates between
   `i − 1` and `i`. It raises `ValueError` for `q` outside `(0, 1)`, a
   non-positive `spot`, mismatched lengths, fewer than 2 points, strikes not strictly increasing, or
   a CDF that decreases.

3. **Lognormal fallback.** For an annualised implied vol `vol > 0` and a horizon `t_years > 0`,
   with `ln(1 + R) ~ N(−vol²·t/2, vol²·t)` (risk-neutral, zero rate):
   - `lognormal_quantile(q, vol, t_years)` returns `exp(−vol²t/2 + vol·√t·Φ⁻¹(q)) − 1`;
   - `lognormal_tail_prob(move, vol, t_years)` returns `P(R ≤ move)` for `move < 0` and
     `P(R ≥ move)` for `move > 0`.

   Both raise `ValueError` for `vol ≤ 0` or `t_years ≤ 0` (or non-finite); `lognormal_quantile` for
   `q` outside `(0, 1)`; `lognormal_tail_prob` for `move == 0` or `move ≤ −1`. `Φ` is the standard
   normal CDF.

4. **Option tiers.** `option_tiers(mild_move, adverse_move, *, severe_factor, bound)` returns
   `{"mild": mild_move, "adverse": adverse_move, "severe": s}` with `s = adverse_move ·
   severe_factor`, its magnitude capped at `bound` (a positive magnitude, keeping the sign; `None` =
   no cap). It raises `ValueError` when `mild_move` and `adverse_move` are 0 or have different signs,
   when `|adverse_move| < |mild_move|`, for `severe_factor < 1`, or for `bound ≤ 0` (equality in
   the first two is allowed). Every `ValueError` in this spec carries a non-empty message.

5. **Option-tail check.** `exceeds_option_tail(tier_move, option_move)` is true when the option
   market's move is more extreme than the tier's move in the same direction: both non-zero, same
   sign, and `|option_move| > |tier_move|`. It is false otherwise, including when `option_move` is
   `None`.

6. **Wiring** (covered by the implementer in `tests/unit/`).
   - **Config** `configs/scenariogen/options_sizing.json` (pydantic- or dataclass-validated, an
     unknown key raises `ConfigError`): `max_staleness_days` 30, `mild_q` 0.05, `adverse_q` 0.01,
     `check_q` 0.01, `severe_factor` from `severity.json`, and `vol_index` mapping a ticker to its
     CBOE index file: AAPL→VXAPL, AMZN→VXAZN, GOOGL→VXGOG, QQQ→VXN, SPX→VIX. Other names have no
     fallback (an index vol understates a single name's tail).
   - **PIT signals.** `OptionSignalProvider.load` takes the run's `as_of` and, per name, the
     snapshot chosen by `select_snapshot` (signals and RND curve from that same date). A name with
     no qualifying snapshot and a `vol_index` entry uses the index close on or before `as_of`
     (`vol = close / 100`, `t_years = 30 / 365`) through Requirement 3. The plausibility
     market-implied check uses this PIT provider.
   - **#10 tiers.** `keyman_nvda` in `scenarios.json` gets `"severity": {"lead": "eq:NVDA",
     "direction": "down", "source": "options"}` and `horizon_days` 21 (the one-month RND horizon in
     trading days), with its narrative reworded as the largest-name event (earnings or guidance
     miss, 8-K 5.02/4.02 as triggers). For a scenario with `"source": "options"`, the tier anchors
     of its lead are `option_tiers(Q(mild_q), Q(adverse_q))`, with `Q` from `rnd_quantile` on the
     PIT RND curve (or `lognormal_quantile` on the index fallback), `severe_factor` and the hard
     plausibility bound. When neither is available, the history anchors apply and the result carries
     the tier flag `option_anchor_unavailable`. The severity block records the anchor source:
     `options` with the snapshot date, the RND horizon and the two quantiles, or `vol_index` with the
     index, or `history`.
   - **Concentration.** The #10 result reports the book's single-name equity stock notional per
     ticker, largest first, so a change of the largest name is visible.
   - **Option-tail check.** For every scenario and tier whose `horizon_days` is at most 21, the
     stress report adds the tier flag `below_option_tail` when `exceeds_option_tail(adverse lead
     move, Q(check_q))` holds for the adverse tier's lead (an `eq:` factor with PIT option data).
     It never changes a shock.
   - **Removed.** `OptionSignalProvider.severity_multiplier` and the `severity_weight`,
     `severity_min`, `severity_max` keys of `plausibility.json` are removed.

Out of scope: `credit_implied` (moves to step 11 with scenario #8, its only user), per-name equity
vol for option underliers (`eqvol` stays one factor), and golden PVs (pricing is untouched).

## Acceptance criteria

- **C1**: `select_snapshot` returns the hand-picked date for a set mixing dates before, on and after
  `as_of`, honours the staleness limit at its boundary (inclusive), returns `None` when nothing
  qualifies, and raises `ValueError` for a negative limit. Properties: adding dates after `as_of`
  never changes the result; the result does not depend on the order of `dates`. Oracle:
  Requirement 1 by hand.
- **C2**: `rnd_quantile` returns the hand-interpolated return (rel 1e-12) on a small synthetic grid,
  including a `q` that falls exactly on a grid point and a flat CDF segment, returns `None` outside
  the grid, and raises `ValueError` for each invalid input of Requirement 2. Properties: the result
  is non-decreasing in `q`; scaling every strike and the spot by the same positive factor leaves it
  unchanged. Oracle: Requirement 2 by hand.
- **C3**: `lognormal_quantile` and `lognormal_tail_prob` match the closed forms of Requirement 3
  (rel 1e-9) at hand-chosen points and raise `ValueError` for each invalid input. Properties: they
  are inverse to each other on the downside (`lognormal_tail_prob(lognormal_quantile(q)) = q` for
  `q < P(R ≤ 0)`, within 1e-9); the downside quantile becomes more extreme as `vol` grows. Oracle:
  Requirement 3; `Φ` and `Φ⁻¹` from `math.erf` / `statistics.NormalDist`.
- **C4**: `option_tiers` returns the hand-computed tiers with and without the cap (rel 1e-12), for
  downside and upside moves, and raises `ValueError` for each invalid input of Requirement 4.
  Properties: `|mild| ≤ |adverse| ≤ |severe|` when uncapped; `|severe| ≤ bound` when capped; all
  three share a sign. Oracle: Requirement 4 by hand.
- **C5**: `exceeds_option_tail` returns the hand-checked verdict for more extreme, equal, less
  extreme, opposite-sign, zero and `None` cases. Property: it is never true for both
  `(a, b)` and `(b, a)`. Oracle: Requirement 5 by hand.

## Interface

```python
# src/scenariogen/options/sizing.py (new)
def select_snapshot(dates: Sequence[str], as_of: date, *, max_staleness_days: int) -> str | None
def rnd_quantile(strikes: Sequence[float], cdf: Sequence[float], spot: float, q: float) -> float | None
def lognormal_quantile(q: float, vol: float, t_years: float) -> float
def lognormal_tail_prob(move: float, vol: float, t_years: float) -> float
def option_tiers(mild_move: float, adverse_move: float, *, severe_factor: float,
                 bound: float | None) -> dict[str, float]
def exceeds_option_tail(tier_move: float, option_move: float | None) -> bool
```

## Notes

- Oracles are hand calculations on synthetic grids (for example strikes 80, 90, 100, 110, 120 with
  CDF 0.0, 0.02, 0.4, 0.9, 1.0 and spot 100). No `data/`, network or current date.
- On 2026-10-06 the NVDA one-month RND gives 5% / 1% / 0.25% quantiles of about −14% / −22% / −29%,
  against history anchors of −33% / −44% / −66% at 5 days. That gap is why #10 changes size; it is
  context, not a test oracle.
