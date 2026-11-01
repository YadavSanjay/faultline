# signoff-replay-versions — the sign-off CLI signs the versions the stress runner checks

## Requirement

A scenario version is the content hash of its spec (`versioning.compute_scenario_version`). Since
`scenario-historical-replay`, a replay scenario's drivers and horizon are resolved from the episode
window when `ScenarioCatalog` loads, and the stress runner registers and gates on the version of that
**resolved** spec. `signoff_runner` still computes its default version from the **raw**
`scenarios.json`, where a replay has no drivers. Two consequences today:

- every replay with the same overlays gets the same raw version (`ai_deflation` and `tariff_shock`
  both list as `9eed806bab978209`);
- `--approve <replay>` without `--version` signs a version that `--require-signoff` never checks,
  so an approved replay is still held out.

When this change is done:

1. **Versions from the catalog.** `signoff.scenario_versions(catalog)` returns, for every scenario in
   `catalog.names()` and every sensitivity in `catalog.sensitivity_names()`, in that order, the
   version of its resolved spec, `compute_scenario_version(catalog.spec(name))`:
   - a name whose spec can't be resolved (`catalog.spec` raises `ScenarioGenerationError`, e.g. a
     replay window the history doesn't cover) is left out;
   - an authored (non-replay) scenario keeps exactly the version it has today.
2. **Wiring** (covered by the implementer in `tests/unit/`). `signoff_runner` builds the catalog the
   way the stress runner does (`ScenarioCatalog.from_config` over `FactorHistory`) and uses
   `scenario_versions` for `--list` and for the default version of `--approve`/`--reject`. A name
   that isn't in the result exits with an error that says why (unknown, or unresolvable replay),
   unless `--version` is given. `--list` also shows the sensitivities.

Out of scope: changing the version hash itself (`_ECONOMIC_KEYS`), which would change every
registered replay version, and re-recording past sign-offs.

## Acceptance criteria

- **C1**: `scenario_versions` returns `compute_scenario_version` of each resolved spec, for the
  scenarios then the sensitivities in catalog order; an authored scenario's version equals the hash
  of its config spec; an unresolvable name is left out. Properties: two replays over the same history
  whose windows give different episode moves get different versions; a version doesn't depend on
  the spec's non-economic keys (narrative, trigger); changing a replay's history under its window
  changes its version. Oracle: `compute_scenario_version` applied in the test to specs the test
  resolves by hand from its own synthetic series (moves computed in the test); properties.

## Interface

- `src.scenariogen.signoff.scenario_versions(catalog: ScenarioCatalog) -> dict[str, str]` (in `signoff.py`, which imports no pricing code; `ScenarioCatalog` may be imported for type checking only)

`ScenarioCatalog` is `src.scenariogen.catalog.ScenarioCatalog`, built in tests directly from a toy
`FactorCovariance` and specs (replays resolved with `catalog.resolve_replay` on synthetic series),
as its dataclass constructor allows.

## Notes

- Spec tests use synthetic series and specs only; no `data/`, services or QuantLib.
- `compute_scenario_version` and `resolve_replay` are existing, unchanged functions; the tests use
  them to build their expected values only from inputs the test defines.
- The spec-PR stub imports `ScenarioCatalog` at module level, because the stub lint admits only added
  imports (an `if TYPE_CHECKING:` block counts as a change outside stubs). The implementation moves that
  import under `typing.TYPE_CHECKING`, so `signoff.py` imports no pricing code at run time.
