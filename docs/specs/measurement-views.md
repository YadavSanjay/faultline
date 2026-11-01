# measurement-views — BFF views and a UI page for coverage, decision impact and the event replay

## Requirement

Follow-up to step 15f (`docs/specs/ablation-waterfall-view.md`). The 2026-10-19 measurement run wrote
five reports. The UI shows the stress report (catalog, detail, Signal Health) and the ablation report
(Layer Ablation), but nothing reads the other three:

- **coverage** — `data/pricer/scenario/<date>/coverage_<stress_run_id>.json` (`coverage_runner`):
  `coverage`, `n_directions`, `directions[]` (`index`, `variance_share`, `spanned_share`, `uncovered`,
  `top_loadings`), `diversity` (`participation_ratio`, `target`, `in_target`, `losses`),
  `merge_pairs[]` (`a`, `b`, `cosine`), `missing[]`;
- **decision impact** — `data/pricer/scenario/<date>/decision_<run_id>.json`
  (`decision_impact_runner`): `sets[]` (each `kind` `"stress"` or `"ablation"`, `label`, `profiles`:
  book → `stress_budget`, `unconstrained` / `constrained` (`binding`, `worst_scenario`, `worst_loss`,
  `over_budget`), `change` (`binding.from/to`, `n_trades_changed`, `multipliers`)) and
  `without_each_source` (source → book → `binding.from/to`, `multipliers` trade →
  `{from, to, ratio}`);
- **event replay** — `data/scenariogen/event_replay/event_replay_<run_id>.json`
  (`event_replay_runner`): `episodes[]` (`name`, `as_of`, `horizon_days`, `available`, `dropped[]`
  (`scenario`, `reason`), `versions.authored|enriched` (`nearest`, `scores`: scenario → `{cosine, ...}`),
  `pit` (`regime_asof`, `dropped_issuer_factors`)) and `limitations`.

This change adds pure view functions to `src/scenariogen/report_views.py` (QuantLib-free, like the
existing views) and a **Measurement** tab in the Scenarios section.

When this change is done:

1. **Coverage view.** `report_views.coverage_view(report)` returns
   `{"coverage", "n_directions", "uncovered_share", "directions", "diversity", "losses",
   "merge_pairs", "missing"}`:
   - `coverage`, `n_directions`, `merge_pairs`, `missing` copied from the report (`[]` when a list key
     is absent);
   - `directions`: the report's directions in `index` order, each
     `{"index", "variance_share", "spanned_share", "uncovered", "top_loadings"}`;
   - `uncovered_share`: the sum of `variance_share` over the directions with `uncovered` true
     (`0.0` when none);
   - `diversity`: `{"participation_ratio", "target", "in_target"}` from the report;
   - `losses`: `[{"scenario", "loss"}]` from `diversity.losses`, ordered by `loss` descending, then by
     scenario name.

   It raises `ValueError` when `coverage` or `directions` is missing, or two directions share an
   `index`. The input is not mutated.

2. **Decision view.** `report_views.decision_view(report)` returns `{"tier", "books", "without"}`:
   - `tier` copied from the report;
   - `books`: from the single set with `kind == "stress"`, one row per book in the order
     `balanced, core, aggressive, risky`, then any other book by name:
     `{"book", "stress_budget", "worst_scenario", "worst_loss", "binding_from", "binding_to",
     "n_over_budget", "n_trades_changed"}` where `worst_scenario` / `worst_loss` /
     `n_over_budget = len(over_budget)` come from `unconstrained`, and `binding_from` / `binding_to`
     from `change.binding`; `[]` when there is no stress set;
   - `without`: one row per source in `without_each_source`, sources in the report's order, each
     `{"source", "books": [{"book", "binding_from", "binding_to", "binding_changed", "size_ratio"}]}`
     with books in the same order as above, `binding_changed = binding_from != binding_to`, and
     `size_ratio` the arithmetic mean of `multipliers[*].ratio` (`1.0` when there are no multipliers);
     `[]` when `without_each_source` is absent.

   It raises `ValueError` for more than one set with `kind == "stress"`. The input is not mutated.

3. **Replay view.** `report_views.replay_view(report)` returns `{"limitations", "episodes"}`:
   - `limitations` copied (`[]` when absent);
   - `episodes` in report order, each `{"name", "as_of", "horizon_days", "authored", "enriched",
     "gain", "available", "dropped", "regime_asof", "dropped_issuer_factors"}`:
     - `authored` / `enriched`: `{"nearest", "cosine"}` where `cosine` is
       `scores[nearest].cosine` (`None` when `nearest` is `None` or has no score);
     - `gain`: `enriched.cosine − authored.cosine`, or `None` when either is `None`;
     - `available` copied; `dropped`: the dropped scenario names in report order;
     - `regime_asof` / `dropped_issuer_factors` from `pit` (`None` / `[]` when absent).

   The input is not mutated.

