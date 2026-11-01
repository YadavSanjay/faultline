# scenario-stability — run-to-run spread of the dual-RAG legs and the P&L they produce

## Requirement

Step 15c of `docs/design/scenario_set_and_ablation_design.md` §9, closing G9 (§7.7): "The dual-RAG
resolution runs three times at the same `as_of`. The report shows the spread of each MacroRAG and
XbrlRag leg and the resulting P&L range." MacroRAG synthesis and XbrlRag narrative search are LLM and
retrieval pipelines, so two runs with identical inputs can return different legs. Nothing measures how
much, so a P&L difference between two runs cannot be told apart from noise.

MacroRAG now accepts `no_cache` (bool, default false) at the top level of the `POST /query` body (not
inside `synthesis`); with it, MacroRAG neither reads nor writes either answer cache. Stability runs send
it, so each repetition re-runs retrieval as well as synthesis. XbrlRag has no cache flag; its calls are
simply repeated.

Neither §7.7 nor §8 gives a stability threshold. This change reports numbers only: **no threshold, no
flag, no status**. Judging what spread is acceptable is left to a later change.

The pure logic lives in a new module `src/scenariogen/stability.py`. A repetition is identified by its
index `r` in `0 .. n_repetitions − 1`.

When this change is done:

1. **Spread of one series.** `spread(values)` takes one value per repetition, in repetition order,
   where each value is a number or `None` (the quantity is absent in that repetition). It returns a
   dict with keys `n`, `n_present`, `values`, `min`, `max`, `range`, `mean`, `std`, `rel_range`,
   `sign_flip`, computed over the present values `x_1 .. x_k` (`k = n_present`) only:
   - `n = len(values)`; `n_present = k`; `values` is the input as a list, numbers converted to `float`,
     `None` kept in place;
   - `min`, `max` of the present values; `range = max − min`;
   - `mean = math.fsum(x) / k`;
   - `std` is the **sample** standard deviation, `sqrt(Σ(x_i − mean)² / (k − 1))`, or `None` when
     `k < 2`;
   - `rel_range = range / max_abs` with `max_abs = max(|x_i|)`, or `None` when `max_abs == 0` (every
     present value is exactly `0.0`). It lies in `[0, 2]` and does not blow up when the mean is near
     zero, which a range over `|mean|` would;
   - `sign_flip` is true when the present values include at least one value `> 0` and at least one
     value `< 0`; a value of exactly `0` counts as neither.

   When `k == 0`, `min`, `max`, `range`, `mean`, `std` and `rel_range` are `None` and `sign_flip` is
   false. When `k == 1`, `range = 0.0`, `std = None`, and `rel_range = 0.0` unless the value is `0.0`
   (then `None`).

   It raises `ValueError` when `values` is empty, or when an element is not `None` and not an `int` or
   `float` instance (a `bool` is rejected), or is NaN or infinite. The input is not mutated.

2. **Leg stability per scenario.** `leg_stability(resolutions, *, n_repetitions, sources)` takes one
   record per (scenario, repetition) — a mapping with `repetition` (int), `scenario` (str), optional
   `stopped` (bool, default false) and, for a record that is not stopped, `legs`: a mapping
   `{source: {leg: move}}`. `sources` defaults to `("macrorag", "xbrlrag", "xbrlrag_seed")`. It returns a list with one
   dict per scenario, in order of the scenario's first appearance in `resolutions`:
   `{"scenario", "n_repetitions", "n_resolved", "n_stopped", <source>: {leg: entry} for each source}`:
   - a repetition is **resolved** for a scenario when it has a record with `stopped` false;
     `n_resolved` counts them and `n_stopped = n_repetitions − n_resolved`. A repetition with no record
     for the scenario counts as stopped, exactly like a record with `stopped` true. A stopped record's
     `legs`, if any, are ignored;
   - in a resolved record, a missing or `None` `legs`, or a source missing from `legs` or mapped to
     `None`, means that source has no legs in that repetition;
   - for each source, the legs are the union of the leg names over the scenario's resolved records,
     listed in sorted order; a source with no legs in any repetition maps to `{}`;
   - each leg's `entry` is `spread(v)` plus `"n_missing"`, where `v` has length `n_repetitions` and
     `v[r]` is the leg's move in repetition `r`, or `None` when `r` is stopped or the leg is absent from
     that resolved repetition; `n_missing = n_resolved − n_present`.

   A leg absent from a resolved repetition is **missing, not zero**: it is left out of the statistics
   and counted in `n_missing`. A stopped repetition is neither: it is left out of the statistics and
   counted once per scenario in `n_stopped`.

   No records → `[]`. It raises `ValueError` for `n_repetitions < 1`; a `repetition` that is not an
   `int` (a `bool` is rejected) or lies outside `[0, n_repetitions)`; two records with the same
   `(scenario, repetition)` (stopped or not); a source key in a resolved record's `legs` that is not in
   `sources`; and any move `spread` rejects. The input is not mutated.

