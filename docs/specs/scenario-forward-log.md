# scenario-forward-log — record each run's predicted moves and score them against what happens

## Requirement

The layer ablation (`scenario-layer-ablation`) measures how much each enrichment layer moves a
scenario, not whether that move is right. A stress scenario has no observed answer at the time it is
made. The only evidence of value is to keep what each run predicted and compare it, later, with what
markets actually did. The news and option-chain layers can't be tested any other way, because their
history is too short for a point-in-time replay (design doc
`docs/design/scenario_set_and_ablation_design.md` §7.4–§7.5).

**A stress scenario is not a forecast of the next week.** In a calm week, a −30% scenario and the
realised +1% move have nothing to say to each other. So the log scores **shape**, and reports stress
windows separately:

- **Cross-sectional rank:** do the names the scenario hits hardest move most in the same direction?
- **Direction:** among factors that really moved, does the scenario have the sign right?
- **Cosine:** after scaling each factor by its own horizon volatility, how close is the scenario's
  shape to the realised move?
- **Stress window:** whether the realised window was itself a stress for the book's market factor.

Value of a layer is then a **paired** comparison: the same run, scenario and tier, scored with and
without that layer.

When this change is done:

1. **Realised moves.** `realised_moves(panel, as_of, horizon_days, factors)` returns each factor's
   realised move over the `horizon_days` panel rows strictly after `as_of`:
   - `panel` is daily factor moves indexed by date, in the canonical units of `factor_keys.py`.
     Equity (`eq:`) and FX (`fx:`) columns are simple daily returns; every other column is a daily
     change.
   - Equity and FX moves compound: `Π(1 + r) − 1`. Every other factor sums its daily changes.
   - It returns `None` while fewer than `horizon_days` rows exist after `as_of` (not yet matured).
   - A factor not in `panel`, or with any missing value inside the window, is left out.
2. **Prediction scoring.** `score_prediction(predicted, realised, scale, *, min_abs_z=1.0)` works
   over the factors present in all three mappings with `scale > 0`, using `z = move / scale`, and
   returns a dict with:
   - `n_factors`: the number of those factors;
   - `cosine`: the cosine similarity of the predicted and realised z-vectors; `None` with fewer than
     two factors or a zero vector;
   - `rank_corr_equity`: the Spearman rank correlation (average ranks for ties) of predicted vs
     realised z over the `eq:` factors (z, not the raw move, so the per-factor rescaling
     invariance in C2 holds); `None` with fewer than three, or a constant vector;
   - `direction_hit_rate`: among factors with `|z_realised| ≥ min_abs_z`, the share whose predicted
     sign equals the realised sign (a predicted move of exactly 0 counts as a miss); `None` when no
     factor qualifies;
   - `n_direction`: the number of factors that qualified.
3. **Stress windows.** `stress_window(realised, scale, *, market_factor="eq:XLK", threshold_z=-2.0)`
   returns `True` when `realised[market_factor] / scale[market_factor] ≤ threshold_z`, `False`
   otherwise, and `None` when the factor or its scale is missing, or the scale isn't positive.
4. **Layer value.** `layer_value(records, layer, metric)` pairs score records that share `run_id`,
   `scenario` and `severity` and whose `layers` sets differ by exactly `layer`: one has it, the
   other doesn't. For each pair where both values of `metric` are not `None`, the delta is
   with − without. It returns `{"layer", "metric", "n_pairs", "mean_delta", "n_improved",
   "n_worse"}`. `n_pairs` counts the pairs that gave a delta (a pair skipped for a `None` value is
   not counted). `mean_delta` is `None` when there are no pairs; `n_improved` and `n_worse` count
   deltas `> 0` and `< 0`.
