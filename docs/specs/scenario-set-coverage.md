# scenario-set-coverage — does the scenario set cover the ways this book loses money?

## Requirement

Nothing reports whether the scenario set covers the book's main loss patterns, or whether its
scenarios are distinct once the book's dominant direction is removed (gap G3,
`docs/design/scenario_set_and_ablation_design.md` §7.3). This change adds the set-level measures and
a report.

All measures work on per-trade P&L vectors over the same ordered list of trades:
- the **history matrix** `H` (days × trades): each trade's daily full-revaluation P&L over the VaR
  lookback;
- the **scenario matrix** `S` (scenarios × trades): each scenario's per-trade P&L at one tier
  (mild by default).

When this change is done:

1. **Principal loss directions.** `principal_directions(history, *, variance_share=0.9,
   max_components=10)` returns `(directions, variances)`:
   - the principal components of `history` after subtracting each column's mean, as unit-length
     rows of `directions` (`k × trades`), in decreasing order of variance;
   - `variances[i]` is the sample variance (ddof 1) of the history's projection on direction `i`;
   - `k` is the smallest count whose variances sum to at least `variance_share` of the total
     variance, capped at `max_components`;
   - each direction is oriented so its component sum (the book's exposure to it) is ≥ 0;
   - fewer than two rows, a non-finite value, `variance_share` outside `(0, 1]`, or zero total
     variance raises `ValueError`.
2. **Spanned share.** `spanned_share(directions, scenarios)` returns, for each direction `v`, the
   share of `v` that lies in the span of the scenario rows: `‖P v‖²`, with `P` the orthogonal
   projection onto that row space.
   - The row space is taken from the scenario rows' singular vectors whose singular value exceeds
     `1e-12 × the largest one`. An all-zero or empty scenario matrix spans nothing (share 0).
   - Mismatched trade counts or a non-finite value raises `ValueError`.
3. **Set coverage.** `set_coverage(variances, shares)` returns `Σ λᵢ sᵢ / Σ λᵢ`, the
   variance-weighted share of the book's loss directions the set spans. Mismatched lengths, a
   negative variance or a zero variance sum raises `ValueError`.
4. **Direction removal.** `remove_direction(rows, direction)` returns each row minus its projection
   on `direction`: `r − (r·v̂) v̂`, with `v̂ = direction / ‖direction‖`. A zero direction raises
   `ValueError`.
5. **Loss-weighted participation ratio.** `participation_ratio(rows, losses)`:
   - keeps the rows whose loss is above zero and whose norm is above zero;
   - scales each kept row to unit length, then by `√(Lᵢ / Σ L)` over the kept rows;
   - returns `(Σ μ)² / Σ μ²`, with `μ` the squared singular values of the scaled matrix;
   - returns 0 when no row is kept;
   - a negative loss counts as no loss (a gain); mismatched lengths or a non-finite value raises
     `ValueError`.
6. **Merge candidates.** `merge_pairs(names, rows, *, threshold=0.85)` returns every pair `(a, b,
   cos)` of distinct rows (`a` before `b` in `names`) whose cosine similarity is strictly above
   `threshold`, in decreasing order of cosine. A row with zero norm is in no pair. Mismatched
   lengths raise `ValueError`.
7. **Wiring** (covered by the implementer in `tests/unit/`).
   - The full-revaluation VaR engine can return the per-trade history matrix (columns sum to
     today's book P&L vector).
   - `python -m src.scenariogen.coverage_runner [--report <stress json>] [--tier mild]` reads a
     stress report (default: the latest for the valuation date), builds `S` from its per-trade
     P&L and `H` from the full-revaluation history over the same trades, and writes
     `data/pricer/scenario/<date>/coverage_<run_id>.json`:
     - `coverage` and, per direction, its variance share, spanned share and five largest trade
       loadings; a direction with a spanned share below `uncovered_below` is flagged uncovered;
     - `diversity`: the participation ratio of the scenario rows with the book's first direction
       removed, weighted by each scenario's loss (`−total P&L`), against the target band;
     - the beta-removed cosine matrix and the merge pairs;
     - scenarios without a result at the tier (held by the gate or stopped), listed and left out;
     - provenance: the stress run id, its snapshot and covariance ids, the lookback and the tier.
   - Parameters live in `configs/scenariogen/coverage.json`, loaded through a typed model (a
     mistyped key raises): `tier` mild, `variance_share` 0.9, `max_components` 10,
     `uncovered_below` 0.5, `merge_threshold` 0.85, `diversity_target` [3, 4].

Out of scope: changing the scenario set from the results (the merge rule is applied by hand), the
BFF/UI, and sensitivities (the report covers the default set).

## Acceptance criteria

- **C1**: `principal_directions` recovers the components of a history built from orthogonal
  directions with known sample variances: directions to `abs 1e-9` (after the stated orientation),
  variances to `rel 1e-9`; `k` follows the variance-share rule and the cap; each direction's
  component sum is ≥ 0; the refusals raise `ValueError`. Properties: adding a constant to any column
  changes nothing; scaling the history by `c ≠ 0` scales the variances by `c²` and leaves the
  directions; the directions are orthonormal. Oracle: a history generated in the test from chosen
  orthonormal vectors and score series (variances computed by hand with numpy); properties.
- **C2**: `spanned_share` gives `(s·v)² / (‖s‖²‖v‖²)` for a single scenario row (rel 1e-12 for a
  unit `v`), 1 for a direction inside the span and 0 for one orthogonal to it (abs 1e-12), and 0
  for an empty or all-zero scenario matrix; mismatches raise. Properties: the share is in `[0, 1]`;
  it doesn't change when a row is scaled by a non-zero number or a linear combination of the rows is
  appended; it never falls when a row is added. Oracle: hand calculation; properties.
- **C3**: `set_coverage` equals the weighted mean by hand (rel 1e-12) and raises on the refusals.
  Property: it lies between the smallest and largest share; scaling every variance by `c > 0`
  leaves it unchanged. Oracle: hand calculation; properties.
- **C4**: `remove_direction` gives the hand residual (abs 1e-12) and raises for a zero direction.
  Properties: every residual is orthogonal to the direction (abs 1e-9 relative to the row norm);
  removing twice equals removing once; scaling the direction by `c ≠ 0` changes nothing. Oracle:
  hand calculation; properties.
- **C5**: `participation_ratio` is `k` for `k` mutually orthogonal rows with equal losses, 1 for
  rows that are positive multiples of one another, `1 / Σ wᵢ²` for orthogonal rows with loss shares
  `wᵢ` (rel 1e-12), and 0 when no row has a loss; gains and zero rows are left out; refusals raise.
  Properties: it lies in `[1, number of kept rows]` when a row is kept; it doesn't change when a
  row is scaled by a positive number, when every loss is scaled by `c > 0`, or when the columns are
  rotated by an orthogonal matrix. Oracle: hand calculation; properties.
- **C6**: `merge_pairs` returns exactly the pairs strictly above the threshold, ordered by cosine,
  with the hand cosine (rel 1e-12); a pair exactly at the threshold is not returned; zero rows are in
  no pair; a mismatch raises. Properties: scaling a row by a positive number doesn't change the
  result; negating a row removes its pairs (for a positive threshold). Oracle: hand calculation;
  properties.

## Interface

New module `src/scenariogen/coverage.py` (numpy only):

- `src.scenariogen.coverage.principal_directions(history: np.ndarray, *, variance_share: float = 0.9,
  max_components: int = 10) -> tuple[np.ndarray, np.ndarray]`
- `src.scenariogen.coverage.spanned_share(directions: np.ndarray, scenarios: np.ndarray) -> np.ndarray`
- `src.scenariogen.coverage.set_coverage(variances: np.ndarray, shares: np.ndarray) -> float`
- `src.scenariogen.coverage.remove_direction(rows: np.ndarray, direction: np.ndarray) -> np.ndarray`
- `src.scenariogen.coverage.participation_ratio(rows: np.ndarray, losses: np.ndarray) -> float`
- `src.scenariogen.coverage.merge_pairs(names: Sequence[str], rows: np.ndarray, *,
  threshold: float = 0.85) -> list[tuple[str, str, float]]`

Matrices are 2-D float arrays with one row per day or scenario and one column per trade; vectors
are 1-D. The implementer defines the runner, the config model and the VaR engine change.

## Notes

- Spec tests use synthetic matrices only; no `data/`, services or QuantLib.
- PCA sign is arbitrary; the orientation rule (component sum ≥ 0) makes directions comparable
  across runs. A direction whose component sum is exactly 0 keeps the sign the SVD gives it, so
  tests avoid that case for orientation checks.
- Coverage uses the span of the scenarios, so a scenario that gains along a direction covers it as
  well as one that loses: the question is whether the pattern is represented, not its sign.
- The diversity target (3–4) and merge threshold (0.85) are the design's settled parameters (§8);
  `uncovered_below` 0.5 means a direction counts as covered when more than half of it lies in the
  set's span.
