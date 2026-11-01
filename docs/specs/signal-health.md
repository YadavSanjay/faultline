# signal-health — per-source coverage, staleness and holds for each stress run

## Requirement

Step 15d of `docs/design/scenario_set_and_ablation_design.md` §9 (G10: no signal-health view, so a
source can degrade silently). The 2026-10-18 dual-RAG run showed the problem: MacroRAG stopped four
of ten scenarios, option data was ten days ahead of the run, and nothing in the report said which
sources were healthy. This change gives every stress run a signal-health block: one status per
source (MacroRAG, XbrlRag, news, crowding, options, GPR, throughput), rolled up into an overall
status.

The pure logic lives in a new module `src/scenariogen/signal_health.py`.

When this change is done:

1. **Source status.** `source_status(source, *, as_of, latest, max_staleness_days, covered,
   expected, holds)` returns a dict with keys `source`, `latest`, `staleness_days`, `stale`,
   `covered`, `expected`, `coverage`, `holds`, `status`:
   - `latest` is the newest date the source's data carries (`None` when the source is not dated);
     `latest` is reported as an ISO string or `None`;
   - `staleness_days = (as_of − latest).days`, or `None` when `latest` is `None`;
   - `stale` is true when `staleness_days > max_staleness_days`, false when `latest` is `None`;
   - `coverage = covered / expected` when `expected > 0`, else `None`;
   - `holds` is a dict reason → count over the `holds` list (in order of first appearance);
   - `status` is `"unused"` when `expected == 0`; else `"down"` when `covered == 0`; else
     `"degraded"` when `stale`, `covered < expected` or `holds` is non-empty; else `"ok"`.

   It raises `ValueError` for `covered < 0`, `expected < 0`, `covered > expected`,
   `max_staleness_days < 0`, or `latest` after `as_of`.

2. **Roll-up.** `signal_health(statuses)` returns `{"overall", "counts", "sources"}`:
   - `overall` is the worst status among the statuses that are not `"unused"`, by
     `down > degraded > ok`; `"unused"` when every status is `"unused"` or there are none;
   - `counts` maps each of `ok`, `degraded`, `down`, `unused` to its number of sources (all four keys
     always present);
   - `sources` lists the statuses ordered worst first (`down`, `degraded`, `ok`, `unused`), then by
     `source` name.

   It raises `ValueError` for two statuses with the same `source`.

3. **Wiring** (covered by the implementer in `tests/unit/`).
   - **Config** `configs/scenariogen/signal_health.json`: `max_staleness_days` per source (MacroRAG
     and XbrlRag undated; news 30; crowding 30; options 30; GPR 60; throughput undated).
   - **Collection** in the stress runner, from what the run already resolved: MacroRAG — scenarios
     that require macro vs those whose macro legs applied, holds = fallback reasons; XbrlRag — company
     legs configured vs resolved for the scenarios run, holds = stop reasons; news — the newest 8-K
     date, configured tickers vs names with events in the window, a hold when the news gate is held;
     crowding — the newest settlement date, names vs reliable names; options — the newest PIT
     snapshot or index date, book option underliers vs names with PIT data; GPR — the series date
     used, tagged scenarios vs resolved; throughput — tagged scenarios vs applied.
   - **Report** `StressReport.signal_health` (serialised as `"signal_health"`), one log line per
     source that is not `ok` or `unused`, and a Prometheus gauge `scenariogen_signal_status{source}`
     (0 ok, 1 degraded, 2 down, -1 unused).

Out of scope: the BFF and UI view (step 15f), alerting, and marketdata feed freshness (the post-ingest
DQ gate already covers it).

## Acceptance criteria

- **C1**: `source_status` returns the hand-built dict for each status (`ok`, `degraded` by
  staleness, by coverage and by holds, `down`, `unused`), for an undated source, and at the staleness
  boundary (equal to the limit is not stale), and raises `ValueError` for each invalid input of
  Requirement 1. Properties: `coverage`, when not `None`, lies in `[0, 1]`; `status` is `"ok"` only
  when nothing in Requirement 1 makes it worse. Oracle: Requirement 1 by hand.
- **C2**: `signal_health` returns the hand-built roll-up for mixed statuses, for all-`unused` and for
  an empty list, always with all four count keys, and raises `ValueError` for a duplicate source.
  Properties: the counts sum to the number of sources; `overall` is the worst non-`unused` status;
  shuffling the input never changes the output. Oracle: Requirement 2 by hand.

## Interface

```python
# src/scenariogen/signal_health.py (new)
def source_status(source: str, *, as_of: date, latest: date | None, max_staleness_days: int,
                  covered: int, expected: int, holds: Sequence[str]) -> dict[str, Any]
def signal_health(statuses: Sequence[Mapping[str, Any]]) -> dict[str, Any]
```

## Notes

- Oracles are hand calculations on synthetic inputs. No `data/`, network or current date.
