# runner-output-root — write pricer run outputs to a chosen root, not always the project's data/

## Requirement

The batch pricer runner (`src.pricer.runner.run`), the governance runner
(`src.pricer.governance_runner.run_governance`) and the pricing service (`src.pricer.service.create_app`)
each take one root, `get_project_root()`, and use it both to **read** inputs (market data under
`data/marketdata/`, git provenance) and to **write** outputs (under `data/pricer/`). Tests that call them
therefore write into the real store. One run of `tests/pricer/test_engine.py`, `test_parallel.py`,
`test_service.py` and `test_governance.py` (measured 2026-10-19) writes 21 files under `data/pricer/`:
four partial 4-trade "runs" (store parquet for runs and results, spans), per-trade PV JSON for
`TRADE_22..25`, a governance report, the snapshot manifest, and it **overwrites**
`PV/summary_2026-05-29.csv` and `PV/quality_2026-05-29.json` with 4-trade versions. The UI then shows
those partial runs as the newest pricer runs.

When this change is done, each of the three entry points has a variant that reads inputs from an
**input root** and writes every output under an **output root**:

1. **Batch runner.** `runner.run_to(output_root, *, input_root=None, asset_class=None, trade_id=None,
   valuation_date=None, valuation_type=None, workers=1, book=None) -> list[dict]` prices exactly as
   `run` does today, with the same arguments and return value (the per-trade summary rows), except:
   - market data, the market-data snapshot and git provenance come from `input_root` (default: the
     project root), resolved through `storage.data_base(input_root)` as today;
   - **every** file it writes (per-trade PV JSON, `PV/summary_<date>.csv`, `PV/quality_<date>.json`,
     the snapshot manifest, the portfolio-version store, the columnar results and runs store, the run
     manifest and metrics files, the spans file) is written under `storage.data_base(output_root)`,
     at the same path relative to that base as `run` writes today relative to
     `storage.data_base(<project root>)`;
   - PV-drift detection reads prior marks from the **output** store (an empty output root has no prior
     marks, so no drift alerts);
   - nothing is created, modified or deleted under `input_root`.
2. **Governance runner.** `governance_runner.run_governance_to(output_root, *, input_root=None,
   asset_class=None, valuation_date=None) -> GovernanceReport` behaves as `run_governance` with the same
   input/output split: inputs from `input_root`, the governance report written under
   `storage.data_base(output_root)` at the same relative path as today
   (`pricer/governance/<date>/governance_<run_id>.json`), nothing written under `input_root`.
3. **Service.** `service.create_app_at(output_root, *, input_root=None) -> FastAPI` builds the same app as
   `create_app`, with the engine reading inputs from `input_root` and every store the app reads or writes
   (results, runs/manifests, portfolio versions, VaR/risk outputs) rooted at `output_root`. So after
   `run_to(out, input_root=inp, ...)`, an app from `create_app_at(out, input_root=inp)` serves those
   results from `/results/...`, and an app on a fresh, empty output root serves none.
4. **Defaults unchanged.** `run(...)`, `run_governance(...)` and `create_app()` keep their signatures and
   behave exactly as today: they are equivalent to the new variants with `output_root` and `input_root`
   both the project root. With `PRICER_DATA_URI` set (or AWS mode), `storage.data_base` already returns
   the configured data root for any root; that rule is unchanged.

**Wiring** (implementer, covered in `tests/pricer/` / `tests/unit/`): the four test files above call the
new variants with `output_root=tmp_path` (and `input_root` left at the project root, so they still read
the real market data), so a full `pytest` run writes nothing under `data/`. `run`, `run_governance` and
`create_app` become thin delegations to the new variants.

Out of scope: moving where market-data ingest writes, the risk/scenariogen/sizing runners, S3 behaviour,
and cleaning up runs already written to `data/pricer/` (the human does that).

## Acceptance criteria

All criteria run with `PRICER_DATA_URI` unset and not in AWS mode, on an `input_root` that is a
temporary copy of a committed minimal market-data fixture (enough for one cash-equity trade, e.g.
`TRADE_1`, to price `ok`) and an `output_root` that is a separate, empty temporary directory.

