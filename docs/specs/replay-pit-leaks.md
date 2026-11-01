# replay-pit-leaks — close the two look-ahead leaks of the point-in-time event replay

## Requirement

Track D (item 4) of the 2026-10-18 fix list. The event replay (`event_replay_runner`, step 14b) rebuilds
the scenario set as of the day before each historical episode and scores it against what happened
next, so every input must be cut at the episode's `as_of`. `PitFactorHistory` cuts each factor series
at the cutoff, but two inputs still carry later information (both listed as known limitations in the
replay report):

- **Σ regime.** `catalog.ScenarioCatalog.from_config` sets the Ledoit-Wolf shrinkage from the
  implied-correlation regime (`implied_corr.resolve_regime`, F7), which reads the **latest** COR3M
  close, not the one at `as_of`.
- **Issuer credit backfill.** `credit:ORCL` / `credit:META` come from DTCC single-name prints that
  start in 2025. Before the first print the factor is the `credit` (IG) moves scaled by the
  issuer-to-IG level ratio measured **at the first print** (`proportional_backfill`). A replay with
  `as_of` before that print therefore uses a ratio only known later.

When this change is done:

1. **Series as of a date.** `implied_corr.series_as_of(series, as_of)` returns the part of a
   date-indexed series on or before `as_of`:
   - `as_of is None` returns the series unchanged (same values and index, a copy);
   - otherwise the entries whose index date (parsed with `pd.to_datetime`) is `<= as_of`, in the
     original order;
   - it raises `ValueError` when the index cannot be parsed as dates. The input is not mutated.

2. **Issuer factors a replay must drop.** `event_replay.pit_issuer_drops(issuer_info, cutoff)` returns
   the set of factor keys `"credit:<ISSUER>"` (issuer upper-cased) whose DTCC history starts after the
   cutoff: for each issuer in `issuer_info` (the `FactorHistory.issuer_credit_info` mapping, issuer ->
   provenance with `"dtcc_start"` as an ISO date string), the key is dropped when
   `date.fromisoformat(dtcc_start) > cutoff`. An issuer whose provenance has no `dtcc_start`, or an empty
   one, is also dropped (no PIT-clean history can be shown). An issuer with `dtcc_start <= cutoff` is
   kept. Empty `issuer_info` returns an empty set. It raises `ValueError` for a `dtcc_start` that is not
   an ISO date. The input is not mutated.

3. **Wiring** (covered by the implementer in `tests/unit/`).
   - `ImpliedCorrelationRegime.resolve(lookback=None, *, as_of=None)` applies `series_as_of` before the
     lookback tail; `resolve_regime(project_root, config, fixed_shrinkage, *, as_of=None)` passes it on.
     With `as_of=None` both behave exactly as today.
   - `ScenarioCatalog.from_config` passes `as_of = history.cutoff` when the history has a `cutoff`
     attribute (the replay's `PitFactorHistory`), else `None` — so stress, ablation and stability runs
     are unchanged. The resolved `RegimeState.asof` then reports the COR3M date actually used.
   - `PitFactorHistory._collect_series` removes the keys in
     `pit_issuer_drops(self.issuer_credit_info, cutoff)` from the collected series (after the base class
     has filled `issuer_credit_info`), so the replay's Σ and drivers have no issuer factor before its
     DTCC history; a scenario's issuer key then falls back to `credit` exactly as when the factor is
     absent (`factor_vector.vector_to_transforms`).
   - The replay report's `limitations` drops both entries; its provenance records the regime's `asof`
     and the dropped issuer factors per episode.

Out of scope: point-in-time cuts for the stress runner (its data ends at the valuation date), the
Σ estimation itself, and other inputs already cut (`PitFactorHistory`, `pit_long_history`, MacroRAG
`date_to`, XbrlRag `as_of` and fiscal years, PIT option signals).

## Acceptance criteria

- **C1**: `series_as_of` returns the hand-picked entries for a series with string dates and one with a
  `DatetimeIndex` (cut strictly after `as_of`, the `as_of` day itself kept), the whole series for
  `as_of=None`, an empty series when `as_of` precedes every date, and raises `ValueError` for an
  unparseable index. Properties: the result's index is an order-preserving subset of the input's (every kept date
  `<= as_of`, every dropped date `> as_of`); the result never grows when `as_of` moves earlier; the
  input is unchanged. Oracle: Requirement 1 by hand.
- **C2**: `pit_issuer_drops` returns the hand-built set for issuers starting before, on and after the
  cutoff, one with no `dtcc_start` and one with an empty string, a lower-case issuer name (key
  upper-cased), `set()` for empty input, and raises `ValueError` for a malformed date. Properties: an
  issuer is in the result exactly when its start is missing/empty or after the cutoff; moving the
  cutoff later never adds a key. Oracle: Requirement 2 by hand.

## Interface

```python
# src/scenariogen/implied_corr.py (new function)
def series_as_of(series: pd.Series, as_of: date | None) -> pd.Series

# src/scenariogen/event_replay.py (new function)
def pit_issuer_drops(issuer_info: Mapping[str, Mapping[str, Any]], cutoff: date) -> set[str]
```

## Notes

- Oracles are hand calculations on synthetic series and provenance. No `data/`, network or current
  date.
- With the current data, `credit:ORCL` / `credit:META` start in 2025, so every replay episode before
  then drops both; `ai_capex_credit` (the ORCL credit replay, 2025-10-01 → 2026-01-30) already drops
  out of earlier episodes because its window ends after their `as_of`.
- Clarifications (from writing the tests): `series_as_of` keeps the original index labels (strings stay
  strings) and the original order, even for an unsorted index; for `as_of=None` it returns a copy that
  can be written without changing the input. `pit_issuer_drops` treats a `dtcc_start` of `None` like a
  missing one (dropped); issuer names that collide after upper-casing are not specified.
