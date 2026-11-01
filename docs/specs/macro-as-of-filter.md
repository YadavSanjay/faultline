# macro-as-of-filter — MacroRAG retrieval pinned to the run's as-of date

## Requirement

Step 14a of `docs/design/scenario_set_and_ablation_design.md` §9 (gap "MacroRAG: no guard against
chunks dated after `as_of`", §5.3; G4). MacroRAG already filters retrieval on a chunk's
`release_date` through `filters.date_from` / `filters.date_to` (it applies them as a range in its
vector store, and drops undated chunks when a date filter is set). Faultline sends `date_to: null`,
so a run as of 2026-10-06 can retrieve documents published later: today the corpus holds two Fed
documents dated 2026-10-07 and 2026-10-13. Of 306 chunked documents, 303 carry a `release_date`,
including the four `market_episode` cards, so pinning costs almost no evidence.

This change caps every MacroRAG synthesis request's `date_to` at the run's `as_of` date, so the
macro legs are point-in-time and a past as-of (the event replay of step 14b) cannot see the future.

When this change is done:

1. **Point-in-time filter.** `macro.models.point_in_time_filter(filters, as_of)` returns a new
   `MacroFilter` equal to `filters` except for `date_to`:
   - `date_to` is `as_of.isoformat()` when `filters.date_to` is `None` or later than `as_of`;
   - an earlier or equal `filters.date_to` is kept as given;
   - every other field (tiers, buckets, doc types, entity and factor tags, publishers, `date_from`)
     is unchanged, and `filters` itself is not mutated.

   It raises `ValueError` when `filters.date_from` is later than `as_of` (a window that ends before it
   starts), or when `date_from` or `date_to` is not an ISO date (`YYYY-MM-DD`; only the first 10
   characters are read). Dates compare as dates.

2. **Wiring** (covered by the implementer in `tests/unit/`).
   - `MacroScenarioProvider._synthesize` passes `point_in_time_filter(MacroFilter.from_config(...),
     as_of)` to the client, with the provider's `as_of` (the run's valuation date).
   - The scenario's dual-RAG provenance records the `date_to` sent.
   - A `ValueError` from a misconfigured scenario filter stops that scenario fail-loud with reason
     `macro_filter_invalid`, like the other macro failures, rather than falling back silently.

Out of scope: the agentic LLM flow's MCP calls to MacroRAG, XbrlRag's as-of (step 14b), changes in
the MacroRAG repo (none needed), and golden PVs (pricing is untouched).

## Acceptance criteria

- **C1**: `point_in_time_filter` sets `date_to` to `as_of` when it is `None` or later, keeps an
  earlier or equal `date_to`, leaves every other field equal to the input and does not mutate it
  (exact equality), and raises `ValueError` for `date_from` after `as_of` and for non-ISO `date_from`
  / `date_to`. Properties: the result's `date_to` is never after `as_of`; applying the function twice
  equals applying it once; the result does not depend on the input's `date_to` once that is on or
  after `as_of`. Oracle: Requirement 1 by hand.

## Interface

```python
# src/scenariogen/macro/models.py (new function)
def point_in_time_filter(filters: MacroFilter, as_of: date) -> MacroFilter
```

`MacroFilter` is the existing dataclass in `src/scenariogen/macro/models.py`.

## Notes

- Oracles are hand-built `MacroFilter` instances. No `data/`, network or current date.
- Context, not an oracle: MacroRAG's `/query` merges request `filters` into its retrieval filter and
  its Qdrant store applies `release_date` in `[date_from, date_to]` (MacroRAG
  `src/vectorstores/qdrant_store.py`, `src/retriever/analysis.py`).
