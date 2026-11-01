# scenario-event-replay — the scenario set as it stood the day before past episodes

## Requirement

Step 14b of `docs/design/scenario_set_and_ablation_design.md` §9 (G4; §7.4 point-in-time event
replay). The question, as agreed: **could the scenario set as it stood the day before an episode
have represented what happened?** The engine is re-run as of the day before three episodes, with
every source pinned to that date, and the scenario vectors are scored against the episode's
realised factor moves. Scenarios whose own replay window ends after the as-of date did not exist
yet and drop out; this removes the circular cases (`crowded_unwind`, `ai_efficiency_shock` and
`tariff_shock` replay exactly these episodes). No pricing: scores are on factor moves.

The new pure logic lives in a new module `src/scenariogen/event_replay.py`; scoring reuses
`forward_log.realised_moves` and `forward_log.score_prediction`.

When this change is done:

1. **Episodes config.** `load_event_replay_config(raw)` validates a parsed
   `configs/scenariogen/event_replay.json` and returns a list of `Episode(name, as_of, horizon_days)`
   in file order. Keys: `description` and `_`-prefixed keys are ignored; `episodes` (required) is a
   non-empty list of objects with `name` (non-empty, unique), `as_of` (ISO date, `YYYY-MM-DD`) and
   `horizon_days` (integer ≥ 1); `min_abs_z` (number > 0, default 1.0) and `sleeves` (object: factor
   key → sleeve name, non-empty strings; default empty) are optional. It raises `ConfigError`
   (`src.common.config.models.ConfigError`) for an unknown key at the top level or in an episode
   (`_`-prefixed keys are ignored at both levels), a missing or empty `episodes` or an `episodes`
   that is not a list of objects, a missing field, a duplicate or empty name, a bad date (including a
   non-string), a `horizon_days` that is not an integer ≥ 1 (a bool is not an integer), a `min_abs_z`
   ≤ 0 or not a number, or a non-string or empty sleeve key or value. `event_replay_settings(raw)`
   validates the whole document the same way and returns `(min_abs_z, sleeves)`.

2. **Scenario availability.** `scenario_available(spec, as_of)` is true for a scenario without a
   `replay` block, and for a replay whose `replay.end` (ISO date) is strictly before `as_of`. It
   raises `ValueError` for a replay block without a parsable `end`.

3. **Point-in-time fiscal years.** `latest_filed_fiscal_years(n, fye_month, fye_day, as_of, *,
   filing_lag_days)` returns, ascending, the `n` most recent fiscal years `Y` whose annual report was
   filed by `as_of`: fiscal year `Y` ends on (year `Y`, `fye_month`, `fye_day`), the day clamped to
   the month's last day, and counts as filed when end + `filing_lag_days` ≤ `as_of`. Fewer than `n`
   qualifying years among the 50 years before `as_of` returns those found. It raises `ValueError`
   for `n < 1`, `fye_month` outside 1–12, `fye_day` outside 1–31, or `filing_lag_days < 0`.

4. **Nearest scenario.** `nearest_scenario(scores)` takes scenario name → a `score_prediction`
   result and returns the name with the highest `cosine`, ignoring `None` cosines; ties go to the
   alphabetically first name; `None` when no scenario has a cosine.

5. **Sleeve direction accuracy.** `sleeve_direction_accuracy(predicted, realised, scale, sleeves, *,
   min_abs_z)` returns sleeve → `{"hits", "n", "rate"}` over the factors that are in all of
   `predicted`, `realised`, `scale` (with `scale > 0`) and `sleeves`, and whose realised z-score
   `|realised / scale| ≥ min_abs_z` (inclusive). A hit is a non-zero prediction with the realised
   move's sign; a zero prediction on a qualifying factor counts in `n` as a miss.
   `rate = hits / n`. A sleeve with no qualifying factor is absent. It raises `ValueError` for
   `min_abs_z ≤ 0`.

