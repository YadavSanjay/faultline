# dualrag-macro-gate — a required MacroRAG leg fails loud; measured episode cards are exempt from freshness

## Requirement

In the named-scenario dual-RAG path (`python -m src.scenariogen.runner --dual-rag`),
`DualRagEnrichmentProvider` (`src/scenariogen/dualrag.py`) merges the authored drivers with
MacroRAG macro legs (`MacroScenarioProvider`, `src/scenariogen/macro/provider.py`) and ARag company
legs. Each scenario in `configs/scenariogen/arag.json` declares `required_services`, for example
`["macro", "arag"]`. CLAUDE.md and the dual-RAG design (decision 2, 2026-07-12) say this path is
fail-loud: a scenario whose required grounding is unavailable is **stopped**
(`ScenarioGenerationError`, recorded in the report's `stopped` list), never silently priced with
static numbers.

Defects, seen in run `f76773c1-1da1-4df4-afcc-afec7961377b` (2026-10-13, valuation date
2026-10-06), and the MacroRAG changes that followed (2026-10-15 handoff note from MacroRAG):

1. **A required macro leg falls back silently.** Only ARag enforces `required_services` per
   scenario (`src/scenariogen/arag/provider.py`). For MacroRAG, `required_services` drives only the
   up-front health preflight. When the synthesis call fails, returns no shock vector, or fails the
   freshness/citation gate, `MacroScenarioProvider.resolve` returns the static drivers with
   `used_macro=False`, and the dual-RAG run prices the scenario anyway. In the run above,
   `taiwan_geopolitical` (which requires `macro`) was priced at −$92M (mild) and −$132M (adverse)
   with static macro legs, after a MacroRAG HTTP 500 in one attempt and a gate failure in the next.
2. **The freshness rule cannot tell measured episode data from stale prose.** The gate failure
   above came from a St. Louis Fed paper dated 2025-02-26 (587 days old against a 550-day window)
   that anchored VIX +10 pts and UST_10Y −30 bp on the 9/11 week, tagged `grounding_mode: "analog"`.
   The first draft of this spec exempted every `analog` driver from freshness. That would have
   passed the wrong analog: a 9/11 week for a Taiwan semiconductor shock. MacroRAG now serves
   **market-episode cards** (`doc_type: "market_episode"`): realized moves over the 5 and 10
   trading days after named stress episodes, measured from FRED, each chunk carrying its exact
   horizon. A card's `release_date` is the end of its measurement window, so its age says nothing
   about whether it applies. The exemption belongs to the **cited chunk's `doc_type`**, not to the
   field's `grounding_mode`.
3. **MacroRAG's horizon check never runs for faultline.** MacroRAG drops a field whose evidence
   horizon is more than 3× away from the requested horizon (`horizon_mismatch`), but only when the
   request carries `synthesis.horizon_days`. faultline's synthesize request sends only `question`,
   `filters` and `mode`, so the check is skipped.
4. **Hard validation codes depend on MacroRAG's deployment config.** MacroRAG drops fields with
   `implausible_magnitude`, `unit_scale_mismatch` or `horizon_mismatch` only when its
   `synthesis.drop_invalid` is on. The committed local config turns it on; the AWS config does not.
   Otherwise the field arrives annotated (`"validation": [...]`) and faultline uses it anyway.

When this change is done:

1. **Required macro is enforced per scenario in the dual-RAG path.** If a scenario's `arag.json`
   entry lists `"macro"` in `required_services` (case-insensitive) and its macro legs are not
   applied, `DualRagEnrichmentProvider.definition_for` raises `ScenarioGenerationError` for that
   scenario, with `reason` set to one of:
   - `macro_synthesis_failed`: the synthesis call raised (HTTP error, timeout, parse failure);
   - `macro_unavailable`: the response had no usable shock-vector fields (none at all, all dropped
     under Requirement 5, or none that maps to a canonical factor), or was refused;
   - `macro_gate_failed`: the freshness/citation gate failed; `detail` contains the gate's findings;
   - `macro_not_configured`: there is no macro provider, or `macro.json` has no entry for the
     scenario.
   The exception propagates as it does for ARag failures today, so the runner stops that scenario
   and continues with the others. It is raised for every tier of that scenario (the macro
   resolution is cached per scenario).
2. **Non-required macro keeps its graceful fallback.** A scenario whose `required_services` does not
   list `"macro"`, or that has no `arag.json` entry, still falls back to its static drivers exactly
   as today (`used_macro=False`, no exception). Plain `--macro` mode
   (`MacroScenarioProvider.resolve` / `definition_for`, used without the dual-RAG provider) never
   raises for these failures and behaves exactly as today.
3. **Field provenance is parsed and carried.** For each shock field, faultline reads from the
   response:
   - `grounding_mode`: `"direct"` when missing; any value other than `"analog"` is treated as
     `"direct"`;
   - `source_horizon_days`: an int, or `None` when missing or not an integer;
   - `validation`: a list of code strings, `[]` when missing.
   All three travel to every canonical driver mapped from that field and appear in the driver's
   provenance: `DriverResolution.sources[<factor>]["grounding_mode" | "source_horizon_days" |
   "validation"]` for every MacroRAG-sourced leg. `grounding_mode` is provenance only; the gate does
   not use it.
4. **Market-episode chunks are exempt from the freshness check only.** For each cited chunk whose
   `doc_type` is exactly `"market_episode"`, the gate skips the "older than `freshness_days`"
   comparison. All other chunks, including those cited by `analog` fields, are checked exactly as
   today. Every other gate rule still applies to every driver: at least `min_chunks_per_driver`
   citations, and each cited chunk present in the response's `chunks` and dated (an undated
   `market_episode` chunk is still a finding).
