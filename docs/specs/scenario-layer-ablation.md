# scenario-layer-ablation — measure what each enrichment layer adds to a named scenario

## Requirement

A dual-RAG named scenario is built in layers. Today only the finished result is reported, so nobody
can see how much of a scenario's P&L, or of its cross-section across trades, each layer caused. The
2026-10-06 run (`8da52756`) shows why this matters: `ai_bust` authors NVDA at −35% over 10 days,
and the **mild** tier applies −68.5% (the worst 10-day NVDA move since 2018 is −32%). This comes
from an XBRL fact multiplier (×1.23), a news multiplier (×1.22), a crowding multiplier (×1.30), a
throughput multiplier (×0.85) and the additive systematic leg. Which of these earns its place is
the question this change answers.

The layers, in their default ladder order:

| Layer id | What it contributes | Where it is applied today |
|---|---|---|
| (base) `authored` | `scenarios.json` drivers and overlays, scaled by the severity tier | always |
| `macrorag` | MacroRAG synthesis replaces the macro driver legs | `DualRagEnrichmentProvider.definition_for` |
| `xbrlrag_narrative` | XbrlRag (ARag) 10-K/Q risk-factor company legs, at their **seed** move | same |
| `xbrlrag_facts` | XbrlRag XBRL fact calibration of those legs (seed → calibrated move) | `arag/calibrate.py` via the ARag provider |
| `news` | 8-K/GDELT multiplier on each company leg | `dualrag._idio_scale` |
| `crowding` | FINRA days-to-cover multiplier on each company leg | `dualrag._idio_scale` |
| `state` | GPR (F8) × throughput (F9) severity multiplier on the whole scenario | `runner.run_stress` (`geo_mult`) |

When this change is done:

1. **One pure composition rule.** `compose_definition` builds a scenario's drivers, overlays and
   severity scale from already-resolved inputs and a set of enabled layers, following exactly the
   rules `DualRagEnrichmentProvider.definition_for` and `runner._materialize` apply today:
   - `severity_scale = tier_scale × (state_mult if "state" enabled else 1)`.
   - Drivers start as `macro_drivers` when `"macrorag"` is enabled and `macro_drivers` is not
     `None`; otherwise as `authored_drivers`. (`macro_drivers` is the provider's already-merged
     authored + macro set.)
   - Overlays start as a copy of `authored_overlays`; authored overlays are **not** scaled.
   - When `"xbrlrag_narrative"` is enabled and there are company legs, each leg `f` has the move
     `idio(f) = calibrated(f)` if `"xbrlrag_facts"` is enabled, else `seed(f)`, and the multiplier
     `m(f) = (news_mult.get(f, 1) if "news" enabled else 1) × (crowding_mult.get(f, 1) if
     "crowding" enabled else 1)`. Then:
     - if any driver remains after removing the company-leg factors (a systematic anchor), the
       drivers become those remaining drivers, and for each leg
       `overlays[f] = overlays.get(f, 0) + idio(f) × severity_scale × m(f)`;
     - otherwise (an idiosyncratic-only scenario such as `keyman_nvda`) the drivers become
       `{f: idio(f) × m(f)}`, unscaled, because the generator scales drivers itself.
   - `"xbrlrag_facts"`, `"news"` and `"crowding"` have no effect unless `"xbrlrag_narrative"` is
     enabled (they act on company legs only).
   - `DualRagEnrichmentProvider.definition_for` and the runner must use this function (or be shown
     equal to it), so that the full layer set reproduces production. The implementer covers that
     equivalence in `tests/unit/`.
