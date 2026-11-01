# grounding-id-keys — only chunk and fact ids count as citation ids

## Requirement

`src.scenariogen.llm.grounding.collect_chunk_ids` harvests the ids a tool output carries. The
grounding gate (spec `llm-grounding-gate`) refuses a scenario whose legs cite an id that no MCP tool
returned during the run. Today a string under the generic key `id` counts as a returned id.

That key is wrong (a P3 review finding). Neither sister service uses `id` for citable
evidence: MacroRAG's chunk ids are under `chunk_id` / `chunk_ids`, and ARag's are under
`chunk_id` (narrative) and `fact_id` (facts). ARag does emit `id` for plan-step ids such as
`"api_screen"` or `"Revenue-2025"` (`XbrlRag src/api/v1/service.py`). A leg citing such a string
would pass the gate's `unseen_citation` check, although it is not evidence.

When this change is done, `collect_chunk_ids` returns exactly every string value under a key named
`chunk_id` or `fact_id`, and every string element of a list under a key named `chunk_ids` or
`fact_ids`, at any depth of nested objects and lists; the empty set if the output is not valid
JSON. A value under `id` (or any other key) is not collected. This supersedes the key list in
`llm-grounding-gate` C1; every other rule of that spec is unchanged.

The graduated `llm-grounding-gate` tests that planted ids under `id` are changed in this spec PR so
that they no longer assert anything about the `id` key (they pass both before and after the
implementation); the new behaviour is pinned by this spec's tests.

Out of scope: scoping seen ids per scenario (rejected in review: Mode 1 candidates legitimately cite
the shared evidence phase's chunks), and any other gate rule.

## Acceptance criteria

- **C1**: `collect_chunk_ids` does not return a string that appears in the output only as a value
  under the key `id`, at any depth, while still returning every `chunk_id` / `fact_id` /
  `chunk_ids` / `fact_ids` id in the same document. Oracle: the Requirement's definition, applied by
  hand to JSON documents built in the test, including an ARag-shaped plan step
  (`{"id": "Revenue-2025", "op": "lookup", ...}`) next to a facts row carrying
  `"citation": {"fact_id": ...}`.
- **C2**: `assess_grounding` reports `unseen_citation` for a scenario whose only problem is a leg
  citing a string that the run's tool outputs carried only under `id`, when `seen_ids` is built with
  `collect_chunk_ids` over those outputs. Oracle: Requirement plus `llm-grounding-gate`
  Requirement 3 (`unseen_citation`), applied by hand.

## Interface

No new interface. Existing signatures, unchanged:

- `src.scenariogen.llm.grounding.collect_chunk_ids(tool_output: str) -> frozenset[str]`
- `src.scenariogen.llm.grounding.assess_grounding(calibrated: CalibratedScenario, *, synthesis:
  dict[str, Any], seen_ids: frozenset[str]) -> GroundingVerdict`
- `src.scenariogen.llm.schema.CalibratedScenario`, `DriverLeg`, `Citation`

## Notes

- All data synthetic, built in the test; no network, no `data/`, no current date.
- A grounded synthesis for C2 (so no other gate rule fires): `{"shock_vector": {"USDTWD": {"value":
  8.0, "unit": "pct", "chunk_ids": ["c1"]}}, "world_view_id": "macro-2026-10-01", "chunks":
  [{"chunk_id": "c1"}], "synthesis_diagnostics": {"coverage_gap": false}}`; every leg cited; no
  verification block (so `all_legs_unsupported` cannot fire).
- Graduated-test edits allowed in this PR (tests/spec/llm-grounding-gate/test_grounding_rules.py):
  drop the `{"id": "ctx-77"}` and `{"id": "i-9"}` plantings and their expectations, and remove `"id"`
  from the property test's sampled id keys. Do not otherwise change those tests.
