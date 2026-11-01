# macro-curve-coherence — ask MacroRAG for every field with its direction; keep the UST curve coherent

## Requirement

`MacroScenarioProvider` (`src/scenariogen/macro/provider.py`) asks MacroRAG for a scenario's macro
legs, and each returned leg replaces that factor's authored driver from `scenarios.json`. Authored
drivers that MacroRAG did not return stay as they are.

The 2026-10-14 live `--dual-rag` run (`8da52756-03e2-4e3c-a998-13903b07a53e`, valuation date
2026-10-06) showed two defects:

1. **A partly-supplied UST curve is spliced with authored tenors.** `rate_capital` is authored as a
   bear-steepener (`ir:DGS2` +100 bp, `ir:DGS10` +100 bp, `ir:DGS30` +150 bp). MacroRAG returned
   only `UST_10Y` +28 bp and `UST_30Y` +32 bp (the April 2025 tariff card). The resolved drivers were
   `ir:DGS2` +100 bp (authored) with `ir:DGS10` +28 bp and `ir:DGS30` +32 bp (MacroRAG): a bear
   *flattener*, the opposite of the scenario. Under the conditional-covariance engine, a curve node
   that is not a driver is propagated from the drivers, so the coherent result is to let the
   unsupplied authored tenors go and propagate them from MacroRAG's tenors.
2. **The request does not say which fields are wanted or which way they should move.** faultline
   sends only the question, filters and `synthesis.horizon_days`; each scenario's `expect_fields` in
   `macro.json` is never sent, and the direction of each leg exists only in the question prose.
   MacroRAG accepts both (`SynthesisOptions.fields` restricts the emitted vector;
   `SynthesisOptions.directions` maps a field to `"up"`/`"down"` under the field's `positive_means`
   convention and flags a field whose sign contradicts it `direction_mismatch`, a hard code that
   MacroRAG drops only when its `drop_invalid` is on).

When this change is done:

1. **The request carries the wanted fields and their directions.** Every synthesize request body
   `MacroScenarioProvider` sends includes, inside `"synthesis"` and next to `horizon_days`:
   - `"fields"`: the scenario's `macro.json` `expect_fields`, in config order; omitted when
     `expect_fields` is missing or empty;
   - `"directions"`: the scenario's `macro.json` `directions` (a new optional object, field name →
     `"up"` | `"down"`); omitted when missing or empty.
2. **`direction_mismatch` is a hard code.** A field whose `validation` contains `direction_mismatch`
   is dropped exactly like the three existing hard codes (treated as if absent from the response).
3. **The UST curve is coherent.** When the macro legs are applied (`used_macro` true) and at least
   one applied MacroRAG leg is an IR curve node (`ir:*`), every authored `ir:*` driver that MacroRAG
   did not supply is removed from `DriverResolution.drivers` and from `DriverResolution.sources`, so
   the engine propagates it from the MacroRAG tenors. The removed factor keys are reported, sorted,
   in `DriverResolution.to_dict()["superseded_static"]` (an empty list when nothing was removed).
   Authored drivers that are not IR curve nodes are unaffected. When no applied MacroRAG leg is an
   IR node, or the macro legs are not applied, the drivers are exactly as today. Overlays are never
   touched. The dual-RAG path inherits this through the macro resolution it already uses.
4. **`directions` is validated when the provider is built.** `MacroScenarioProvider(config, ...)`
   raises `ConfigError` (`src.common.config.models.ConfigError`) if any scenario's `directions` has a
   value other than `"up"` or `"down"`, or a key that is not in that scenario's `expect_fields`.
