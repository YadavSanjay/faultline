# gate-findings-recorded — record why the plausibility gate held a scenario tier out

## Requirement

Step 15b of `docs/design/scenario_set_and_ablation_design.md` §9 (G10, silent degradation). When the
plausibility gate holds a scenario tier out of a stress run, the runner logs only the number of
findings (`GATE FAIL v=… -> held out (10 finding(s))`) and the stress report omits the tier. In the
2026-10-18 dual-RAG run, `ai_deflation` was held out at every tier and the cause could not be
recovered afterwards. This change records each held-out tier and its findings in the report and the
log.

When this change is done:

1. **Held-out entry.** `report.held_out_entry(scenario, severity, version, result)` returns
   `{"scenario", "severity", "scenario_version", "status", "findings"}` for a
   `plausibility.PlausibilityResult`: `status` is `result.status`; `findings` is
   `[f.as_dict() for f in result.findings]` ordered with `"fail"` findings first, then `"flag"`, then
   any other severity, keeping their original order within each group. The input is not mutated.

2. **Log line.** `report.held_out_summary(entry, limit=5)` returns one line for the log:
   `"<n_fail> fail / <n> finding(s): "` (counted from `entry["findings"]`: `n_fail` the findings
   whose `severity` is `"fail"`) followed by up to `limit` findings from `entry["findings"]`
   in their order, each as `"<factor> <check> <observed> vs <limit>"` with `observed` and `limit`
   formatted `:.4g`, joined by `"; "`, and `" (+<k> more)"` when `k > 0` findings are left out.
   `limit < 1` raises `ValueError`.

3. **Wiring** (covered by the implementer in `tests/unit/`).
   - `StressReport` gains `held_out: list[dict]` (default empty), serialised by `to_dict` as
     `"held_out"`.
   - When the gate holds a tier out, the runner appends `held_out_entry(...)` to the report and logs
     `held_out_summary(...)` on the existing `GATE FAIL` warning line.

Out of scope: changing the gate, its bounds or what counts as promotable; the BFF and UI (step 15d).

## Acceptance criteria

- **C1**: `held_out_entry` returns the hand-built dict for a result mixing fail, flag and other
  findings (fails first, then flags, then others, stable within groups), for a result with no
  findings (`findings == []`), and leaves the result unchanged. Property: the entry's findings are a
  permutation of `[f.as_dict() for f in result.findings]`, and no `"fail"` follows a non-`"fail"`.
  Oracle: Requirement 1 by hand.
- **C2**: `held_out_summary` returns the hand-written line for entries with fewer, exactly `limit`
  and more than `limit` findings (with the `(+k more)` suffix only in the last case), and raises
  `ValueError` for `limit < 1`. Property: the line starts with `"<n_fail> fail / <n> finding(s)"`
  for the entry's counts, and has at most `limit` `"; "`-separated finding parts. Oracle:
  Requirement 2 by hand.

## Interface

```python
# src/scenariogen/report.py (new functions)
def held_out_entry(scenario: str, severity: str, version: str, result: PlausibilityResult) -> dict[str, Any]
def held_out_summary(entry: Mapping[str, Any], limit: int = 5) -> str
```

`PlausibilityResult` and `PlausibilityFinding` are in `src/scenariogen/plausibility.py`
(`PlausibilityFinding(factor, check, observed, limit, severity)`, `as_dict()`).

## Notes

- Oracles are hand-built `PlausibilityResult` / `PlausibilityFinding` instances. No `data/`.
