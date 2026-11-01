# panel-gap-carry — carry a factor's level across short gaps in the aligned risk panel

## Requirement

Track E (item 3) of the 2026-10-18 fix list (decisions 2026-10-19). `FactorHistory._aligned_moves`
builds the date-aligned factor-move panel that the full-revaluation VaR replays and scenariogen's Σ
estimates on: it joins every factor's daily moves on the union of their dates and drops any date where
**one** factor is missing (`dropna(how="any")`). Over the current 750-date window (2023-12-04 →
2026-10-06) that drops 171 dates. About 43 are market holidays and weekend dates (correctly dropped).
About 125 are trading days lost to single-factor holes:

- seven FRED Treasury tenors (`ir:DGS1MO/3MO/6MO/1/3/7/20`) are blank on ~76 business days that the
  other factors have (gaps of 1–5 days; decision: leave the data as it is);
- `fxrate:JPY` (BoJ TONAR) is the only missing factor on 44 days (Japanese holidays).

Decision: carry a factor's **level** across a short gap (at most 3 business days), i.e. a zero move on
the missing day; the next observed move already spans the gap, because each factor's moves are
computed on its own observed dates. A date stays eligible only when most factors observed it, so
weekend rows and full market holidays are still dropped.

When this change is done:

1. **Carry short gaps.** `factor_history.carry_short_gaps(panel, *, max_gap, min_coverage)` takes the
   outer-joined move panel (rows = the union of dates, ascending; columns = factors; `NaN` = the factor
   has no move that date) and returns the panel to align on:
   - **eligible rows**: the rows where the share of non-`NaN` columns is `>= min_coverage` (share =
     observed columns / all columns); every other row is removed;
   - on the eligible rows, for each column, a run of consecutive `NaN`s of length `<= max_gap` that has
     an observed value both before and after it (an interior run) is filled with `0.0`; longer runs and
     runs at the start or end of the column stay `NaN`;
   - row order and the column order are preserved; filled values are exactly `0.0`; observed values are
     unchanged.

   It does **not** drop the rows that still have a `NaN` (the caller's `dropna` does). It raises
   `ValueError` when `max_gap < 0`, when `min_coverage` is not in `(0, 1]`, or when the index is not
   strictly increasing. An empty panel (no rows) is returned as is. The input is not mutated.
   `max_gap = 0` fills nothing (only the eligibility filter applies).

2. **Wiring** (covered by the implementer in `tests/unit/` / `tests/pricer/`).
   - `FactorHistory._aligned_moves` applies `carry_short_gaps` between the `concat` and the
     `dropna(how="any")`, with `max_gap` and `min_coverage` from validated pricer config (new keys
     `risk.panel_max_gap_days`, default 3, and `risk.panel_min_coverage`, default 0.75, in the pydantic
     config model; a mistyped key raises `ConfigError`). `max_gap_days = 0` restores today's panel
     exactly (the eligibility filter only removes rows that had a `NaN` anyway).
   - The panel's provenance (where `aligned_panel` / the VaR result records its window) reports the
     number of carried cells per factor in the window.
   - Golden PVs are unaffected (they are PVs, not VaR); any test that pins a VaR or Σ number computed
     from real data and moves because of this change is reported, not silently re-pinned.

Out of scope: repairing `fred_rates.csv` (decision: leave the data), longer gaps, interpolation, the
per-factor `factor_series` (each factor's own dates, unchanged), and the forward-scoring log.

## Acceptance criteria

- **C1**: `carry_short_gaps` returns the hand-built panel for a synthetic 4-column panel with: a weekend
  row observed by one column only (removed by eligibility), a full-holiday row (removed), a 1-row and a
  3-row interior gap (filled with 0.0), a 4-row interior gap (left `NaN` with `max_gap = 3`), and a
  leading and a trailing gap (left `NaN`); returns the panel unchanged except for ineligible rows with
  `max_gap = 0`; returns an empty panel unchanged; and raises `ValueError` for `max_gap = -1`,
  `min_coverage` of `0` and `1.5`, and a non-increasing index. Properties: every observed value of an
  eligible row is unchanged; every filled cell is exactly `0.0` and lies in an interior run of length
  `<= max_gap`; raising `max_gap` never un-fills a cell; raising `min_coverage` never adds a row; the
  output rows are a subset of the input rows in the same order. Oracle: Requirement 1 by hand.

## Interface

```python
# src/pricer/risk/factor_history.py (new module-level function)
def carry_short_gaps(panel: pd.DataFrame, *, max_gap: int, min_coverage: float) -> pd.DataFrame
```

## Notes

- Oracles are hand calculations on a synthetic panel. No `data/`, network or current date.
- A carried day understates that factor's daily vol slightly (one zero move, then a two-day move); with
  `max_gap = 3` and the current data that touches at most ~120 cells of seven tenors and ~44 of
  `fxrate:JPY` in a 750-day window.
- With `min_coverage = 0.75` and 44 factors, a date with up to 11 missing factors stays eligible: the
  Treasury-hole dates (7 missing) and the Japanese-holiday dates (1 missing) do; US market holidays
  (all equities missing) and weekend rows do not.
- Clarifications (from writing the tests): gap runs are measured on the eligible rows only, after the
  ineligible rows are removed (a gap straddling a removed holiday row counts only its eligible rows; an
  ineligible row observed by the column does not split its run); eligibility is `>= min_coverage`; a
  repeated date makes the index not strictly increasing (`ValueError`); the order of checks for an empty
  panel with invalid parameters is not specified.
