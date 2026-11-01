# var-panel-window — the sensitivity VaR replays the full-revaluation panel's dates

## Requirement

The risk report carries two historical-simulation VaR families over the same lookback (750 days):

- the **full-revaluation** VaR (`src.pricer.risk.historical_var.FullRevalVarEngine`) replays the
  date-aligned factor-move panel `FactorHistory.aligned_panel(lookback)`. Since `panel-gap-carry`
  that panel carries a factor's level across short gaps (`carry_short_gaps`), and its 750-date window
  starts 2023-09-22;
- the **sensitivity** family (`src.pricer.risk.var.VarEngine.historical_var`: the Δγ headline, the
  component-ES decomposition, the parametric cross-check, filtered HS and stressed VaR) builds its own
  panel from its own per-factor P&L columns with `pd.concat(cols, axis=1).dropna(how="any")`, without
  the gap carry, so its window still starts 2022-12-20.

The two therefore replay different days (their daily P&L vectors correlate at −0.08 instead of
1.00), and the risk page's "closeness of Δγ VaR to full-revaluation VaR is the P&L-attribution
coverage check" compares unrelated samples. The sensitivity family uses a different factor set (41
key-rate, credit-bucket, vol-index, equity and FX factors from its own config `risk.var.factors`), so
applying the same gap rule to its own columns would not guarantee the same dates.

Decision: the sensitivity family replays **the full-revaluation panel's dates**. Its per-factor P&L
columns are put onto that date window, short gaps carried the same way, and only window dates where
some column still has no value are dropped.

When this change is done:

1. **Align to a window.** `var.align_to_window(cols, window, *, max_gap)` takes per-factor columns
   (each a date-indexed `pd.Series` of daily P&L contributions; a column may have dates outside the
   window and may lack window dates) and a strictly increasing `window` of dates, and returns a
   `pd.DataFrame`:
   - one column per entry of `cols`, named by its key, in the iteration order of `cols`;
   - built on the `window` rows: each column takes its value on each window date (a value the column
     has on a date outside the window is not used);
   - on those rows, per column, a run of consecutive missing window rows of length `<= max_gap` that
     has a value both before and after it in the window (an interior run) is filled with `0.0`;
     longer runs and runs at the start or end of the window stay missing;
   - every row that still has a missing value is removed; the remaining rows keep the window's order;
   - observed values are returned unchanged; filled cells are exactly `0.0`; the inputs are not
     mutated.

   It raises `ValueError` when `max_gap < 0` or when `window` is not strictly increasing. An empty
   `cols` returns an empty `DataFrame` (no rows, no columns). `max_gap = 0` fills nothing.

2. **Wiring** (covered by the implementer in `tests/unit/` / `tests/pricer/`).
   - `VarEngine.historical_var` takes its window from `FactorHistory(project_root, config)`:
     the headline Δγ VaR/ES, `decomposition`, `parametric` and filtered HS (`fhs`) use the dates of
     `aligned_panel(lookback)` with `lookback = risk.var.lookback_days`; stressed VaR uses the dates of
     the full aligned history (every row `FactorHistory` aligns, not only the last `lookback`). Each
     applies `align_to_window` to its columns with `max_gap = risk.panel_max_gap_days`, replacing the
     `concat(...).dropna(how="any")` panels. `VarEngine`'s constructor signature and every caller stay
     unchanged.
   - `VarResult.to_dict()` gains `dates` (ISO dates, one per `pnl_vector` entry), and
     `FullRevalVarResult.to_dict()` emits the `dates` it already holds, so the two windows can be
     compared from the stored report.
   - An empty window or no usable column keeps today's behaviour: a result with `n_scenarios = 0` and
     the existing warning.
   - Golden PVs are unaffected (PVs, not VaR). A test that pins a sensitivity-VaR number computed from
     real data and moves because of this change is reported, not silently re-pinned.

Out of scope: changing the sensitivity factor set or its sources, the full-revaluation panel, the
covariance used by scenariogen, and the horizon scaling.

## Acceptance criteria

- **C1**: `align_to_window` returns the hand-built frame for a synthetic case with a window of at
  least eight dates and at least three columns, covering: a column observed on every window date; a
  1-row and a 2-row interior gap (filled with `0.0` when `max_gap = 2`); a 3-row interior gap (left
  missing with `max_gap = 2`, so its rows are dropped); a leading and a trailing gap (rows dropped); a
  column value on a date outside the window (ignored); and column order taken from `cols`. With
  `max_gap = 0` it returns the frame where only fully observed window rows remain. Exact equality of
  values, index and columns. Oracle: hand construction in the test.
- **C2**: Properties, for any columns and window: the returned index is a subset of `window` in
  increasing order; every returned value equals the column's own value on that date or is exactly `0.0`
  where the column had none; the result has no missing values; when every column has a value on every
  window date the result equals the columns placed on the window unchanged; permuting `cols` permutes
  the result's columns and nothing else; raising `max_gap` never removes a row that a smaller
  `max_gap` kept. Oracle: definitions in the requirement (metamorphic and property checks).
- **C3**: Refusals: `ValueError` for `max_gap = -1`, for a window with a repeated date, and for a
  decreasing window. An empty `cols` returns an empty `DataFrame`. Oracle: the requirement.

## Interface

```python
# src/pricer/risk/var.py (new module-level function)
def align_to_window(cols: Mapping[str, pd.Series], window: pd.DatetimeIndex, *, max_gap: int) -> pd.DataFrame
```

## Notes

- Oracles are hand calculations on synthetic series. No `data/`, network or current date.
- A carried cell is a zero P&L contribution on that day (a zero factor move), which is what the
  full-revaluation panel replays for the same gap, so the two families see the same days and the same
  carry rule. A column's value on a date outside the window is dropped rather than added to the next
  window date; the full-revaluation panel drops such dates the same way.
- Gap runs are measured on window rows only (consecutive positions in `window`), not on calendar days.
- Clarification (from writing the tests): a `NaN` value in a column counts as missing on that date,
  the same as a date the column does not have, as in `carry_short_gaps`. The locked tests model
  missing values as absent dates only.
- Real-data acceptance, reported in the implementation PR rather than locked: after
  `python -m src.pricer.risk_runner` at the default date, the stored `var.dates` equal the
  `full_reval_var.dates` (or the dropped dates are listed), `var.n_scenarios` equals
  `full_reval_var.n_scenarios` up to those drops, and the two `pnl_vector`s correlate above 0.95.
