# scenario-severity-anchor — tie each scenario's severity to history; enrichment shapes it within a band

## Requirement

Today a tier is the authored drivers × a fixed multiplier (1.0 / 1.5 / 2.0), and the enrichment
layers compound on top. In run `8da52756` the **mild** tier moved NVDA −57% to −68% over 10 days,
against a worst observed 10-day move of −32%, and three scenarios hit the −95% floor at **adverse**.
The design (`docs/design/scenario_set_and_ablation_design.md` §3.3) anchors every tier to history
and lets the enrichment sources move a scenario only within a band around the anchor. This change
implements that rule, using the long history from `long-history-ingest`.

When this change is done:

1. **Horizon moves.** `horizon_moves(moves, factor, horizon_days)` turns a daily-move series in
   canonical units into overlapping `horizon_days`-move windows, one per end date from the
   `horizon_days`-th move on. Equity and FX (`eq:`, `fx:`) moves compound, `Π(1 + r) − 1` over the
   window; every other factor sums its moves. `horizon_days ≤ 0`, or fewer moves than
   `horizon_days`, raises `ValueError`.
2. **Tier anchors.** `tier_anchors(moves, factor, horizon_days, direction, *, mild_return_years=10.0,
   severe_factor=1.5, bound=None)` returns `{"mild", "adverse", "severe"}` in the factor's units,
   from the `n` horizon moves of Requirement 1:
   - `direction` is `"down"` (the adverse tail is the lower one) or `"up"` (the upper one); anything
     else raises `ValueError`;
   - **mild** is a once-in-`mild_return_years` move. With `p = horizon_days / (252 ·
     mild_return_years)` and `k = max(1, ceil(n · p))`, it is the `k`-th smallest horizon move for
     `"down"` and the `k`-th largest for `"up"`;
   - **adverse** is the record: the smallest horizon move for `"down"`, the largest for `"up"`;
   - **severe** is `adverse × severe_factor`. If `bound` is given (a positive magnitude), its absolute
     value is capped at `bound`, keeping its sign;
   - `mild_return_years ≤ 0`, `severe_factor < 1` or `bound ≤ 0` raises `ValueError`.
3. **Company-leg cap.** `cap_leg_inputs(seed_legs, calibrated_legs, news_mult, crowding_mult, *,
   cap=1.5)` bounds each company leg's compound multiplier. For leg `f`, with `a = calibrated / seed`
   (1 when the seed is 0), `n = news_mult.get(f, 1)` and `c = crowding_mult.get(f, 1)`, the compound
   is `m = a · n · c`:
   - when `1/cap ≤ m ≤ cap`, the leg is unchanged;
   - otherwise each of the three factors `x ∈ {a, n, c}` becomes `x^λ`, with
     `λ = ln(cap) / ln(m)` when `m > cap` and `λ = ln(1/cap) / ln(m)` when `m < 1/cap`. The compound
     then sits exactly on the bound, and each factor keeps its share of `ln m`;
   - it returns `(calibrated_legs, news_mult, crowding_mult)`, where the capped calibrated move is
     `seed × a^λ`. The keys of each output equal the keys of the matching input: a multiplier
     absent from an input stays absent, since `1^λ = 1`;
   - `cap < 1`, or `seed_legs` and `calibrated_legs` with different keys, raises `ValueError`.
     Inputs are not mutated.
4. **Company-specific seed.** `residual_seeds(seed_legs, calibrated_legs, authored_drivers,
   systematic)` returns `(seed_legs, calibrated_legs)`. A leg whose factor is also an authored driver
   has its seed set from the authored *total* move, so its company-specific part is
   `seed' = authored_drivers[f] − systematic[f]`. Here `systematic[f]` is the factor's co-move from
   the other drivers (0 when absent). Its calibrated move keeps the fact ratio:
   `calibrated' = seed' × calibrated / seed` (or `seed'` when the seed is 0). Legs not among the
   authored drivers are unchanged. With all multipliers at 1, this makes the final move
   `systematic + seed' = authored` instead of `authored + systematic`. Mismatched leg keys raise
   `ValueError`.
5. **Anchor scale.** `anchor_scale(lead_composed, lead_authored, anchor, *, band=0.25)` returns the
   factor `k` by which a composed scenario is rescaled so that its lead factor's move becomes
   `anchor × clamp(r, 1 − band, 1 + band)`:
   - `r = lead_composed / lead_authored` is the enrichment's relative effect on the lead factor;
   - `r = 1` when `lead_authored` is 0;
   - `k = anchor × clamp(r, 1 − band, 1 + band) / lead_composed`;
   - `lead_composed = 0`, a `band` outside `[0, 1)` or a non-finite input raises `ValueError`.
