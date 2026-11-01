# scenario-decision-impact — scenario losses as a sizing constraint, with and without each source

## Requirement

Nothing shows whether the scenario set changes a decision (gap G13,
`docs/design/scenario_set_and_ablation_design.md` §7.6). The sizing module (`src/pricer/sizing`)
sizes each risk-appetite profile to a daily VaR budget; the stress scenarios only report losses
afterwards. This change adds the scenario losses as a **stress-loss constraint** next to the VaR budget
and reports how position sizes, the binding constraint and limit breaches change with and without each
scenario set, and with and without each enrichment source.

Decided 2026-10-17: the constraint uses the **adverse** tier, and each profile's stress-loss budget is
**6 × its daily VaR budget** (balanced 6%, core 12%, aggressive 15%, risky 30% of the $1bn capital
base).

When this change is done:

1. **Worst stress loss.** `worst_stress(multipliers, scenario_pnl, names)` returns
   `(name, loss)` for the scenario whose book loss `−Σᵢ mᵢ pₛᵢ` is largest:
   - `scenario_pnl` is a scenarios × trades matrix of per-trade P&L of the **base** book (the unsized
     book the multipliers apply to); `multipliers` the per-trade size multipliers; `names` one per row;
   - `loss` is positive for a loss and may be negative when every scenario gains; ties go to the first
     scenario in `names`;
   - an empty scenario matrix, mismatched shapes or a non-finite value raises `ValueError`.
2. **Stress scale.** `stress_scale(multipliers, scenario_pnl, budget)` returns the factor `s ∈ (0, 1]`
   that the whole multiplier vector is scaled by so the worst stress loss is at most `budget`:
   - `s = budget / worst_loss` when `worst_loss > budget`, else `1` (the constraint only ever shrinks
     a book; a book that gains in every scenario is unconstrained);
   - a non-positive `budget`, or the refusals of `worst_stress`, raises `ValueError`.
3. **Wiring** (covered by the implementer in `tests/unit/`).
   - `configs/pricer/sizing.json` gains `stress_constraint: {"tier": "adverse", "var_multiple": 6.0}`;
     a profile's stress budget is `var_multiple × var_budget_pct × capital_base`.
   - `solve_profile` takes an optional stress input (the scenario P&L matrix in model trade order, the
     scenario names, the budget). After the VaR scaling and caps it applies `stress_scale` to the
     multipliers (a homogeneous rescale, like the VaR step); when it binds, `binding_constraint` is
     `"stress_loss"`. `SizingResult` records the stress budget, the worst scenario and its loss. Without
     a stress input, `solve_profile` is unchanged (byte-identical results).
   - A new runner, `python -m src.scenariogen.decision_impact_runner`, reads scenario vectors from a
     stress report (default: the latest) and/or a layer-ablation report, and revalues the **base** book
     under each scenario vector (full revaluation) to build the scenario P&L matrices:
     - from a stress report, one scenario set: its results at the constraint tier;
     - from an ablation report, one scenario set per layer subset (all layers, each "all but one
       layer", and no layers), so the effect of dropping each source is measured;
     - for each set and each profile it sizes with and without the stress constraint and reports the
       per-trade multiplier changes, the binding constraint, the worst scenario, the book's VaR, ES,
       expected return and gross leverage, and the scenarios over the budget before and after;
     - for an ablation report, a "without each source" table compares each all-but-one set with the
       all-layers set (multiplier and binding changes caused by removing that source);
     - writes `data/pricer/scenario/<date>/decision_<run_id>.json`, provenance-stamped (stress or
       ablation run id, snapshot and covariance ids, tier, budgets).
   - The runner is analysis only: it does not write book variants or change `portfolio*.json`, so golden
     PVs are untouched; `sizing_runner` keeps its current behaviour.

Out of scope: adopting the constraint in the published book variants (a later, explicit decision with a
golden re-baseline), and live dual-RAG / ablation runs themselves (the runner consumes their reports).

## Acceptance criteria

- **C1**: `worst_stress` returns the scenario with the largest book loss `−Σ mᵢ pₛᵢ` and that loss
  (rel 1e-12), negative when every scenario gains, the first on a tie, and raises on the refusals.
  Properties: scaling the multipliers by `c > 0` scales the loss by `c` and keeps the scenario;
  permuting the scenario rows (with their names) changes neither; appending a scenario that loses less
  changes nothing. Oracle: hand calculation on small matrices; properties.
- **C2**: `stress_scale` is `budget / worst_loss` when the worst loss exceeds the budget and `1`
  otherwise (rel 1e-12), and raises for a non-positive budget. Properties: after scaling, the worst loss
  is at most the budget (rel 1e-12) and equals it when the scale is below 1; the result is in `(0, 1]`;
  scaling the budget and the P&L matrix by the same `c > 0` leaves it unchanged; a larger budget never
  gives a smaller scale. Oracle: hand calculation; properties.

## Interface

New module `src/pricer/sizing/stress_constraint.py` (numpy only):

- `src.pricer.sizing.stress_constraint.worst_stress(multipliers: np.ndarray, scenario_pnl: np.ndarray,
  names: Sequence[str]) -> tuple[str, float]`
- `src.pricer.sizing.stress_constraint.stress_scale(multipliers: np.ndarray, scenario_pnl: np.ndarray,
  budget: float) -> float`

`multipliers` is 1-D (trades); `scenario_pnl` is 2-D (scenarios × trades); `budget` is in dollars.

## Notes

- Spec tests use small synthetic matrices; no `data/`, services or QuantLib.
- P&L is linear in position size for a fixed scenario (each trade's P&L scales with its notional), so a
  homogeneous rescale of the multipliers rescales every scenario loss by the same factor.
- `src/pricer` must not import `src/scenariogen` (`tests/unit/test_import_boundaries.py`): the pure
  constraint lives in `src/pricer/sizing`, and the runner that reads scenariogen reports lives in
  `src/scenariogen`.
