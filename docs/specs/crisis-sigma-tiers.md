# crisis-sigma-tiers — crisis Σ for the severe tier, a shrinkage cap, and bounded replay tiers

## Requirement

Scenarios propagate their drivers through Σ (conditional covariance). Today Σ is a 250-day EWMA
(λ = 0.94) shrunk toward its diagonal by a factor the COR3M implied-correlation regime sets between
0.4 and 0.65. In the calm regime of the 2026-10-06 run it was 0.62, which cut NVDA's beta to XLK to
0.46, against about 1.1 in raw history and 1.7 in the 2022 sell-off (gap G5,
`docs/design/scenario_set_and_ablation_design.md` §3.2–§3.3; settled: "the severe tier uses crisis
Σ, so calm-regime shrinkage can't damp it").

Separately, the 2022 `ai_deflation` replay's adverse and severe tiers are held out by the gate:
anchoring scales the whole observed episode ×1.84 / ×2.76 to reach the dot-com XLK record, which takes
1-year yields past the 400bp hard bound. Crisis Σ doesn't help there, because a replay observes every
factor. A replay's tier scale is therefore capped at the largest scale that keeps every factor inside
its hard bound (decided 2026-10-17): its higher tiers become "the episode as large as stays
plausible", not a rescaling past what any factor has done.

When this change is done:

1. **Crisis Σ.** `crisis_covariance(panel, windows, *, shrinkage, min_days)` returns
   `(covariance, coverage)`:
   - the rows of `panel` (an aligned daily factor-move frame indexed by date) dated inside any of the
     `windows` (`(start, end)` ISO pairs, both ends inclusive; a row inside two windows counts once);
   - the sample covariance of those rows (ddof 1) and their mean, shrunk toward the diagonal by
     `shrinkage`: variances kept, every off-diagonal entry multiplied by `1 − shrinkage`;
   - returned as a `FactorCovariance` over the panel's columns, with `n_obs` the number of rows used;
   - `coverage` maps each window, keyed `"<start>..<end>"`, to its number of panel rows (0 for a
     window the history doesn't reach);
   - fewer than `min_days` rows in total, or `shrinkage` outside `[0, 1]`, raises `ValueError`.
2. **Capped regime shrinkage.** `regime_shrinkage(percentile, *, shrink_systemic,
   shrink_idiosyncratic, max_shrinkage)` returns
   `min(shrink_idiosyncratic − percentile × (shrink_idiosyncratic − shrink_systemic), max_shrinkage)`,
   the regime's linear map (as `ImpliedCorrelationRegime.resolve` computes it today) capped at
   `max_shrinkage`. A `percentile` outside `[0, 1]` raises `ValueError`.
3. **Bound scale cap.** `bound_scale_cap(moves, bounds, overlays=None)` returns the largest `k ≥ 0`
   such that, for every factor `f` with a bound, `|k · moves[f] + overlays.get(f, 0)| ≤ bounds[f]`:
   - `moves` is the part of a scenario vector that scales with the tier (drivers and their
     propagation); `overlays` the part that doesn't; `bounds` factor → hard bound (positive, canonical
     units); factors missing from `bounds` are unconstrained;
   - with no binding constraint (every bounded factor has `moves[f] = 0`) the result is `math.inf`;
   - an overlay that alone exceeds its factor's bound, or a non-positive bound, raises `ValueError`.
4. **Wiring** (covered by the implementer in `tests/unit/`).
   - `configs/scenariogen/covariance.json`:
     - `implied_corr_regime.max_shrinkage = 0.3` (production Σ; NVDA's beta to XLK 0.85 at
       2026-10-06, and every authored scenario still passes the gate at mild and adverse);
     - a `severe` block, `{"windows": <crisis_windows>, "shrinkage": 0.2, "min_days": 60}`: 42 factors
       on 142 crisis days make the sample matrix rank-deficient, so a light shrinkage keeps it
       invertible for the propagation.
   - **Severe tier.** `ScenarioCatalog` builds the crisis Σ once from the full aligned panel
     (`FactorHistory.aligned_panel` over all available days, `include_style` as configured). Every
     severe tier materializes with it, on every path that materializes a tier: the static path, the
     anchored path, the dual-RAG company-leg hook and the ablation. The severe tier's plausibility
     envelope uses the same Σ. The materialized provenance records the crisis Σ's `cov_id`, its
     `n_obs` and the window coverage. A crisis Σ that can't be built (below `min_days`) stops the
     severe tier for every scenario (fail-loud), not a fallback to the calm Σ.
   - **Replay tiers.** For a replay (a scenario with a `replay` block), the anchored scale is
     `min(k_anchor, bound_scale_cap(...))`, with the moves and overlays of the tier at scale 1 and the
     bounds from `plausibility.hard_bound`. The severity provenance records `k_anchor`, `k_cap` and
     the factor that binds. Authored scenarios keep the current anchoring.
   - `docs/design/scenario_set_and_ablation_design.md` §8 records the new settled parameters
     (production shrinkage cap 0.3; severe Σ = crisis windows, shrinkage 0.2, at least 60 days; replay
     tier scale capped at the hard bounds).

Out of scope: extending the aligned history before 2022 (so the 2018-Q4 and COVID-2020 windows stay
empty until it is), changing the hard bounds, and the mild and adverse tiers' Σ (production, capped).

## Acceptance criteria

- **C1**: `crisis_covariance` uses exactly the rows inside the windows (both ends inclusive, a row in
  two windows once) and returns their ddof-1 sample covariance and mean with the off-diagonals scaled by
  `1 − shrinkage` (rel 1e-12); `coverage` counts each window's rows, 0 for an empty one; too few rows or
  a shrinkage outside `[0, 1]` raises. Properties: the diagonal doesn't depend on `shrinkage`; the
  result doesn't depend on the order of `windows` or on rows outside them; at `shrinkage = 1` it is
  diagonal. Oracle: numpy `cov`/`mean` on the rows the test selects by hand; properties.
- **C2**: `regime_shrinkage` equals the linear map by hand, capped at `max_shrinkage` (rel 1e-12), and
  raises for a percentile outside `[0, 1]`. Properties: it is non-increasing in `percentile` when
  `shrink_systemic ≤ shrink_idiosyncratic`; it never exceeds `max_shrinkage`; a cap at or above
  `shrink_idiosyncratic` leaves the map unchanged. Oracle: hand calculation; properties.
- **C3**: `bound_scale_cap` returns the hand value `min_f (b_f − sign(m_f) · o_f) / |m_f|` over factors
  with a bound and a non-zero move (rel 1e-12), `inf` with none, and raises for an overlay beyond its
  bound or a non-positive bound. Properties: at the returned `k` every bounded factor is within its
  bound (abs 1e-12) and at least one sits on it; scaling every move by `c > 0` scales the cap by
  `1 / c` (no overlays); adding an unbounded factor changes nothing. Oracle: hand calculation;
  properties.

## Interface

- `src.scenariogen.covariance.crisis_covariance(panel: pd.DataFrame, windows: Sequence[tuple[str, str]],
  *, shrinkage: float, min_days: int) -> tuple[FactorCovariance, dict[str, int]]`
- `src.scenariogen.implied_corr.regime_shrinkage(percentile: float, *, shrink_systemic: float,
  shrink_idiosyncratic: float, max_shrinkage: float) -> float`
- `src.scenariogen.severity.bound_scale_cap(moves: Mapping[str, float], bounds: Mapping[str, float],
  overlays: Mapping[str, float] | None = None) -> float`

## Notes

- Spec tests use synthetic panels and dicts; no `data/`, services or QuantLib.
- The 2026-10-06 numbers in this spec (betas, the 142 crisis days, the ×1.84 / ×2.76 scales) come from
  a read-only probe of the on-disk history; they motivate the change and are not test values.