- **C1**: After `run_to(output_root, input_root=input_root, trade_id=<the fixture trade>,
  valuation_date=<the fixture date>)`, the set of files under `input_root` and each file's bytes are
  exactly as before the call (no file created, modified or deleted). Properties: the same holds for any
  `trade_id` subset of the fixture's trades and for `workers` of 1 and 2. Oracle: Requirement 1
  (invariant).
- **C2**: That run writes, under `<output_root>/data/pricer/`: `PV/<asset_class>/<trade_id>/<date>.json`
  for the priced trade, `PV/summary_<date>.csv` (one row per selected trade), `PV/quality_<date>.json`,
  at least one parquet file under `store/runs/valuation_date=<date>/` and under
  `store/results/valuation_type=PV/valuation_date=<date>/`, and `store/spans/<run_id>.json`, where
  `<run_id>` is the run id recorded in the stored run. The returned rows have one entry per selected
  trade with the trade's id and status `ok`. Metamorphic: two runs into two different empty output
  roots produce the same set of relative paths except for names that embed the run id, and the same
  PV for the trade (exactly equal: same inputs, same code). Oracle: Requirement 1 and the output layout
  it names (by hand).
- **C3**: A second `run_to` into the same output root for the same date leaves two runs in that output
  store (both run ids are readable through `ResultStore` on `output_root`), while an independent empty
  output root still has no runs. Oracle: Requirement 1 (writes are routed per output root).
- **C4**: `run_governance_to(output_root, input_root=input_root, asset_class=<the fixture trade's asset
  class>, valuation_date=<the fixture date>)` returns a `GovernanceReport` and writes exactly one
  `pricer/governance/<date>/governance_<run_id>.json` under `<output_root>/data/`, whose run id matches
  the report's; `input_root` is byte-identical before and after (as C1). Whether governance *passes* on
  the fixture is not asserted. Oracle: Requirement 2.
- **C5**: After `run_to(output_root, input_root=input_root, ...)` for the fixture trade and date,
  `create_app_at(output_root, input_root=input_root)` served through FastAPI's `TestClient` lists that
  trade from `GET /results/run/<date>`; `create_app_at(<another empty dir>, input_root=input_root)` lists
  no trades for that date (empty list or a not-found response, as `create_app` does today for a date with
  no runs). `input_root` is byte-identical before and after (as C1). Oracle: Requirement 3.

C1–C5 each need a point test and a property or metamorphic test, per the rubric.

## Interface

```python
# src/pricer/runner.py (new module-level function)
def run_to(output_root: Path, *, input_root: Path | None = None, asset_class: str | None = None,
           trade_id: str | None = None, valuation_date: date | None = None,
           valuation_type: str | None = None, workers: int = 1, book: str | None = None) -> list[dict]

# src/pricer/governance_runner.py (new module-level function)
def run_governance_to(output_root: Path, *, input_root: Path | None = None,
                      asset_class: str | None = None,
                      valuation_date: date | None = None) -> GovernanceReport

# src/pricer/service.py (new module-level function)
def create_app_at(output_root: Path, *, input_root: Path | None = None) -> FastAPI
```

## Notes

- The minimal fixture is synthetic or a few rows of public end-of-day prices, committed under the spec's
  test folder; the spec author picks the trade and date (a cash-equity trade is the cheapest to price:
  check which market-data files its pricer and the data-quality gate need, and include only those).
  Configs (`configs/pricer/*.json`, the portfolio config) are read from the repo as today; only market
  data comes from `input_root`.
- `storage.data_base(root)` returns `<root>/data` when `PRICER_DATA_URI` is unset and not in AWS mode;
  tests make sure that variable is unset (e.g. `monkeypatch.delenv`).
- `service.py` builds a module-level `app = create_app()` at import, which reads the real project
  data; spec tests import only `create_app_at`, so importing `src.pricer.service` must not fail when the
  project's `data/` is absent — if it does, raise it in a dispute rather than working around it.
- Expected values come from the requirement (paths, counts, invariants), never from running the code.