6. **Wiring** (covered by the implementer in `tests/unit/`).
   - **Config.** Each anchored scenario in `scenarios.json` gets
     `"severity": {"lead": <factor>, "direction": "up"|"down"}`, with its horizon from the
     scenario's `horizon_days`. `configs/scenariogen/severity.json` holds `mild_return_years` (10),
     `severe_factor` (1.5), `band` (0.25), `leg_cap` (1.5), `state_floor` (1.0) and
     `max_proxy_beta` (1.5). The bound per factor class comes from `plausibility.json`.
   - **Anchors** come from `long_history.load_long_history`. A factor spliced with a proxy `β`
     above `max_proxy_beta` uses its own moves only, and is labelled.
   - **Tier pipeline.** For an anchored scenario, both the stress runner (dual-RAG and static) and
     the ablation runner build each tier as follows:
     1. floor the state multiplier at `state_floor`;
     2. apply `residual_seeds`, with `systematic` from Σ's conditional mean given the systematic
        drivers at scale 1, then `cap_leg_inputs`;
     3. compose at tier scale 1;
     4. materialize the composed definition and the authored-only definition (no layers), and read
        their lead moves;
     5. rescale the composed definition's drivers and overlays by `anchor_scale` against the
        tier's anchor, and materialize the result.

     Scenarios without a `severity` block keep today's behaviour. The ablation runner's
     composition-rule version becomes `dualrag-v2`.
   - **Severity report.** Each result carries a `severity` block: lead, direction, the three
     anchors and where they came from (start date, proxy, β), `r`, `k`, the raw and floored state
     multiplier, the legs the cap touched, and, for every factor with long history, its move as a
     fraction of its own mild and adverse anchors.
   - Choose the lead factors as follows, until the scenario set is replaced:

     | Scenario | Lead | Direction |
     |---|---|---|
     | `taiwan_geopolitical` | `eq:TSM` | down |
     | `ai_bust` | `eq:XLK` | down |
     | `rate_capital` | `ir:DGS10` | up |
     | `keyman_nvda` | `eq:NVDA` | down |
     | `funding_basis_squeeze` | `credit` | up |
     | `real_rate_repricing` | `ir:DGS10` | up |

     `momentum_crash` and `semis_dispersion` have no long-history lead and stay unanchored.

Out of scope: the new scenario set and multi-month horizons (the next change), crisis Σ for the severe
tier, and any change to golden PVs (pricing is untouched; scenario results and limits change).

## Acceptance criteria

- **C1**: `horizon_moves` returns the compounded (`eq:`/`fx:`) or summed windows of Requirement 1, at
  the right end dates (rel 1e-12). It raises `ValueError` for `horizon_days ≤ 0` and for too few
  moves. A window of length 1 returns the moves themselves. Oracle: Requirement 1 by hand; length 1
  as a property.
- **C2**: `tier_anchors` returns the hand-computed `mild` (the `k`-th order statistic with
  `k = max(1, ceil(n·p))`), `adverse` (the record) and `severe` (`adverse × severe_factor`, capped at
  `bound`), for `"down"` and `"up"` (rel 1e-12). It raises `ValueError` for a bad direction,
  `mild_return_years`, `severe_factor` or `bound`. Properties:
  - `|mild| ≤ |adverse| ≤ |severe|` whenever the mild move is on the adverse side (so the record is
    too) and no `bound` is given;
  - mirroring the moves (negating every daily move of a non-compounding factor) swaps `"down"` and
    `"up"` anchors with their signs flipped.

  Oracle: Requirement 2 by hand; ordering and mirroring as properties.
- **C3**: `cap_leg_inputs` leaves legs with `1/cap ≤ m ≤ cap` unchanged, and for others returns
  `x^λ` per factor and `seed × a^λ`, as in Requirement 3 (rel 1e-12). Properties:
  - after capping, every compound lies in `[1/cap, cap]`, within 1e-12;
  - each factor's share of `ln m` is preserved;
  - keys and inputs are unchanged;
  - `cap < 1` or mismatched keys raise `ValueError`.

  Oracle: Requirement 3 by hand; the properties above.
- **C4**: `residual_seeds` returns `authored − systematic` seeds and fact-ratio-preserving calibrated
  moves for legs among the authored drivers, and leaves others unchanged (rel 1e-12). Property:
  `systematic[f] + seed'[f] = authored[f]` for every such leg. Mismatched keys raise `ValueError`.
  Oracle: Requirement 4 by hand; the identity as a property.
- **C5**: `anchor_scale` returns `k` with `k · lead_composed = anchor · clamp(r, 1 − band, 1 + band)`
  (rel 1e-12), including:
  - `r` inside the band, above it and below it;
  - `r` negative (clamped to `1 − band`);
  - `lead_authored = 0` (so `r = 1`);
  - the `ValueError` cases.

  Property: for `r` inside the band, the rescaled lead equals `anchor · r`. Oracle: Requirement 5 by
  hand; the identity as a property.

## Interface

New module `src/scenariogen/severity.py`:

- `src.scenariogen.severity.horizon_moves(moves: pd.Series, factor: str, horizon_days: int)
  -> pd.Series`
- `src.scenariogen.severity.tier_anchors(moves: pd.Series, factor: str, horizon_days: int,
  direction: str, *, mild_return_years: float = 10.0, severe_factor: float = 1.5,
  bound: float | None = None) -> dict[str, float]`
- `src.scenariogen.severity.cap_leg_inputs(seed_legs: dict[str, float],
  calibrated_legs: dict[str, float], news_mult: dict[str, float], crowding_mult: dict[str, float],
  *, cap: float = 1.5) -> tuple[dict[str, float], dict[str, float], dict[str, float]]`
- `src.scenariogen.severity.residual_seeds(seed_legs: dict[str, float],
  calibrated_legs: dict[str, float], authored_drivers: Mapping[str, float],
  systematic: Mapping[str, float]) -> tuple[dict[str, float], dict[str, float]]`
- `src.scenariogen.severity.anchor_scale(lead_composed: float, lead_authored: float, anchor: float,
  *, band: float = 0.25) -> float`

`moves` is a daily-move series sorted by date, in the canonical units of `factor_keys.py`. The
implementer defines the config loader, the pipeline wiring and the report.

## Notes

- Spec tests use synthetic series and dicts only; no `data/`, services or QuantLib.
- `p = horizon_days / (252 · years)` treats the overlapping windows as one per trading day, and asks
  for the move exceeded about once per `years` of non-overlapping horizons. Spec tests keep
  `horizon_days < 252 · mild_return_years` (`p < 1`, so `k ≤ n`); `p ≥ 1` is left unspecified.
- The ablation's layer contributions change meaning under this rule: a layer can move the lead
  factor by at most ±`band`, while it can still reshape every other factor. That is why the
  composition-rule version changes.
