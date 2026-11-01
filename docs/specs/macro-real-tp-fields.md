# macro-real-tp-fields — use MacroRAG's real-yield and term-premium legs; pin the hard-code set

## Requirement

`MacroScenarioProvider` (`src/scenariogen/macro/provider.py`) maps MacroRAG shock-vector fields to
canonical factor keys (`to_canonical_drivers`, `src/scenariogen/macro/mapping.py`) and drops fields
that carry a MacroRAG hard validation code (`HARD_VALIDATION_CODES`, `src/scenariogen/macro/models.py`).
MacroRAG's 2026-10-21 hand-off found
three gaps:

1. **A hard code is missing.** MacroRAG's `HARD_VALIDATION_CODES` (`src/synthesis/validate.py`, main
   `ac29d30`) has five codes; faultline's copy lacks `card_value_mismatch` (the field cites a
   market-episode card that measures it, but its value is not that card's largest move). A MacroRAG
   deployment with `synthesis.drop_invalid` off would hand faultline a mis-sized field and faultline
   would apply it.
2. **Two contract fields are discarded.** `REAL_YIELD_10Y` and `TERM_PREMIUM_10Y` (unit `bp`;
   positive = the 10Y TIPS real yield / ACM 10Y term premium rises) are in MacroRAG's shock vocabulary
   and carried on its episode cards, but `to_canonical_drivers` has no branch for them, so they never
   become drivers. faultline already has the target factors in Σ: `real:DFII10` and `tp:ACMTP10`
   (`src/scenariogen/factor_keys.py`, driver-only, decimal daily change = percent change / 100).
3. **Unmapped fields vanish silently.** Nothing in the resolution records that a usable field was
   returned but had no canonical factor, which is why gap 2 went unnoticed.

When this change is done:

1. **The hard-code set equals MacroRAG's.** `HARD_VALIDATION_CODES` is exactly
   `{"implausible_magnitude", "unit_scale_mismatch", "horizon_mismatch", "direction_mismatch",
   "card_value_mismatch"}`. A field whose `validation` contains `card_value_mismatch` (alone or with
   other codes) is dropped like the other hard codes: absent from `MacroSynthesis.fields`, listed in
   `MacroSynthesis.dropped` with its codes, and so producing no driver.
2. **The two fields map, in decimal.** `to_canonical_drivers` maps `REAL_YIELD_10Y` → `real:DFII10`
   and `TERM_PREMIUM_10Y` → `tp:ACMTP10`, each with `move = value × 1e-4` (bp → decimal), carrying the
   field's name (`macro_field`), `chunk_ids`, `rationale`, `grounding_mode`, `source_horizon_days` and
   `validation` like every other mapped field. Every existing mapping is unchanged. These drivers
   are not IR curve nodes, so they never trigger the curve-supersede rule of `macro-curve-coherence`
   (an authored `ir:*` driver is superseded only by a MacroRAG `ir:*` leg). They pass through
   `MacroGate` like any other driver and, when applied, replace an authored driver on the same key.
3. **Unmapped fields are recorded.** `DriverResolution.to_dict()["unmapped_fields"]` lists, sorted
   and without duplicates, the names of the fields in `MacroSynthesis.fields` (i.e. usable, not
   hard-dropped) whose name has no canonical mapping. The mapped names are exactly `UST_2Y`, `UST_5Y`,
   `UST_10Y`, `UST_30Y`, `UST`, `IG_OAS`, `HY_OAS`, `MOVE`, `VIX`, `USDTWD`, `USDJPY`,
   `REAL_YIELD_10Y`, `TERM_PREMIUM_10Y`; any other name is unmapped (e.g. `XCCY_BASIS`, `EQUITY_US`,
   `MOVE_1Y`). An `HY_OAS` that is not used because `IG_OAS` is present is *not* unmapped. The list
   is recorded whenever a synthesis response was received, including when nothing mappable remained
   and the scenario fell back; it is an empty list when every field mapped or no response was
   received. Hard-dropped fields appear only in `dropped_fields`, never in `unmapped_fields`.
4. **The committed config asks for the fields.** In `configs/scenariogen/macro.json`:
   - `rate_capital`: `expect_fields` is `UST_2Y, UST_5Y, UST_10Y, UST_30Y, MOVE, IG_OAS,
     TERM_PREMIUM_10Y` (in that order) and `directions` maps every one of them to `"up"`;
   - `inflation_repricing`: `expect_fields` is `UST_2Y, UST_5Y, UST_10Y, UST_30Y, MOVE, VIX, IG_OAS,
     REAL_YIELD_10Y` (in that order) and `directions` maps every one of them to `"up"`.

   Directions follow MacroRAG's `positive_means` (a term-premium bear steepener raises the term
   premium; a hawkish 2022-style repricing raises the 10Y real yield). The other scenarios'
   `expect_fields` and `directions` are unchanged.

Out of scope: `BREAKEVEN` fields (not in MacroRAG's vocabulary); any rule that removes or rescales a
nominal `ir:DGS10` leg when real-yield or term-premium legs are also set (under the
conditional-covariance engine the drivers are conditioned on jointly, not added); question wording;
the `MOVE_*` term-structure, `EQUITY_US` and `XCCY_BASIS` fields (they stay unmapped and are now
recorded); the agentic LLM path.

## Acceptance criteria

- **C1**: `HARD_VALIDATION_CODES` equals the five-code set of Requirement 1, which equals a committed
  fixture copy of MacroRAG's set. A response field carrying `card_value_mismatch` (alone or with a
  soft code such as `horizon_unknown`) is absent from `MacroSynthesis.fields`, present in
  `MacroSynthesis.dropped` with its codes, and yields no driver through `MacroScenarioProvider.resolve`;
  a field with only soft codes is kept. Oracle: MacroRAG `src/synthesis/validate.py:125` at `ac29d30`
  (fixture copy); Requirement 1.