5. **The committed config states every direction.** In `configs/scenariogen/macro.json`, each of the
   four dual-RAG scenarios has `expect_fields` and `directions` exactly as in the table below, the
   directions following each scenario's `synthesis_question` (MacroRAG `positive_means`: UST `up` =
   yields rise; OAS `up` = spreads widen; MOVE/VIX `up` = vol rises; USDTWD/USDJPY `up` = TWD/JPY
   depreciate):

   | Scenario | `expect_fields` → `directions` |
   |---|---|
   | `taiwan_geopolitical` | USDTWD up, USDJPY down, IG_OAS up, HY_OAS up, MOVE up, VIX up, UST_2Y down, UST_5Y down, UST_10Y down, UST_30Y down |
   | `ai_deflation` | IG_OAS up, HY_OAS up, MOVE up, VIX up, UST_2Y, UST_5Y, UST_10Y, UST_30Y (no direction declared for the UST legs) |
   | `rate_capital` | UST_2Y up, UST_5Y up, UST_10Y up, UST_30Y up, MOVE up, IG_OAS up, TERM_PREMIUM_10Y up |
   | `keyman_nvda` | VIX up, IG_OAS up, MOVE up |

   (`expect_fields` is the set of fields in the row, in the row's order.)

Out of scope: MacroRAG's choice of episode (a MacroRAG task); any coherence rule for factor groups
other than the IR curve; `synthesis.scenario`; changing a question's wording; scenarios that do not
use MacroRAG.

## Acceptance criteria

- **C1**: For a scenario whose `macro.json` entry has `expect_fields` F and `directions` D, the body
  posted to `/query` has `synthesis.fields == F` (same order), `synthesis.directions == D`, and
  `synthesis.horizon_days` as before; with F empty or missing the `fields` key is absent, and with D
  empty or missing the `directions` key is absent. Oracle: Requirement 1, on synthetic configs.
- **C2**: A field carrying `direction_mismatch` (alone or with other codes) yields no driver, no gate
  finding and no applied leg; a dropped `IG_OAS` lets a valid `HY_OAS` map to `credit`. Oracle:
  Requirement 2, by hand.
- **C3**: For `rate_capital` (authored `ir:DGS2` +50, `ir:DGS10` +100, `ir:DGS30` +150 bp) and a
  response with `UST_10Y` +28 bp, `UST_30Y` +32 bp and `IG_OAS` +25 bp citing a fresh chunk, the
  resolved drivers are exactly `{ir:DGS10: 0.0028, ir:DGS30: 0.0032, credit: 0.0025}` and
  `superseded_static == ["ir:DGS2"]`, in plain mode and through `DualRagEnrichmentProvider`. With
  only `IG_OAS` returned, or with a failing gate, the authored IR drivers are all kept and
  `superseded_static == []`. In general, for any authored driver set and any applied MacroRAG legs:
  the drivers are the MacroRAG legs plus the authored drivers they don't replace, minus the authored
  `ir:*` drivers not supplied by MacroRAG when MacroRAG supplied at least one `ir:*` leg. Oracle:
  Requirement 3; conversions bp × 1e-4.
- **C4**: `MacroScenarioProvider` construction raises `ConfigError` for a `directions` value other than
  `"up"`/`"down"` and for a `directions` key missing from `expect_fields`; a valid config constructs.
  Oracle: Requirement 4.
- **C5**: The committed `macro.json` has, for each of the four scenarios, `expect_fields` and
  `directions` equal to the Requirement 5 table. Oracle: the table (from each `synthesis_question`
  and MacroRAG's `positive_means`).

## Interface

No new interface. Existing signatures, exercised as they are:

- `src.scenariogen.macro.provider.MacroScenarioProvider(config, *, as_of, client=None,
  project_root=None)` with `resolve(name) -> DriverResolution` and `DriverResolution.to_dict()`
- `src.scenariogen.dualrag.DualRagEnrichmentProvider(*, macro_provider=None, arag_provider=None,
  news_resolution=None, crowding_resolution=None, project_root=None)` with `definition_for(...)`
- `src.scenariogen.macro.client.MacroRAGClient` over a transport with `get(path)` / `post(path,
  payload)` (the I/O boundary tests may fake)
- `src.scenariogen.macro.models.MacroSynthesis.from_response(d)`,
  `src.scenariogen.macro.mapping.to_canonical_drivers(fields, move_base=100.0)`,
  `src.scenariogen.macro.gate.MacroGate(as_of, *, freshness_days=120, min_chunks_per_driver=1,
  require_citations=True)`
- `src.common.config.models.ConfigError`

The implementer adds whatever it needs (e.g. keywords on `MacroRAGClient.synthesize`, a
`superseded_static` attribute on `DriverResolution`); none of that is locked.

## Notes

- Fakes only at the `MacroRAGClient` and ARag client transports. Never fake `MacroScenarioProvider`,
  `DualRagEnrichmentProvider`, `MacroGate` or the mapping.
- Tests may use the committed configs via `get_project_root()`, synthetic config dicts, or a temporary
  project root holding a synthetic `configs/scenariogen/scenarios.json` (the provider reads authored
  drivers from `<project_root>/configs/scenariogen/scenarios.json`).
- IR curve nodes: `ir:DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2, DGS3, DGS5, DGS7, DGS10, DGS20, DGS30`.
  `UST_2Y/5Y/10Y/30Y` map to `ir:DGS2/5/10/30`; a parallel `UST` maps to every node.
- Response shape as in `docs/specs/dualrag-macro-gate.md` (Notes). Request options: MacroRAG
  `src/api/models.py` `SynthesisOptions` (`fields`, `horizon_days`, `scenario`, `directions`).
- All data synthetic; no network, no `data/`, no current date (pass `as_of` explicitly).
- Updated 2026-10-30 after later changes (retired spec; tests kept in step): the new scenario set replaced
  `ai_bust` with `ai_deflation`, which declares no direction for its UST legs, and moved `rate_capital`'s
  `ir:DGS2` to +50 bp; macro-real-tp-fields added `TERM_PREMIUM_10Y` to `rate_capital`; options-shock-sizing
  made `keyman_nvda`'s horizon 21 days. The defect described under Requirement quotes the values of that run.