3. **P&L range per scenario tier.** `pnl_stability(pnls, *, n_repetitions)` takes one record per
   priced (scenario, tier, repetition) — a mapping with `repetition` (int), `scenario` (str), `severity`
   (str) and `total_pnl` (number). It returns a list with one dict per `(scenario, severity)`, in order
   of first appearance in `pnls`: `{"scenario", "severity", "n_repetitions", "n_priced", "n_missing"}`
   merged with `spread(v)`, where `v[r]` is the record's `total_pnl` for repetition `r`, or `None` when
   that repetition has no record (the scenario stopped, or the tier was not priced);
   `n_priced = n_present` and `n_missing = n_repetitions − n_priced`. The P&L range is `range`
   (`max − min`); `min` is the worst loss.

   No records → `[]`. It raises `ValueError` for `n_repetitions < 1`; a `repetition` that is not an
   `int` (a `bool` is rejected) or lies outside `[0, n_repetitions)`; two records with the same
   `(scenario, severity, repetition)`; and a `total_pnl` that is `None` or that `spread` rejects. The
   input is not mutated.

4. **Wiring** (covered by the implementer in `tests/unit/`).
   - **`no_cache` request flag.** `MacroRAGClient.synthesize(..., no_cache: bool = False)` adds
     `"no_cache": true` at the top level of the `/query` body when true, and leaves the key out when
     false, so every existing request body stays byte-identical. `MacroScenarioProvider(...,
     no_cache: bool = False)` passes it on every synthesis call, and
     `runner._build_dual_provider(..., macro_no_cache: bool = False)` passes it to the data provider
     (the health client never synthesises). The stress and ablation runners keep the default.
   - **Runner** `src/scenariogen/stability_runner.py`:
     `python -m src.scenariogen.stability_runner [--repetitions N] [--scenario X] [--severity T ...]
     [--book B] [--valuation-date D] [--cov-lookback L]`. `--repetitions` defaults to 3 (§7.7) and must
     be at least 2; `--severity` may repeat and defaults to every catalog tier. Everything except the
     dual-RAG provider is built once and shared by all repetitions (pricing engine, snapshot, catalog
     and Σ, crisis Σ, anchor book, option anchors, GPR and throughput multipliers), so only the MacroRAG
     and XbrlRag answers can differ between repetitions. Each repetition builds a **fresh** provider
     with `_build_dual_provider(..., macro_no_cache=True)` — fresh `_macro_cache` / `_arag_cache`, a
     fresh run-level XbrlRag budget and its own fail-loud preflight. Per repetition and scenario:
     `provider.layer_inputs(name)` resolves the services (a `ScenarioGenerationError` stops the scenario
     for that repetition: a stopped record, plus a `stopped` report entry with `repetition`, `scenario`,
     `reason`, `detail`); the leg record takes `legs["macrorag"]` = the resolution's drivers whose
     source `origin` is `"macrorag"` (`{}` when `used_macro` is false) and `legs["xbrlrag"]` =
     `company_drivers` (the fact-calibrated company legs, before news, crowding and tier scale; `{}`
     without an XbrlRag resolution) and `legs["xbrlrag_seed"]` = each company leg's narrative seed
     (`sources[factor]["seed_move"]` of the XbrlRag resolution, before fact calibration; `{}` without
     an XbrlRag resolution), so narrative-search noise and facts-lookup noise show separately. Each tier is then materialised exactly as the stress runner's
     dual-RAG production path does (`_materialize` / `_materialize_anchored`, state multiplier, option
     anchors, crisis Σ for the severe tier) and fully revalued without capital, liquidity, collateral,
     plausibility gate, sign-off, attribution or narrative; `total_pnl` is the sum of per-trade P&L. A
     `ScenarioGenerationError` for one tier leaves that tier unpriced in that repetition, with a
     `stopped` entry carrying `severity`. The run writes no forward-log predictions.
   - **Call trace** (as the ablation runner does): one trace collector bound for the whole run, each
     call tagged with its scenario; the sidecar `data/pricer/scenario/<date>/trace/<run_id>.jsonl` is
     written, and the report carries `trace` (the collector summary) and `latency_by_repetition`
     (per repetition, the total MacroRAG and XbrlRag call latency in seconds), so the uncached MacroRAG
     latency is visible.
   - **Report** `data/pricer/scenario/<date>/stability_<run_id>.json` via `storage.join` /
     `storage.write_text_atomic`, with `valuation_date`, `run_id`, `generated_at`, `n_repetitions`,
     `no_cache: true`, `severities`, `composition_rule`, `provenance`, `snapshot_id`,
     `factor_snapshot_id`, `covariance`, `resolutions` (per repetition and scenario: `world_view_id`,
     `used_macro`, `fallback_reason`, `used_arag`), `legs` (`leg_stability` output), `pnl`
     (`pnl_stability` output), `stopped`, `trace`, `latency_by_repetition`, and `note`: "Run-to-run spread at one as_of; no threshold is
     applied."
   - **Logging**: one line per repetition start; one line per scenario × tier with the P&L `min`,
     `max`, `range` and `n_priced`/`n_repetitions`; one line per scenario and source with its number of
     legs and the leg with the largest `range` (ties: first in sorted order); the report path.