5. **The log and its wiring** (covered by the implementer in `tests/unit/`):
   - **Store.** An append-only prediction store under the storage façade
     (`data/pricer/store/scenario_predictions/`), keyed `(run_id, scenario, severity, layers)`, so a
     re-run upserts. Each record holds: `as_of` (the valuation date), `horizon_days`, `layers`
     (sorted list; `[]` = authored), the materialized factor vector, the per-factor `scale`
     (`√(Σ_ii · horizon_days)` from the run's covariance, recorded at prediction time),
     `scenario_version`, `cov_id`, `snapshot_id`, `composition_rule`, the live triggers that fired
     (GPR, throughput, 8-K items, crowding squeeze) and `pit_clean`.
   - **Writers.** The stress runner logs every materialized scenario tier: layers = all layers for
     `--dual-rag`, `[]` for the authored-only path. The ablation runner logs every priced layer
     subset. Both have `pit_clean = true`.
   - **Backfill.** `python -m src.scenariogen.forward_score --backfill` imports the existing
     `scenario_*.json` and `ablation_*.json` reports under `data/pricer/scenario/<date>/` with
     `pit_clean = false`, because their service inputs can't be shown to be point-in-time. Scale is
     recomputed from the current covariance and marked as such.
   - **Scoring runner.** `python -m src.scenariogen.forward_score [--as-of D]` scores every matured,
     unscored prediction from the full factor history (each factor's own series, not the 250-day
     inner-joined panel), stores the scores keyed like the predictions, and writes
     `data/pricer/scenario/forward_scores_<date>.json`. The report covers, per scenario and
     variant, the mean of each metric over all windows and over stress windows; per layer,
     `layer_value` for each metric, split the same way; the number of distinct `as_of` windows
     (overlapping windows aren't independent evidence); and how many records are `pit_clean`.

Out of scope: scheduling the runs; BFF/UI surfacing; any change to how scenarios are built;
point-in-time historical replay.

## Acceptance criteria

- **C1**: `realised_moves` returns, for each kept factor, `Π(1 + r) − 1` over the window for `eq:`
  and `fx:` factors and `Σ Δ` for the others (rel 1e-12). The window is the first `horizon_days`
  rows strictly after `as_of`, so a row dated `as_of` is excluded. It returns `None` when fewer rows
  exist; leaves out factors missing from the panel or with a missing value in the window; and
  returns only the requested `factors`. Oracle: Requirement 1 by hand on a small synthetic panel.
- **C2**: `score_prediction` returns `n_factors`, `cosine`, `rank_corr_equity`,
  `direction_hit_rate` and `n_direction` as defined in Requirement 2:
  - values within rel 1e-12 (Spearman `abs=1e-12`) of hand calculations;
  - each `None` case above;
  - scaling every predicted move by the same positive constant leaves all five unchanged;
  - multiplying one factor's predicted move, realised move and scale by the same positive constant
    leaves all five unchanged.

  Oracle: Requirement 2 by hand; invariances as properties.
- **C3**: `stress_window` returns `True` at and below the threshold z, `False` above it, and `None`
  for a missing factor, a missing scale, or a scale ≤ 0. Oracle: Requirement 3 by hand.
- **C4**: `layer_value` pairs only records with the same `run_id`, `scenario` and `severity` whose
  `layers` differ by exactly `layer`. It returns the hand-computed `n_pairs`, `mean_delta`,
  `n_improved` and `n_worse`; skips pairs where either metric value is `None`; and returns
  `mean_delta = None` with no pairs. Swapping the with and without values of every pair negates
  `mean_delta` and swaps `n_improved` and `n_worse`. Oracle: Requirement 4 by hand; the swap as a
  property.

## Interface

New module `src/scenariogen/forward_log.py`:

- `src.scenariogen.forward_log.realised_moves(panel: pd.DataFrame, as_of: date, horizon_days: int,
  factors: Sequence[str]) -> dict[str, float] | None`
- `src.scenariogen.forward_log.score_prediction(predicted: Mapping[str, float],
  realised: Mapping[str, float], scale: Mapping[str, float], *, min_abs_z: float = 1.0)
  -> dict[str, Any]`
- `src.scenariogen.forward_log.stress_window(realised: Mapping[str, float],
  scale: Mapping[str, float], *, market_factor: str = "eq:XLK", threshold_z: float = -2.0)
  -> bool | None`
- `src.scenariogen.forward_log.layer_value(records: Sequence[Mapping[str, Any]], layer: str,
  metric: str) -> dict[str, Any]`

`panel` has a `DatetimeIndex` (or date-like index) sorted ascending, with canonical factor keys as
columns. Each `records` item carries `run_id`, `scenario`, `severity`, `layers` (a list of layer
ids) and the metric keys. The implementer defines the store, the record builder, the runners and
any constants.

## Notes

- Spec-test inputs are synthetic panels, vectors and records built in the tests; no `data/`,
  services or QuantLib.
- Evidence builds up slowly: stress windows are rare, and daily runs with 10-day horizons overlap,
  which is why the report counts distinct windows. The scores are meant to accumulate over months.
- `pit_clean = false` backfilled records are reported apart from live ones.
