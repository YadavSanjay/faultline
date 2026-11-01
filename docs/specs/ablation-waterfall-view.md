# ablation-waterfall-view — BFF and UI ablation waterfall, signal-health view and replay drivers

## Requirement

Step 15f of `docs/design/scenario_set_and_ablation_design.md` §9 (G10, G11; step 15: "BFF and UI
waterfall"). Steps 15b, 15d and 15e put held-out tiers, signal health and cost per layer into the run
reports, but the UI shows none of them. §7.1 asks for a demo view: "a per-scenario waterfall
(authored → + MacroRAG → + XbrlRag → + key events → + market intelligence → final), with the Shapley
bar beside it". The BFF also shows wrong definitions for replay scenarios: `configs/scenariogen/
scenarios.json` keeps a replay's `"drivers": {}` and a placeholder `"horizon_days": 10` (the real
drivers and horizon are resolved from the episode in `ScenarioCatalog.from_config`). The BFF reads
that raw file, so a replay tier missing from the date's reports (held out by the gate, stopped, not
run) shows no drivers, and the catalog shows the placeholder horizon for every replay.

The pure logic lives in a new module `src/scenariogen/report_views.py`. The BFF (`ui/bff/app/
scenarios.py`) imports it; the module imports only the standard library and
`src.scenariogen.ablation`, so the BFF image stays QuantLib-free.

Waterfall steps group the six ablation layers (`ablation.LAYERS`) by the §7.1 sources table:

| Step id | Layers | §7.1 label |
|---|---|---|
| `macrorag` | `macrorag` | + MacroRAG |
| `xbrlrag` | `xbrlrag_narrative`, `xbrlrag_facts` | + XbrlRag |
| `events` | `news` | + key events |
| `market_intelligence` | `crowding`, `state` | + market intelligence |

The bars are the ladder (cumulative prefixes in the record's `order`, order-dependent); the grouped
Shapley value (order-free) sits beside each bar, as §7.1 asks.

When this change is done:

1. **Report selection.** `select_report(reports, *, run_id=None, require=None)` picks one report
   from a list of decoded report dicts (stress or ablation):
   - with `run_id`: the report whose `"run_id"` equals it, or `None` when there is none;
   - otherwise, among the reports that contain the key `require` (all reports when `require` is
     `None`), the one with the latest `"generated_at"` (parsed with `datetime.fromisoformat`; a
     report with no or a `None` `generated_at` ranks below every dated one); ties go to the
     lexicographically greatest `run_id` (a missing `run_id` counts as `""`); `None` when no report
     qualifies.

   It raises `ValueError` for two reports with the same non-empty `run_id`, or a `generated_at` that
   is not a valid ISO string. The input is not mutated.

2. **Waterfall of one scenario tier.** `waterfall(record, cost_rows=None)` takes one entry of an
   ablation report's `"scenarios"` (keys `scenario`, `severity`, `composition_rule`, `order`,
   `ladder`, `shapley`, and optionally `subsets`) and the report's `layer_cost["rows"]` (or `None`
   when the report has no `layer_cost` block). It returns
   `{"scenario", "severity", "composition_rule", "authored_pnl", "final_pnl", "authored_floor_hits",
   "steps"}`:
   - `authored_pnl` is `ladder[0]["total_pnl"]`; `final_pnl` is `ladder[-1]["total_pnl"]`;
   - `steps` has the four steps of the table above. A step's *present layers* are its layers that
     appear in `order`, kept in `order`'s order. Steps with present layers come first, ordered by the
     position of their first present layer in `order`; steps with none follow in table order;
   - each step is `{"step", "layers", "pnl_before", "pnl_after", "delta", "shapley", "floor_hits",
     "n_calls", "latency_s", "cost_usd", "contribution_per_s", "contribution_per_usd"}`:
     - `layers` is the step's present layers;
     - `pnl_after` is the ladder `total_pnl` at the step's last present layer; `pnl_before` is the
       ladder `total_pnl` one ladder entry before its first present layer (the authored entry for
       the first step); for a step with no present layers both equal `final_pnl`;
     - `delta = pnl_after − pnl_before`;
     - `shapley` is the sum of `record["shapley"].get(layer, 0.0)` over all the step's layers in the
       table (present or not);
     - `floor_hits` is the `floor_hits` list of the entry of `record["subsets"]` whose `layers`
       (sorted) equal every layer of `order` up to and including the step's last present layer,
       sorted; `[]` when the step has no present layers, `subsets` is missing, or no entry matches;
     - when `cost_rows` is `None`, `n_calls`, `latency_s`, `cost_usd` and both ratios are `None`.
       Otherwise, over the rows whose `scenario` and `severity` equal the record's and whose `layer`
       is one of the step's table layers: `n_calls` and `latency_s` are the sums (`0` and `0.0` when
       no row matches); `cost_usd` is `None` when every matching row's `cost_usd` is `None` (or none
       matches), else the sum of the ones that are not; `contribution_per_s = |shapley| / latency_s`
       when `latency_s > 0`, else `None`; `contribution_per_usd = |shapley| / cost_usd` when
       `cost_usd` is not `None` and `> 0`, else `None`;
   - `authored_floor_hits` is the sorted `floor_hits` of the `subsets` entry with `layers == []`,
     else `[]`.

   It raises `ValueError` when: `ladder` is empty or `ladder[0]["layer"]` is not `"authored"`; the
   `layer` of `ladder[1:]` does not equal `order`; `order` holds an id outside `LAYERS` or a
   repeated id; or a step's present layers are not consecutive in `order` (another step's layer sits
   between them). The input is not mutated.

3. **Ablation view.** `ablation_view(report)` returns `{"run_id", "valuation_date",
   "generated_at", "severity", "note", "composition_rules", "mixed_rules", "scenarios", "stopped",
   "overhead"}`:
   - `run_id`, `valuation_date`, `generated_at`, `severity` and `note` are copied (`None` when
     missing);
   - `scenarios` is `[waterfall(r, cost_rows) for r in report["scenarios"]]` in report order, with
     `cost_rows = report["layer_cost"]["rows"]` when `"layer_cost"` is present and not `None`, else
     `None`;
   - `composition_rules` is the sorted distinct `composition_rule` values of those scenarios;
     `mixed_rules` is true when there are two or more (§7.1: results compare only within one rule);
   - `stopped` is `report.get("stopped", [])`, copied;
   - `overhead` is `report["layer_cost"]["overhead"]` when the block is present, else `None`.

   It raises `ValueError` for two scenarios with the same `(scenario, severity)`, and propagates the
   `ValueError` of `waterfall`.

4. **Signal-health view.** `signal_health_view(report)` takes one decoded stress report and returns
   `{"available", "run_id", "valuation_date", "generated_at", "overall", "counts", "sources",
   "held_out", "stopped", "attention"}`:
   - `available` is false when the report has no `"signal_health"` key or it is `None` (a run from
     before step 15d); then `overall` and `counts` are `None` and `sources` is `[]`, while `held_out`,
     `stopped` and `attention` are still built;
   - otherwise `overall`, `counts` and `sources` are copied from `signal_health` (`sources` keeps its
     worst-first order), and each source gets one more key `n_holds`, the sum of its `holds` counts;
   - `held_out` lists `report.get("held_out", [])` in report order, each entry copied with three more
     keys: `n_findings`, `n_fail` (findings whose `severity` is `"fail"`) and `n_flag` (`"flag"`);
   - `stopped` is `report.get("stopped", [])`, copied;
   - `attention` = the number of sources with status `"down"` or `"degraded"` + `len(held_out)` +
     `len(stopped)`.

5. **Scenario definition (replay fix).** `definition_view(raw_spec, *, entry=None,
   stored_spec=None)` returns the drivers and horizon to show for a scenario tier:
   `{"source", "drivers", "overlays", "horizon_days", "severity_scale", "scaled", "replay"}`.
   `raw_spec` is the `scenarios.json` entry; `entry` the stress-report entry for the tier (or
   `None`); `stored_spec` the version-store content `data/pricer/store/scenario/versions/
   <version>.json` (the replay-resolved spec the runner registered; or `None`). The first rule that
   applies wins:
   - `"report"`: `entry["materialized"]["provenance"]["drivers"]` exists and is non-empty. `drivers`
     and `overlays` are the provenance's (floats as stored; drivers already × `severity_scale`),
     `horizon_days` and `severity_scale` the provenance's, `scaled` true, `replay` the provenance's
     `"replay"` block copied (or `None`);
   - `"version"`: `stored_spec` is not `None` and its `drivers` is non-empty. `drivers`, `overlays`
     and `horizon_days` come from `stored_spec`, `severity_scale` is `None`, `scaled` false;
   - `"config"`: `raw_spec` has no `"replay"` key. `drivers`, `overlays` and `horizon_days` come from
     `raw_spec`, `severity_scale` `None`, `scaled` false;
   - `"unresolved"`: a replay with nothing resolved. `drivers` is `{}`, `overlays` from `raw_spec`,
     `horizon_days` `None` (the raw value is a placeholder), `severity_scale` `None`, `scaled` false.

   In the last three cases `replay` is `{"start": str(r["start"]), "end": str(r["end"])}` from the
   used spec's `"replay"` block, or `None` when it has none. Drivers and overlays from a spec are
   converted to canonical decimal moves as `scenario_gen.to_canonical` does: a number is taken as is;
   for a mapping the first key present of `move_bp` (× 1e-4), `move_pct` (÷ 100), `move_pts`
   (÷ 100), `move_rel`, `move` gives the value. A mapping with none of these keys raises
   `ValueError`. The inputs are not mutated.

6. **Wiring** (covered by the implementer in `tests/bff/` and `ui/app` vitest).
   - **Replay fix** in `ui/bff/app/scenarios.py`: `catalog()` sets each cell's `horizon_days` from
     `definition_view(spec, entry=r)` instead of the raw `spec.get("horizon_days")` (line 259);
     `detail()`, when the tier is not in the date's reports (lines 295-298), returns
     `available: false` plus a `definition` block from `definition_view(cfg, stored_spec=…)`, where
     the stored spec is loaded by the `scenario_version` of the tier's `held_out` entry in the newest
     report (else none), and a `reason` that names the outcome: `held_out` (with its findings),
     `stopped` (with its reason) or `not run`. Catalog cells for tiers not run get `outcome`
     (`"ran"`, `"held_out"`, `"stopped"`, `"not_run"`) from the newest stress report of the date.
   - **Endpoints** (declared in `main.py`, tag `scenarios`, `valuation_date` query param as the
     existing routes, declared before `/api/scenarios/{name}`):
     - `GET /api/scenarios/ablation?valuation_date=&severity=&run_id=` → the newest
       `ablation_*.json` of the date (filtered by `severity` when given; `select_report`), as
       `{"available": true, "available_dates", "runs": [{run_id, severity, generated_at}],
       **ablation_view(report)}`, or `{"available": false, "reason"}` when there is none or
       `ablation_view` raises `ValueError` (reason = the message).
     - `GET /api/scenarios/ablation/{name}?valuation_date=&severity=&run_id=` → the one scenario's
       waterfall from the same report, plus the report-level fields, or `available: false`.
     - `GET /api/scenarios/signal-health?valuation_date=&run_id=` →
       `signal_health_view(select_report(reports, run_id=run_id, require="signal_health"))`, falling
       back to the newest report when none carries the block (so held-out and stopped still show).
     `available_dates()` also lists dates that have only `ablation_*.json` files.
   - **UI** (`ui/app`, hand-typed client in `src/lib/api.ts`, TanStack Query hooks, design tokens,
     `EChart`):
     - Scenarios layout gains two tabs: **Layer Ablation** (`/scenarios/ablation`) — a scenario list
       with final P&L and the mixed-rule warning, a per-scenario waterfall chart (authored bar, four
       step bars coloured by sign of `delta`, final bar) with a horizontal Shapley bar per step beside
       it, floor-hit markers on steps whose `floor_hits` is non-empty ("contribution understated"),
       the calls / latency / cost per step ("not reported" for a `None` cost), and the §7.1 reading
       rule "measures influence, not value"; and **Signal Health** (`/scenarios/signal-health`) — an
       overall status badge, one row per source (status, latest, staleness, coverage, holds) worst
       first, the held-out tiers with their fail/flag counts and findings, and the stopped scenarios.
     - Scenario detail gains a **Layers** tab showing that scenario's waterfall when the ablation
       endpoint has it; the Shock Derivation tab renders the `definition` block for a tier not run,
       and the replay window (`replay.start`–`replay.end`, observed factors, excluded moves) for a
       replay.
     - The Control Room / Cockpit board shows the signal-health `overall` badge with `attention`.

Out of scope: changing the ablation, signal-health or gate logic; re-running replays in the BFF;
alerts; recording sign-off holds in the report (the runner skips unsigned scenarios without a
`held_out` entry today).

## Acceptance criteria

- **C1**: `select_report` returns the hand-picked report for a list mixing reports with and without
  the `require` key, a tie on `generated_at` (greatest `run_id` wins), a report without
  `generated_at`, and a given `run_id` (present and absent), returns `None` for an empty list, and
  raises `ValueError` for a duplicate `run_id` and for an invalid `generated_at`. Properties:
  shuffling the input never changes the result; the result, when not `None`, contains `require`.
  Oracle: Requirement 1 by hand.
- **C2**: `waterfall` returns the hand-built dict for a six-layer record in `LAYERS` order with cost
  rows (a layer with reported cost, a layer with none, a row of another scenario and of another
  severity), for the same record with `cost_rows=None`, for an `order` that leaves out `state` and
  `xbrlrag_facts` (partial and empty step), for a reordered `order` (`news` first), and with and
  without `subsets` (floor hits found, not found); and raises `ValueError` for each invalid input of
  Requirement 2. Properties, on records built from a random characteristic function `v(S)` with
  `ablation.ladder_attribution` and `ablation.shapley_attribution` over a random valid order: the
  step deltas sum to `final_pnl − authored_pnl`; the step Shapley values sum to the same (Shapley
  efficiency); swapping two layers of one step inside `order` changes no step's `pnl_before`,
  `pnl_after` or `delta`; every ratio is `None` exactly when its denominator is `None` or `0`.
  Oracle: Requirement 2 by hand; the sum properties are exact identities of the ladder and of Shapley
  efficiency (tolerance 1e-9 relative).
- **C3**: `ablation_view` returns the hand-built view for a two-scenario report with a `layer_cost`
  block and mixed composition rules, for a report without `layer_cost` (every cost field `None`,
  `overhead` `None`) and without `stopped`, and raises `ValueError` for a duplicate
  `(scenario, severity)` and for a scenario with an invalid ladder. Properties: `mixed_rules` is true
  exactly when `composition_rules` has two or more entries; each `scenarios[i]` equals
  `waterfall(report["scenarios"][i], rows)`. Oracle: Requirement 3 by hand.
- **C4**: `signal_health_view` returns the hand-built view for a report with signal health (mixed
  statuses, holds), held-out tiers (fail, flag and other findings) and stopped scenarios, and for a
  report without `signal_health` (`available` false, held-out and stopped still built). Properties:
  `attention` equals the down + degraded sources plus held-out and stopped counts; `n_fail + n_flag
  <= n_findings` for every held-out entry; the input report is not mutated. Oracle: Requirement 4 by
  hand.
- **C5**: `definition_view` returns the hand-built dict for each source case: a report entry of a
  replay (scaled drivers and the replay block), a held-out replay with a stored spec, an authored
  scenario from the raw config (each unit key converted), and a replay with nothing resolved
  (`drivers == {}`, `horizon_days is None`, the window); an entry with empty provenance drivers falls
  through to the next rule; a mapping with no unit key raises `ValueError`. Properties: `scaled` is
  true exactly when `source == "report"`; adding an `entry` with non-empty drivers always gives
  `source == "report"` whatever `stored_spec` is; `"unresolved"` occurs only for a raw spec with a
  `replay` block; a unit conversion matches the hand factor for any finite value. Oracle:
  Requirement 5 by hand.

## Interface

```python
# src/scenariogen/report_views.py (new)
def select_report(reports: Sequence[Mapping[str, Any]], *, run_id: str | None = None,
                  require: str | None = None) -> Mapping[str, Any] | None
def waterfall(record: Mapping[str, Any],
              cost_rows: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]
def ablation_view(report: Mapping[str, Any]) -> dict[str, Any]
def signal_health_view(report: Mapping[str, Any]) -> dict[str, Any]
def definition_view(raw_spec: Mapping[str, Any], *, entry: Mapping[str, Any] | None = None,
                    stored_spec: Mapping[str, Any] | None = None) -> dict[str, Any]
```

`LAYERS` is imported from `src/scenariogen/ablation.py`. The stub gate allows only functions and
imports in a new stub module, so the implementer adds the step table as a constant (for example
`WATERFALL_STEPS: tuple[tuple[str, tuple[str, ...]], ...]`); the tests use the literal step ids and
layer lists of the table above.

## Notes

- Oracles are hand calculations on synthetic report dicts. No `data/`, network or current date.
- **Where the stubs live.** `.claude/tdd/profile.toml` sets `src_roots = ["src"]`, so a stub module
  under `ui/bff/` is not allowed; the module goes in `src/scenariogen/` (in the strict mypy gate). The
  BFF already imports `src.scenariogen.*` (`news.py`, deferred imports in `scenarios.py`);
  `tests/unit/test_import_boundaries.py` only restricts `src/pricer` and `src/common`, so the import is
  allowed. `tests/bff/test_bff_quantlib_free.py` must stay green: the module may import only the
  standard library and `src.scenariogen.ablation` (numpy only). It must not import `scenario_gen`
  (which pulls in `src.pricer.risk`), which is why Requirement 5 restates `to_canonical`'s unit rule.
- **Ladder vs Shapley.** The ladder depends on `order` (a layer added later is measured on top of
  the earlier ones); Shapley does not. The ablation runner always uses `order = LAYERS`, whose
  grouping is contiguous; Requirement 2 still handles other orders so a custom-order record either
  renders correctly or fails loud.
- **Composition rule.** The ablation report's top-level `composition_rule` is the unanchored
  constant (`dualrag-v1`) even when its records are anchored (`dualrag-v3`); the view uses the
  per-record values.
- **Replay-drivers bug, what was checked.** In every local stress report each scenario entry,
  replays included, carries a non-empty `materialized.provenance.drivers` and a `replay` block, and
  `detail("ai_deflation", …)` returns 44 drivers when a report has the tier. The drivers go missing
  only when the tier is absent from every report of the date (gate hold — `ai_deflation` was held at
  every tier in the 2026-10-18 dual-RAG run — stop, sign-off hold, not run): `detail()` then returns
  only the narrative, and the raw config has no replay drivers to fall back on. The horizon bug shows
  on every replay row of the catalog (`10` instead of e.g. `221` for `ai_deflation`, `4` for
  `tariff_shock`).
- Contribution in the per-step ratios uses `|shapley|`, as in `docs/specs/cost-per-layer.md`.
- Decisions 2026-10-18: held-out tiers and stopped scenarios do not change `overall` (it keeps the
  meaning of the report's `signal_health.overall`); they count only in `attention`. Out of scope:
  recording sign-off-held scenarios in the report, gross reallocation and rank correlation per step,
  and a fallback to the latest version-store entry by name when a tier has no `held_out` entry.
- The handover note "the BFF shows no drivers for replay scenarios" was not reproduced for tiers that
  ran; the two causes found (a tier absent from every report of the date, and the catalog's horizon
  read from the unresolved config) are what Requirement 5 and the wiring fix.
- Clarifications (from writing the tests): a step's Shapley sum and its calls, latency and cost sum
  over all of the step's table layers, present in `order` or not; when several `subsets` entries match
  the same prefix the first is used; timestamps are compared as given (no timezone handling);
  unit keys in a driver spec are read in table order, first key wins (`{"move_bp": 10, "move": 99}`
  gives 0.001); the "version" rule needs non-empty `stored_spec` drivers, else the next rule applies.