Out of scope: a stability threshold or status, the stress and ablation runners' reports, the agentic
LLM path, per-trade P&L spread, the BFF and UI (step 15f), and MacroRAG's own cache (separate repo,
already done).

## Acceptance criteria

- **C1**: `spread([-0.10, -0.12, None])` returns `n = 3`, `n_present = 2`,
  `values = [-0.10, -0.12, None]`, `min = -0.12`, `max = -0.10`, `range = 0.02`, `mean = -0.11`,
  `std = sqrt(0.0002) ≈ 0.0141421`, `rel_range = 0.02 / 0.12 ≈ 0.166667`, `sign_flip = False`; and
  `spread([2.0, -1.0, 5.0])` gives `range = 6.0`, `mean = 2.0`, `std = 3.0`, `rel_range = 1.2`,
  `sign_flip = True`. Also the edge cases of Requirement 1: all `None`; one present value (non-zero and
  `0.0`); all present values `0.0` (`rel_range None`, `sign_flip False`); `[0.0, 1.0]` (no sign flip);
  ints accepted and returned as floats; and `ValueError` for `[]`, NaN, `inf`, `True` and a string.
  Properties: `min ≤ mean ≤ max` (within the approx tolerance); `range ≥ 0`; `rel_range`, when not `None`, lies in `[0, 2]`; adding
  a constant `c` to every present value leaves `range` and `std` unchanged; multiplying every present
  value by `a > 0` scales `min`, `max`, `range`, `mean` and `std` by `a` and leaves `rel_range` and
  `sign_flip` unchanged; multiplying by `−1` swaps and negates `min`/`max` and leaves `range`, `std`,
  `rel_range` and `sign_flip` unchanged; permuting the present values among themselves changes only
  `values`. Oracle: Requirement 1 by hand.