4. **Wiring** (covered by the implementer in `tests/bff/` and vitest).
   - BFF `GET /api/scenarios/measurement?valuation_date=` (declared before `/api/scenarios/{name}`):
     `{"valuation_date", "available_dates", "coverage", "decision", "replay"}` where `coverage` and
     `decision` are the views of the newest `coverage_*.json` / `decision_*.json` for the date (by
     `generated_at`, via `select_report`), and `replay` the view of the newest event-replay report
     overall (it is not per valuation date); each block is `{"available": false, "reason"}` when its
     report is missing or its view raises. Each available block also carries its report's `run_id`
     (or `stress_run_id`) and `generated_at`.
   - UI: a **Measurement** tab in the Scenarios layout (`/scenarios/measurement`) with three sections
     — coverage (directions table with uncovered flags, diversity against its target, merge
     candidates, scenario losses), decision impact (books table; "without each source" table with the
     binding change and size ratio), event replay (episodes with authored/enriched closest scenario,
     cosine and gain, point-in-time notes). Follows the design system (semantic tokens, both themes);
     each section says when its report is not available.

Out of scope: the stability report (15c is tabled), changing any runner or report format.

## Acceptance criteria

- **C1**: `coverage_view` returns the hand-built view for a synthetic report with 3 directions given
  out of `index` order (one uncovered), 3 scenario losses with a tie, and one merge pair; `[]` for
  absent `merge_pairs` / `missing`; `uncovered_share = 0.0` with no uncovered direction; raises for a
  missing `coverage`, missing `directions` and a duplicate `index`. Properties: `uncovered_share` equals
  the sum over uncovered directions and lies in `[0, sum of all variance_share]`; `losses` is sorted
  descending and is a permutation of the report's losses; the input is unchanged. Oracle:
  Requirement 1 by hand.
- **C2**: `decision_view` returns the hand-built view for a synthetic report with a stress set over the
  four standard books plus one extra book, an ablation set (ignored), and two sources in
  `without_each_source` (one with an empty `multipliers`); `books == []` with no stress set; raises for
  two stress sets. Properties: `binding_changed` is true exactly when from ≠ to; `size_ratio` is the
  mean of the ratios; book order is the standard order then by name; the input is unchanged. Oracle:
  Requirement 2 by hand.
- **C3**: `replay_view` returns the hand-built view for a synthetic report with two episodes (one with
  both versions scored, one whose enriched `nearest` is `None`), `gain` positive, negative and `None`
  cases, absent `pit` and absent `limitations`. Properties: `gain` is `None` exactly when a cosine is
  `None`, else equals the difference; episode order follows the report; the input is unchanged.
  Oracle: Requirement 3 by hand.

## Interface

```python
# src/scenariogen/report_views.py (new functions)
def coverage_view(report: Mapping[str, Any]) -> dict[str, Any]
def decision_view(report: Mapping[str, Any]) -> dict[str, Any]
def replay_view(report: Mapping[str, Any]) -> dict[str, Any]
```

The module stays QuantLib-free (`tests/bff/test_bff_quantlib_free.py`).

## Notes

- Oracles are hand calculations on synthetic report JSON (dyadic numbers). No `data/`, network or
  current date.
- Field names follow the 2026-10-19 reports (`coverage_a4d2d16e…`, `decision_c82250ff…`,
  `event_replay_092b1612…`).
- Clarifications (from writing the tests):
  - `diversity.losses` is a mapping scenario -> loss (as `coverage_runner` writes it); view directions
    carry exactly the five named keys and `diversity` exactly its three (extra report keys such as
    `variance` or `book_exposure` are left out); "missing" means an absent key.
  - In `decision_view`, `worst_scenario`, `worst_loss` and `n_over_budget` come from `unconstrained`
    and the binding pair from `change.binding`, never from `constrained`; each `without` row orders
    its own books by the standard order, then by name.
  - A multiplier `ratio` of `None` (the runner writes it when a base multiplier is 0) is left out of
    the mean; when every ratio is `None` or there are none, `size_ratio` is `1.0` (not pinned by a test).
  - In `replay_view`, a `nearest` absent from `scores`, or a score whose `cosine` is `None`, gives a
    `None` cosine; a missing `versions.authored` / `enriched` block is out of scope.
  - Error messages are only required to be non-empty.