5. **Fields with a hard validation code are dropped.** A shock field whose `validation` contains
   `implausible_magnitude`, `unit_scale_mismatch` or `horizon_mismatch` is treated as if absent
   from the response: it yields no canonical driver, is not gated, and is not applied (so, for
   example, a dropped `IG_OAS` lets `HY_OAS` map to `credit`). Any other code (e.g.
   `horizon_unknown`) keeps the field. If no usable field remains, Requirement 1's
   `macro_unavailable` applies (and, when macro is not required, the static fallback).
6. **The synthesis request carries the scenario horizon.** Every synthesize request body that
   `MacroScenarioProvider` sends includes `"synthesis": {"horizon_days": <h>}`, where `h` is the
   scenario's `horizon_days` in `scenarios.json` (10 when absent). The existing `question`,
   `filters` and `mode: "synthesize"` keys are unchanged.

Out of scope: adding documents to the MacroRAG corpus; the gate's handling of chunks dated after
the valuation date; checking a field's sign against the scenario direction (a candidate MacroRAG
spec); sending `synthesis.fields`; the LLM flow (`src/scenariogen/llm/`, which has its own grounding
gate); the runner's existing per-scenario stop handling; and `freshness_days` itself. The wording of
`macro.json`'s `synthesis_question`s and filters is config, changed outside this spec.

## Acceptance criteria

- **C1**: With a scenario that requires `macro`, `DualRagEnrichmentProvider.definition_for` raises
  `ScenarioGenerationError` whose `scenario` is that scenario and whose `reason` is
  `macro_synthesis_failed` when the MacroRAG transport raises, `macro_unavailable` when the response
  has an empty `shock_vector` or every field carries a hard validation code, `macro_gate_failed` when
  a field cites only stale non-episode chunks (with every finding string in `detail`, whatever the
  field's `grounding_mode`), and `macro_not_configured` when the dual provider has no macro
  provider. It raises on every severity tier. Oracle: Requirement 1, applied to synthetic responses
  built in the test.
- **C2**: With the same failures, a scenario that does not require `macro` (its `arag.json`
  `required_services` lacks `"macro"`, or it has no `arag.json` entry) gets a definition built from
  its static drivers, with `used_macro` false and no exception; and `MacroScenarioProvider` used on
  its own (`resolve`, `definition_for`) never raises for any of the failures, returning the static
  drivers. Oracle: Requirement 2; the static drivers are the scenario's `scenarios.json` drivers,
  converted with `to_canonical`.
- **C3**: The defect case (USDTWD +5 pct citing two chunks, VIX +10 pts and UST_10Y −30 bp each
  citing one, all dated 2025-02-26, valuation date 2026-10-06, `freshness_days` 550) fails the gate
  with exactly one stale finding per (driver, cited chunk) pair, 4 in all, whether the fields are
  `analog`, `direct` or unmarked, when the chunks are not `market_episode`. The same response with
  every chunk's `doc_type` `"market_episode"` passes, and the macro legs are applied (`used_macro`
  true, the canonical moves are those of the shock vector). In general a finding is a (driver,
  cited chunk) pair whose chunk is older than 550 days and not `market_episode`. Oracle:
  Requirement 4; ages computed by hand (2026-10-06 − 2025-02-26 = 587 days).
- **C4**: Drivers citing `market_episode` chunks still fail the other gate rules: no citations
  (when required), a cited chunk absent from `chunks`, or an undated cited chunk (including an
  undated episode card). A driver citing one stale episode card and one stale non-episode chunk gets
  exactly one finding, for the non-episode chunk. Oracle: Requirement 4.
- **C5**: Every MacroRAG-sourced leg in `DriverResolution.sources` carries `grounding_mode`
  (`"analog"` or `"direct"`; missing or unknown → `"direct"`), `source_horizon_days` (the field's
  int, or `None`) and `validation` (the field's list, or `[]`), including every tenor of a parallel
  `UST` field. Oracle: Requirement 3.
- **C6**: A field whose `validation` contains a hard code yields no driver, no gate finding and no
  applied leg; its static driver (if any) stays; a field with only soft codes is applied. A dropped
  `IG_OAS` with a valid `HY_OAS` puts the HY move on `credit`. A stale non-episode citation on a
  dropped field causes no finding. Oracle: Requirement 5, by hand.
- **C7**: The body `MacroScenarioProvider` posts to `/query` for a scenario contains
  `synthesis.horizon_days` equal to that scenario's `scenarios.json` `horizon_days` (10 for
  `taiwan_geopolitical`, `ai_efficiency_shock` and `rate_capital`; 21 for `keyman_nvda` in the committed config),
  together with `mode: "synthesize"`, the configured question and filters. Oracle: Requirement 6 and
  the committed `scenarios.json`.

