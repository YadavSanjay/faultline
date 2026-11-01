# llm-grounding-gate — refuse to price or publish an ungrounded LLM scenario

## Requirement

The scenariogen LLM flow (`src/scenariogen/llm/flow.py`, `LLMScenarioFlow.run_hypothetical` and
`run_comprehensive`) runs evidence → propose → calibrate → verify → fact-check → price → narrate →
persist. The runner then writes every scenario in `artifact.calibrated` to a StressReport, which is
what the BFF/UI shows.

The defect, seen in a live run:

1. During calibrate, the model called `macro__macro_synthesize_shock_vector`. The call exceeded the
   MCP call timeout, the client marked the MacroRAG server degraded, and every later MacroRAG call
   was skipped.
2. The model wrote the macro legs anyway. They cite chunk ids (`macro-001`, `macro-002`, `macro-003`)
   that no tool ever returned. It also invented
   `world_view_id = "macro-worldview-missing-2026-10-11 (...)"`, which the flow copied into the
   artifact unchanged.
3. The verify phase marked **all 16 legs UNSUPPORTED**, but its "never drop every leg" guard kept
   them all.
4. The scenario was priced at −$91.5M and published to the UI.

When this change is done:

1. **MacroRAG synthesis is called by code, not left to the model.** Before calibrating each scenario
   proposal, the flow calls `macro__macro_synthesize_shock_vector` once itself, through the tool
   registry, with arguments built from the proposal. The parsed response is passed to the calibrate
   phase as grounding. If this call fails (the tool raises, times out, or returns something that is
   not a JSON object), that scenario is refused. The flow does not fall back to letting the model
   calibrate without it.
2. **`world_view_id` belongs to the code.** Each `CalibratedScenario.world_view_id` is set from the
   `world_view_id` field of that scenario's synthesis response, overwriting anything the model
   emitted. `LLMScenarioArtifact.world_view_id` is taken only from kept scenarios. No placeholder is
   ever filled in. The model is never asked for a world-view id: no message sent to the LLM in the
   calibrate stage contains the substring `world_view_id`, and neither does the `question` argument
   of the synthesize call.
3. **A deterministic grounding gate runs after verify and fact-check, before pricing.** A scenario
   that fails it is refused. It is not passed to `PricingService.price`, it does not appear in
   `artifact.calibrated` (so the runner never publishes it), it is not narrated, and in Mode 1 it is
   not ranked. The gate fails a scenario when any of the following holds:
   - **all_legs_unsupported**: the scenario has at least one leg, and its `verification["verdicts"]`
     mark every leg's factor `UNSUPPORTED` (case-insensitive);
   - **coverage_gap**: the synthesis response's `synthesis_diagnostics.coverage_gap` is `true`, or
     its `shock_vector` is missing or empty;
   - **missing_world_view_id**: the synthesis response has no non-empty string `world_view_id`;
   - **uncited_leg**: some leg has no citations;
   - **unseen_citation**: some leg cites a `chunk_id` that never appeared in any MCP tool output
     returned during this run, up to and including this scenario's calibration.
4. **One refused scenario does not stop the run.** Other themes (Mode 2) and candidates (Mode 1) are
   still processed. A run in which every scenario is refused returns an artifact with an empty
   `calibrated` list, so the runner writes no StressReport. The flow does not raise in that case.

Out of scope (separate spec `mcp-call-timeouts`): per-tool MCP timeouts and per-tool rather than
whole-server degradation. Also out of scope: any change to `verify_scenario`'s own behaviour (the
existing test `test_verify_never_drops_all_legs` stays valid, because the gate reads the verdicts
verify records); MacroRAG's latency; and the fact-check clamps. The implementer decides how a
refusal is recorded (for example a `rejected` list on the manifest with the reason codes, plus an
ERROR log) and covers it with unit tests in `tests/unit/`.

## Acceptance criteria

- **C1**: `collect_chunk_ids(tool_output)` returns exactly the set of ids in that output: every
  string value under a key named `chunk_id`, `fact_id` or `id`, and every string element of a list
  under a key named `chunk_ids` or `fact_ids`, at any depth of nested objects and lists. If the
  output is not valid JSON, the result is the empty set. Oracle: this definition, applied by hand to
  JSON documents built in the test, shaped like the MacroRAG synthesize response
  (`chunks[].chunk_id`, `shock_vector.<field>.chunk_ids`) and the ARag narrative/facts responses
  (`chunk_id`, `citation.fact_id`).
- **C2**: `assess_grounding(calibrated, synthesis=..., seen_ids=...)` returns
  `GroundingVerdict(ok, reasons)`. `reasons` holds exactly the reason codes from Requirement 3
  (`all_legs_unsupported`, `coverage_gap`, `missing_world_view_id`, `uncited_leg`,
  `unseen_citation`) whose conditions hold, each at most once, in that order. `ok` is true if and
  only if `reasons` is empty. Oracle: the rule table in Requirement 3, applied by hand to hand-built
  `CalibratedScenario` and synthesis dicts. Include the defect case: 16 legs all `UNSUPPORTED`, macro
  legs citing `macro-001` that is not among the seen ids, must give `ok=False` with
  `all_legs_unsupported` and `unseen_citation`.