- **C2**: `to_canonical_drivers` maps `TERM_PREMIUM_10Y` +17 bp to a `tp:ACMTP10` driver of 0.0017 and
  `REAL_YIELD_10Y` +23 bp to a `real:DFII10` driver of 0.0023 (abs 1e-12), with `macro_field`,
  `chunk_ids`, `rationale`, `grounding_mode`, `source_horizon_days` and `validation` carried; for any
  finite value v (bp), the move is v × 1e-4 and scales linearly; adding these fields to a vector
  leaves the drivers of the other fields unchanged. Oracle: MacroRAG unit table (bp) and factor_keys
  convention (decimal), bp × 1e-4.
- **C3**: Through `MacroScenarioProvider.resolve` for `rate_capital` (authored `ir:DGS2` +100,
  `ir:DGS10` +100, `ir:DGS30` +150 bp), a response with only `TERM_PREMIUM_10Y` +17 bp (fresh
  citation) resolves to the three authored IR drivers plus `tp:ACMTP10: 0.0017`, with
  `superseded_static == []` and source origin `macrorag` on `tp:ACMTP10`; a response with `UST_10Y`
  +28 bp and `TERM_PREMIUM_10Y` +17 bp resolves to exactly `{ir:DGS10: 0.0028, tp:ACMTP10: 0.0017}`
  with `superseded_static == ["ir:DGS2", "ir:DGS30"]`. A `TERM_PREMIUM_10Y` citing only an undated or
  missing chunk fails the gate and the scenario falls back to the authored drivers. Oracle:
  Requirements 2 and macro-curve-coherence Requirement 3, by hand.
- **C4**: `DriverResolution.to_dict()["unmapped_fields"]` is the sorted, de-duplicated list of usable
  field names outside the Requirement 3 mapped set: e.g. a response with `UST_10Y`, `XCCY_BASIS`,
  `EQUITY_US`, `MOVE_5Y` gives `["EQUITY_US", "MOVE_5Y", "XCCY_BASIS"]`; with `IG_OAS` and `HY_OAS`
  it is `[]`; a hard-dropped `XCCY_BASIS` is not listed; a response with only `XCCY_BASIS` falls back
  and still lists `["XCCY_BASIS"]`; a failed synthesis call gives `[]`. For any subset of vocabulary
  names, `unmapped_fields` = sorted(subset − mapped set). Oracle: Requirement 3, by hand.
- **C5**: The committed `macro.json` has `rate_capital` and `inflation_repricing` `expect_fields` and
  `directions` exactly as in Requirement 4, and `MacroScenarioProvider` constructs on the committed
  config. Oracle: Requirement 4 (each `synthesis_question` and MacroRAG's `positive_means`).

## Interface

No new public function. Existing signatures, exercised as they are:

- `src.scenariogen.macro.models.HARD_VALIDATION_CODES`, `MacroSynthesis.from_response(d)`,
  `MacroShockField`
- `src.scenariogen.macro.mapping.to_canonical_drivers(fields, move_base=100.0) -> list[MacroDriver]`
- `src.scenariogen.macro.provider.MacroScenarioProvider(config, *, as_of, client=None,
  project_root=None)` with `resolve(name) -> DriverResolution` and `DriverResolution.to_dict()`
- `src.scenariogen.macro.client.MacroRAGClient` over a transport with `get(path)` / `post(path,
  payload)` (the I/O boundary tests may fake)
- `src.scenariogen.factor_keys.REAL_10Y_KEY`, `TERM_PREMIUM_10Y_KEY`

The implementer adds whatever it needs (e.g. an `unmapped_fields` attribute on `DriverResolution`, a
mapped-name constant in `mapping.py`); none of that is locked beyond the `to_dict()` key.

## Notes

- Fakes only at the `MacroRAGClient` transport. Never fake `MacroScenarioProvider`, `MacroGate`, the
  mapping or the models.
- Tests may use the committed configs via `get_project_root()`, synthetic config dicts, or a temporary
  project root holding a synthetic `configs/scenariogen/scenarios.json`.
- Response shape as in `docs/specs/dualrag-macro-gate.md` (Notes); fixtures from
  `tests/spec/macro-curve-coherence/conftest.py` show a working fake transport and response builder.
- Fixture `macrorag_hard_codes.json`: a copy of MacroRAG's `HARD_VALIDATION_CODES` at `ac29d30`
  (`implausible_magnitude`, `unit_scale_mismatch`, `horizon_mismatch`, `direction_mismatch`,
  `card_value_mismatch`).
- MacroRAG vocabulary (`src/synthesis/vocab.py`, 19 fields): `UST_2Y, UST_5Y, UST_10Y, UST_30Y, UST,
  IG_OAS, HY_OAS, MOVE, MOVE_1Y, MOVE_5Y, MOVE_10Y, MOVE_30Y, VIX, EQUITY_US, USDTWD, USDJPY,
  XCCY_BASIS, REAL_YIELD_10Y, TERM_PREMIUM_10Y`.
- All data synthetic; no network, no `data/`, no current date (pass `as_of` explicitly).