## Interface

No new interface. Existing signatures, unchanged and exercised as they are:

- `src.scenariogen.dualrag.DualRagEnrichmentProvider(*, macro_provider=None, arag_provider=None,
  news_resolution=None, crowding_resolution=None, project_root=None)` with
  `definition_for(name, *, severity="adverse", severity_scale=1.0) -> tuple[ScenarioDefinition,
  DualResolution]`
- `src.scenariogen.macro.provider.MacroScenarioProvider(config, *, as_of, client=None,
  project_root=None)` with `resolve(name) -> DriverResolution` and `definition_for(...)`
- `src.scenariogen.macro.client.MacroRAGClient` over a transport with `get(path)` / `post(path,
  payload)` (the I/O boundary tests may fake)
- `src.scenariogen.macro.gate.MacroGate(as_of, *, freshness_days=120, min_chunks_per_driver=1,
  require_citations=True)` with `check(drivers, chunks) -> MacroGateResult`
- `src.scenariogen.macro.models.MacroSynthesis.from_response(d)`,
  `src.scenariogen.macro.mapping.to_canonical_drivers(fields, move_base=100.0)`
- `src.scenariogen.arag.provider.AragCompanyProvider(config, *, as_of, client=None, ...)` (see its
  signature) over an ARag client whose transport tests may fake
- `src.scenariogen.errors.ScenarioGenerationError(scenario, reason, detail="")`

The implementer adds whatever fields this needs (e.g. `grounding_mode`, `source_horizon_days`,
`validation` on `MacroShockField` / `MacroDriver`; a keyword for the horizon on
`MacroRAGClient.synthesize`; a fallback reason on `DriverResolution`); none of that is locked.
The Requirement 5 drop happens no later than `to_canonical_drivers` (in
`MacroSynthesis.from_response` or in the mapping, the implementer's choice): the tests observe it
through `MacroSynthesis.from_response` → `to_canonical_drivers` → `MacroGate.check` and through
`MacroScenarioProvider`.

## Notes

- Fakes only at the HTTP transport of `MacroRAGClient` and of the ARag client (as in
  `tests/scenariogen/test_macro_provider.py` / `test_arag_provider.py`). Never fake
  `MacroScenarioProvider`, `AragCompanyProvider`, `MacroGate` or `DualRagEnrichmentProvider`.
- Configs: tests may use the committed `configs/scenariogen/{macro,arag,scenarios}.json` (read via
  `load_macro_config()` etc. and `project_root=get_project_root()`), or synthetic config dicts. In the
  committed `arag.json`, `taiwan_geopolitical`, `ai_efficiency_shock` and `keyman_nvda` require
  `["macro", "arag"]`, and `rate_capital` requires `["macro"]`. For C2's "does not require macro"
  case, build a synthetic `arag.json`-shaped dict (or a scenario without an entry) rather than
  editing committed config. When a test needs the ARag side to succeed, fake its transport to return
  grounded results, or use a scenario/config where ARag is not required.
- MacroRAG synthesis response shape (`POST /query`, mode synthesize; MacroRAG
  `src/synthesis/schema.py`): `{"shock_vector": {"<FIELD>": {"value": float, "unit":
  "bp"|"pct"|"pts"|"rel", "chunk_ids": [...], "grounding_mode": "direct"|"analog", "rationale":
  "...", "validation": ["<code>", ...], "source_horizon_days": int}}, "chunks": [{"chunk_id": "...",
  "release_date": "YYYY-MM-DD", "doc_type": "...", ...}], "world_view_id": "...", "summary": "..."}`.
  `validation` and `source_horizon_days` are omitted when empty/undeclared. Hard codes are MacroRAG's
  `HARD_VALIDATION_CODES` (`src/synthesis/validate.py`). Request options (MacroRAG
  `SynthesisOptions`): `"synthesis": {"horizon_days": int, "fields": [...], "scenario": str}`, all
  optional.
- Canonical conversion (for expected moves): IR/credit bp × 1e-4; FX pct / 100; VIX pts / 100;
  MOVE pts / `move_base` (rel as-is). `UST_10Y` maps to `ir:DGS10`; a parallel `UST` maps to every
  IR tenor node.
- All data synthetic; no network, no `data/`, no current date (pass `as_of` explicitly).
- Updated 2026-10-30 after the scenario set changed (retired spec; tests kept in step): `ai_bust` is gone,
  so `ai_efficiency_shock` takes its place; `keyman_nvda`'s horizon is 21 days (options-shock-sizing). Only
  `taiwan_geopolitical`, `rate_capital` and `keyman_nvda` still have authored drivers, so the static-fallback
  property (C2) runs on those three.