6. **Wiring** (covered by the implementer in `tests/unit/`).
   - **Config** `configs/scenariogen/event_replay.json`: `yen_carry` as of 2024-07-10 (horizon 18,
     to 2024-08-05), `deepseek` as of 2025-01-24 (horizon 1, 2025-01-27), `tariff` as of 2025-04-02
     (horizon 4, to 2025-04-08); `sleeves` mapping the book's factors to semis, platforms, power,
     index, rates, credit, vol and fx.
   - **Runner** `python -m src.scenariogen.event_replay_runner [--episode NAME] [--dual-rag]`: per
     episode, the factor history, covariance and long-history anchors are cut at `as_of`; the
     scenarios with `scenario_available` are materialized authored-only (no layers) and, with
     `--dual-rag`, enriched (every layer). Every source is pinned: MacroRAG through
     `macro-as-of-filter`; XbrlRag facts with `as_of` and narrative/fact fiscal years replaced by
     `latest_filed_fiscal_years` (2 for narratives, 1 for a calibration; `filing_lag_days` 90; the
     filer's `fye_month`/`fye_day` from XbrlRag `/companies/{ticker}`); news, crowding, GPR,
     throughput and events resolved as of `as_of`; option signals PIT (none exist before 2026, so
     option-anchored scenarios fall back to history and are flagged); GDELT unused before its
     history starts.
   - **Scores.** Realised moves from each factor's own full series (`realised_moves`, horizon from
     the episode), scale from the as-of covariance (`horizon_scale`); per scenario and version
     (authored, enriched) `score_prediction` plus `sleeve_direction_accuracy`; per episode the
     `nearest_scenario` for each version and the list of scenarios that dropped out with the reason.
   - **Report** `data/scenariogen/event_replay/event_replay_<run_id>.json` through the storage
     façade, provenance-stamped (snapshot ids, `cov_id`, as-of dates, composition rule).

Out of scope: pricing the replays, new data ingest, changing the scenario set, and golden PVs.

## Acceptance criteria

- **C1**: `load_event_replay_config` returns the hand-listed episodes and `event_replay_settings` the
  settings for a valid document (defaults when optional keys are absent), and both raise
  `ConfigError` for each invalid input of Requirement 1. Property: adding `_`-prefixed keys never
  changes the result. Oracle: Requirement 1 by hand.
- **C2**: `scenario_available` is true without a replay, true for `end` before `as_of`, false for
  `end` on or after `as_of`, and raises `ValueError` for a missing or bad `end`. Property: for a fixed
  replay, availability is monotone in `as_of` (once true, true for every later date). Oracle:
  Requirement 2 by hand.
- **C3**: `latest_filed_fiscal_years` returns the hand-computed years for January (NVDA-like), June
  and September fiscal year-ends and for December, at as-of dates on both sides of a filing deadline
  (inclusive), clamps a day 31 in a 30-day or February month, and raises `ValueError` for each invalid
  input. Properties: the result is ascending, has at most `n` entries, every year satisfies the filed
  rule, and moving `as_of` later never removes the latest year. Oracle: Requirement 3 by hand.
- **C4**: `nearest_scenario` returns the hand-picked name, skips `None` cosines, breaks ties
  alphabetically and returns `None` when nothing has a cosine. Property: the result's cosine is the
  maximum over non-`None` cosines. Oracle: Requirement 4 by hand.
- **C5**: `sleeve_direction_accuracy` returns the hand-computed hits, counts and rates for a synthetic
  case with several sleeves, factors below the z threshold, zero predictions, zero or missing scale
  and factors without a sleeve, and raises `ValueError` for `min_abs_z ≤ 0`. Properties: `0 ≤ rate ≤ 1`
  and `hits ≤ n`; scaling `predicted` by a positive constant never changes the result. Oracle:
  Requirement 5 by hand.

## Interface

```python
# src/scenariogen/event_replay.py (new)
class Episode(NamedTuple):
    name: str
    as_of: date
    horizon_days: int

def load_event_replay_config(raw: Mapping[str, Any]) -> list[Episode]
def event_replay_settings(raw: Mapping[str, Any]) -> tuple[float, dict[str, str]]
def scenario_available(spec: Mapping[str, Any], as_of: date) -> bool
def latest_filed_fiscal_years(n: int, fye_month: int, fye_day: int, as_of: date, *,
                              filing_lag_days: int) -> list[int]
def nearest_scenario(scores: Mapping[str, Mapping[str, Any]]) -> str | None
def sleeve_direction_accuracy(predicted: Mapping[str, float], realised: Mapping[str, float],
                              scale: Mapping[str, float], sleeves: Mapping[str, str], *,
                              min_abs_z: float) -> dict[str, dict[str, float]]
```

## Notes

- Oracles are hand calculations on synthetic configs, specs and vectors. No `data/`, network or
  current date.
- Context, not oracles: XbrlRag's fact lookup takes `as_of`; its narrative search takes only
  `fiscal_years` and its chunks carry no filing date, hence Requirement 3. NVDA's fiscal year ends in
  January (`fye_month` 1), MSFT's in June, AAPL's in September.