2. **Ladder attribution.** Given a function that prices a layer subset to per-trade P&L, and a
   ladder order, report one step per prefix (base first, then each layer added in order) with:
   total P&L, the marginal P&L against the previous step, the **gross reallocation**
   `Σ_t |pnl_k(t) − pnl_{k−1}(t)|` over the union of trades (missing = 0), and the Spearman rank
   correlation of per-trade P&L with the previous step, over the same union of trades (a trade
   missing from a step counts as P&L 0). The Spearman figure uses average ranks for ties, and is
   `None` for the base step or when either vector is constant. The base step has no previous step,
   so its `gross_reallocation` is `None` too. `order` holds layer ids only: the base is implicit, so
   `"authored"` (like any id outside Requirement 1's table, or a repeated id) raises `ValueError`.
   A layer can move little
   in total yet reshuffle a lot of risk between names; gross reallocation and rank correlation show
   that.
3. **Shapley attribution.** The ladder depends on order (the layers multiply), so also report the
   exact Shapley value of each layer for total P&L: the average of its marginal contribution over
   all orderings, with the base (empty set) as the reference.
4. **A runner.** `python -m src.scenariogen.ablation_runner [--scenario X] [--severity T]
   [--valuation-date D] [--book B]` (default tier: `mild`) resolves MacroRAG, XbrlRag, news, crowding, GPR and throughput
   **once** per scenario (the same fail-loud preflight and gates as `--dual-rag`). It then prices
   every distinct layer subset by full revaluation (no capital, no LLM narrative), and writes
   `data/pricer/scenario/<date>/ablation_<run_id>.json` holding, per scenario × tier: the ladder,
   the Shapley values, per-subset per-trade P&L, and the run provenance (`RunProvenance`, market and
   factor `snapshot_id`, `cov_id`, MacroRAG `world_view_id`, and the resolutions used). It also
   holds, per layer subset, the **floor hits**: the factors whose materialized move sits at
   `factor_keys.RETURN_FLOOR`. A floored factor can't respond to further layers, so the report
   must show where contributions are understated. It also holds a `composition_rule` version
   string, so results from different composition rules aren't compared by mistake. Subsets that
   compose to identical definitions may be priced once. The implementer covers the runner in
   `tests/unit/` with fakes at the service and pricing boundaries.

The ablation measures each layer's **influence** on the scenario, not its **value**: a layer that
enlarges a loss isn't better for that reason. Evidence of value comes from scoring predictions
against realised moves (a separate change).

Out of scope: point-in-time historical event replay (a separate spec, which first needs MacroRAG to
reject chunks dated after `as_of`); BFF/UI surfacing; changing any layer's calibration; capital
(ES-under-shock) per subset.

## Acceptance criteria

- **C1**: `compose_definition` returns `(drivers, overlays, severity_scale)` equal (rel 1e-12) to
  Requirement 1's rule applied by hand. Cover at least: the base set; `macrorag` only; the full set
  for an anchored scenario; the full set for an idiosyncratic-only scenario; `news`/`crowding`
  without `xbrlrag_narrative` (equal to the base); `xbrlrag_narrative` without `xbrlrag_facts` (seed
  moves); an authored overlay on the same factor as a company leg (added, not replaced); a leg
  missing from `news_mult` (multiplier 1); and `macro_drivers=None` with `macrorag` enabled (equal
  to the base). Inputs are not mutated. Oracle: Requirement 1 computed by hand in the test.
- **C2**: `compose_definition` raises `ValueError` for: a layer id not in Requirement 1's table; a
  result with no drivers; `seed_legs` and `calibrated_legs` with different keys; a non-finite or
  non-positive `tier_scale` or `state_mult`. Oracle: Requirement 1 and the fail-loud convention in
  `CLAUDE.md`.
- **C3**: `ladder_attribution` returns one step per prefix, with `layer` (`"authored"` for the base,
  then each layer id), `layers` (the enabled set, sorted list), `total_pnl`, `marginal_pnl`,
  `gross_reallocation` and `rank_corr_vs_prev`, each as defined in Requirement 2 (rel 1e-12; Spearman
  `abs=1e-12` against `scipy.stats.spearmanr` computed in the test or a hand-ranked example). The
  marginals telescope: `Σ marginal_pnl = total_pnl(last)` (base marginal = base total). The base
  step has `gross_reallocation` and `rank_corr_vs_prev` both `None`. Spearman is taken over the union
  of trade ids of the two steps, a missing trade counting as 0. An order with an unknown or repeated
  layer, or containing `"authored"`, raises `ValueError`. Oracle: Requirement 2 by hand; telescoping
  as a property.
- **C4**: `shapley_attribution` returns the exact Shapley values: the three-player glove game
  (`v(S) = 1` iff `S` holds player `a` and at least one of `b`, `c`) gives `a = 2/3`,
  `b = c = 1/6`; an additive game gives each player its own contribution. Efficiency
  (`Σ φ = v(all) − v(∅)`), symmetry (interchangeable players get equal values) and the null-player
  property (a player that never changes `v` gets 0) hold for random games (`abs=1e-9`). Repeated
  player ids raise `ValueError`. Oracle: Shapley (1953) and the glove game as given in standard
  cooperative-game texts; the axioms as properties.

## Interface

New module `src/scenariogen/ablation.py`:

- `src.scenariogen.ablation.compose_definition(*, layers: frozenset[str],
  authored_drivers: dict[str, float], authored_overlays: dict[str, float],
  macro_drivers: dict[str, float] | None, seed_legs: dict[str, float],
  calibrated_legs: dict[str, float], news_mult: dict[str, float],
  crowding_mult: dict[str, float], tier_scale: float, state_mult: float)
  -> tuple[dict[str, float], dict[str, float], float]`
- `src.scenariogen.ablation.ladder_attribution(pnl_for: Callable[[frozenset[str]],
  Mapping[str, float]], order: Sequence[str]) -> list[dict[str, Any]]`
- `src.scenariogen.ablation.shapley_attribution(value_for: Callable[[frozenset[str]], float],
  players: Sequence[str]) -> dict[str, float]`

All keys are canonical factor keys (`eq:NVDA`, `ir:DGS10`, …) in decimal native units, as
`factor_keys.py` defines them. `layers` and `order` hold the layer ids from Requirement 1's table
(the base is the empty set and has no id). The implementer defines any constants (for example the
default ladder order) and the runner module `src/scenariogen/ablation_runner.py`.

## Notes

- All spec-test inputs are synthetic dicts and callables built in the tests; no `data/`, no
  services, no QuantLib. A pricing callable in a test is a fake at the pricing boundary (linear in
  factor moves), not a mock of project code.
- The composition rule is read from `src/scenariogen/dualrag.py` (`definition_for`,
  `_idio_scale`) and `src/scenariogen/runner.py` (`run_stress`: `geo_mult`, `_materialize`).
  `ScenarioGenerator.materialize` scales drivers, not overlays, by `severity_scale`.
- Expected follow-ups after the first live ablation run: a severity cap or plausibility rule for
  compounded company-leg multipliers, and the point-in-time event-replay spec.