- **C2**: `leg_stability` with `n_repetitions = 3` returns the hand-built list for two scenarios: in
  scenario A, repetition 0 has MacroRAG legs `{ir:USD10Y: 0.0040, eq:XLK: -0.08}` and XbrlRag legs
  `{eq:NVDA: -0.20}`, repetition 1 has `{ir:USD10Y: 0.0050}` and `{eq:NVDA: -0.26, eq:AMD: -0.15}`,
  repetition 2 is a record with `stopped` true; scenario B is resolved in repetition 1 only, with no
  legs, and has no record for repetitions 0 and 2 (the call passes `sources=("macrorag", "xbrlrag")`). Expected: A has `n_resolved = 2`, `n_stopped = 1`,
  `eq:XLK` with `values [-0.08, None, None]`, `n_present 1`, `n_missing 1`, `eq:AMD` with
  `values [None, -0.15, None]`, `n_missing 1`, `ir:USD10Y` with `range 0.0010`, `mean 0.0045`,
  `n_missing 0`, legs listed in sorted order; B has `n_resolved = 1`, `n_stopped = 2` and `{}` for both
  sources. It returns `[]` for no records and raises `ValueError` for each invalid input of
  Requirement 2 (including a duplicate where one record is stopped, and an unknown source key).
  Properties: per scenario `n_resolved + n_stopped == n_repetitions`; every leg entry has
  `n_present + n_missing == n_resolved` and `len(values) == n_repetitions`; replacing a stopped record
  by no record never changes the output when the scenario has an earlier record in the input; shuffling the records changes only the order of scenarios,
  which follows first appearance. Oracle: Requirement 2 by hand.
- **C3**: `pnl_stability` with `n_repetitions = 3` returns the hand-built rows for scenario A at
  `mild` (P&L `-1.0e6, -1.2e6, -0.9e6` → `min -1.2e6`, `max -0.9e6`, `range 3.0e5`, `mean
  -1.0333…e6`, `std ≈ 152752.5`, `n_priced 3`, `n_missing 0`) and `adverse` (repetition 1 missing →
  `values [-2.0e6, None, -2.5e6]`, `n_priced 2`, `n_missing 1`, `range 5.0e5`), and scenario B at
  `mild` priced once (`std None`, `range 0.0`), in first-appearance order; `[]` for no records; and
  `ValueError` for each invalid input of Requirement 3. Properties: every row has
  `n_priced + n_missing == n_repetitions`; adding a record for a new tier never changes the existing
  rows; permuting the repetition indices of a row's records permutes its `values` and leaves every
  other field unchanged. Oracle: Requirement 3 by hand.

## Interface

```python
# src/scenariogen/stability.py (new)
def spread(values: Sequence[float | None]) -> dict[str, Any]
def leg_stability(resolutions: Sequence[Mapping[str, Any]], *, n_repetitions: int,
                  sources: Sequence[str] = ("macrorag", "xbrlrag", "xbrlrag_seed")) -> list[dict[str, Any]]
def pnl_stability(pnls: Sequence[Mapping[str, Any]], *, n_repetitions: int) -> list[dict[str, Any]]
```

## Notes

- Oracles are hand calculations on synthetic records. No `data/`, network or current date.
- Expected floats are compared with `pytest.approx` (relative 1e-12, absolute 1e-15).
- Why `rel_range` divides by `max |x|`, not `|mean|`: MacroRAG legs can change sign between
  repetitions; a mean near zero would make a relative spread arbitrarily large, while `max |x|` is zero
  only when every value is exactly zero (inputs, not a computed float).
- Why the sample standard deviation: the repetitions are a sample of the services' run-to-run
  variation, and with three repetitions the population formula understates it by a factor
  `sqrt(2/3)`.
- Why a missing leg is not counted as zero: a MacroRAG field that is absent in one repetition means the
  static driver or propagation sets that factor, not that the factor did not move; counting it as `0`
  would invent a value. The `n_missing` count shows how often a leg drops out, which is itself part of
  the instability.
- The XbrlRag leg is the fact-calibrated `company_drivers` value; its narrative seed (`seed_move`) is
  reported as a third source, `xbrlrag_seed` (decision 2026-10-18). The news and crowding multipliers
  are not reported: they come from local files and do not change between repetitions.
- Decisions 2026-10-18: a missing leg counts as missing (not zero); every catalog tier is priced by
  default; the runner writes a call trace.
- Clarifications (from writing the tests): a stopped record's `legs` are ignored entirely, even a
  source key outside `sources`; a float `repetition` (e.g. `1.0`) is rejected like any non-`int`;
  every float statistic is returned as a `float` (int inputs come back as floats in `values`, `min`,
  `max`); `values` is always a `list`, whatever sequence type was passed; the order of the
  "no records" and "`n_repetitions < 1`" checks is not specified.