- **C3**: In both flow modes, for each scenario that gets as far as calibration, the MCP tool source
  receives exactly one `macro_synthesize_shock_vector` call made by the code, before the first LLM
  call of that scenario's calibrate stage. Its `question` argument is a non-empty string that does
  not contain `world_view_id`. No message sent to the LLM in the calibrate stage contains
  `world_view_id`. Oracle: the call order recorded by fake MCP and LLM backends sharing one event log
  (the LLM stage can be told apart by the calibrate system prompt's phrase `concrete DRIVER shocks`).
- **C4**: When the synthesis response carries `world_view_id = W` and the model's calibrate JSON
  carries a different value (e.g. `macro-worldview-missing-2026-10-11`), the kept scenario's
  `world_view_id` and `artifact.world_view_id` both equal `W`. Oracle: Requirement 2. The value the
  synthesis fake returns is the only correct source.
- **C5**: When the gate fails for a scenario (each of the five reasons tested separately), or when the
  pre-calibration synthesize call raises, that scenario is not passed to the pricer, is absent from
  `artifact.calibrated`, and (Mode 1) is absent from `artifact.ranking`. Other scenarios in the same
  run that pass are priced and kept. A run whose scenarios are all refused returns normally with an
  empty `artifact.calibrated`. Metamorphic: making one scenario's citations unseen removes exactly
  that scenario from the output and leaves the other scenarios' priced P&L unchanged. Oracle:
  Requirement 3 and Requirement 4; the fake pricer's record of what it was asked to price.

## Interface

New module `src/scenariogen/llm/grounding.py`:

- `src.scenariogen.llm.grounding.GroundingVerdict`: a `typing.NamedTuple` with fields
  `ok: bool` and `reasons: tuple[str, ...]`.
- `src.scenariogen.llm.grounding.collect_chunk_ids(tool_output: str) -> frozenset[str]`
- `src.scenariogen.llm.grounding.assess_grounding(calibrated: CalibratedScenario, *, synthesis:
  dict[str, Any], seen_ids: frozenset[str]) -> GroundingVerdict`

Existing signatures stay unchanged and are exercised as they are:

- `src.scenariogen.llm.flow.LLMScenarioFlow(*, gateway, registry, pricer, store, prompts, settings,
  mcp_settings, portfolio_summary="", portfolio_tickers="", portfolio_asset_classes="")` with
  `run_hypothetical(themes, *, run_id, created_at="")` and `run_comprehensive(*, run_id,
  created_at="")`
- `src.scenariogen.llm.gateway.InstrumentedChatClient(backend, cost_tracker, ...)` over a
  `ChatBackend` whose `create(*, messages, tools=None, **kw) -> RawCompletion`
- `src.scenariogen.llm.mcp.registry.McpToolRegistry(source: ToolSource, specs)` over a `ToolSource`
  with `list_tools(server)` / `call_tool(server, tool, arguments) -> str`
- `src.scenariogen.llm.results.PricingService.price(definition) -> PricedScenario`
- `src.scenariogen.llm.schema.CalibratedScenario`, `DriverLeg`, `Citation`

The implementer adds whatever else the flow needs (the pre-calibration synthesize helper, how
calibrate receives the synthesis, the run-level record of seen ids). None of that is locked.

## Notes

- **Fakes only at I/O boundaries.** The LLM backend (`ChatBackend`), the MCP tool source
  (`ToolSource`) and the pricer (`PricingService`, a Protocol seam whose real implementation needs
  `data/`) may be faked, as in `tests/scenariogen/llm/test_p2_flow.py`. Script the fake backend per
  phase by a phrase from each phase's system prompt: evidence `gathering grounded evidence`, propose
  `designing portfolio-level stress scenarios`, calibrate `concrete DRIVER shocks`, verify and narrate
  by their own prompts in `prompts/scenariogen/`. Use the real `PromptManager` over
  `prompts/scenariogen` and the real `load_llm_settings()` / `load_mcp_settings()` configs (committed
  files), and a `LLMScenarioArtifactStore` rooted at `tmp_path`.
- Verify output is a JSON object `{"verdicts": [{"factor": ..., "verdict": "SUPPORTED" |
  "UNSUPPORTED", "reason": ...}]}`. A test controls the gate's `all_legs_unsupported` input through
  the fake backend's verify reply.
- Response shapes (synthetic, modelled on the real services):
  - MacroRAG synthesize: `{"answer": ..., "chunks": [{"chunk_id": "c1", ...}], "shock_vector":
    {"USDTWD": {"value": 8.0, "unit": "pct", "chunk_ids": ["c1"], "grounding_mode": "direct"}},
    "world_view_id": "macro-2026-10-01", "synthesis_diagnostics": {"outcome": "grounded",
    "coverage_gap": false, "kept_fields": ["USDTWD"], ...}}`. On a coverage gap, `shock_vector` is
    `{}` and `coverage_gap` is `true`.
  - ARag narrative search: `{"chunks": [{"chunk_id": "<uuid>", "text": ..., "section_path": ...}]}`.
    ARag facts: rows carrying `"citation": {"fact_id": "...", ...}`.
- The configured Mode-1 candidate count is `budgets.max_candidates` in
  `configs/scenariogen/llm/llm.json`. A test controls how many candidates exist through the fake
  propose reply.
- All data is synthetic. There is no network, no `data/`, and no current date.
