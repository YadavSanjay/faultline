# cost-per-layer — calls, latency and cost of each enrichment layer next to its Shapley contribution

## Requirement

Step 15e of `docs/design/scenario_set_and_ablation_design.md` §9 (G11: no cost or latency per layer
against its contribution, so value per dollar is unknown). The ablation runner already gives each
layer's exact Shapley contribution to a scenario tier's P&L; the call trace records each MacroRAG,
XbrlRag and LLM call with its latency. Nothing joins the two.

Neither MacroRAG nor XbrlRag reports a cost today (their REST responses carry no usage block), so
their calls are recorded at `$0` and dollar cost per layer cannot be measured. Decision (2026-10-18):
report calls and latency now, with contribution per second of latency; read a cost from a response's
`usage.cost_usd` when a service starts sending one, and report cost as not reported (`None`) until
then. The services are asked separately to add the block; no further faultline change is needed then.

The pure logic lives in a new module `src/scenariogen/layer_cost.py`. Layer ids are
`ablation.LAYERS` = `("macrorag", "xbrlrag_narrative", "xbrlrag_facts", "news", "crowding", "state")`;
`OVERHEAD = "overhead"` collects calls that belong to no layer (the narrative LLM, health probes).

When this change is done:

1. **Call → layer.** `call_layer(service, purpose)` returns:
   - `"macrorag"` when `service == "macrorag"`;
   - `"xbrlrag_narrative"` when `service == "arag"` and `"/narrative/"` is in `purpose`;
   - `"xbrlrag_facts"` when `service == "arag"` and `"/facts/"` is in `purpose`;
   - `"overhead"` otherwise (including `service == "llm"` and any other `arag` path).

   `news`, `crowding` and `state` read local files and make no calls, so no call maps to them.

2. **Reported cost.** `response_cost(response)` returns the cost a service reported in a response:
   - `None` when `response` is not a mapping, has no `"usage"` key, `usage` is not a mapping, or
     `usage` has no `"cost_usd"` key or it is `None`;
   - `float(usage["cost_usd"])` when it is a finite number `>= 0` (an `int` or `float`, not a `bool`);
   - it raises `ValueError` when `cost_usd` is present but negative, NaN, infinite, a `bool` or not
     a number (a service sending a broken cost is a bug, not a missing value).

3. **Usage per scenario and layer.** `layer_usage(records)` takes call records (mappings with at least
   `service`, `purpose`, `ok`, `latency_ms`, `scenario`, `cost_usd`, `cost_reported`) and returns
   `{scenario: {layer: usage}}` where, for every scenario that appears in a record, every layer in
   `LAYERS` and `"overhead"` is present, and `usage` is:
   - `n_calls`: the records of that scenario mapped to that layer by `call_layer`;
   - `n_errors`: those with `ok` false;
   - `latency_s`: the sum of their `latency_ms` / 1000;
   - `n_cost_reported`: those with `cost_reported` true;
   - `cost_usd`: the sum of `cost_usd` over the records with `cost_reported` true, or `None` when
     `n_cost_reported == 0`.

   A layer with no calls has `n_calls = n_errors = n_cost_reported = 0`, `latency_s = 0.0`,
   `cost_usd = None`. No records → `{}`. It raises `ValueError` for a negative `latency_ms` or a
   negative `cost_usd` on a record with `cost_reported` true. The input is not mutated.

4. **Value table.** `layer_value(usage, attributions)` joins `layer_usage` output to the ablation
   records. Each attribution is a mapping with `scenario`, `severity` and `shapley`
   (`{layer: pnl}`; a layer missing from `shapley` counts `0.0`). It returns
   `{"rows", "totals", "overhead"}`:
   - `rows`: for each attribution in input order, then each layer in `LAYERS` order, a dict
     `{"scenario", "severity", "layer", "shapley", "contribution", "n_calls", "latency_s",
     "cost_usd", "contribution_per_s", "contribution_per_usd"}` where `contribution = |shapley|`,
     the usage fields come from `usage[scenario][layer]` (zero calls, `0.0` s and `None` cost when the
     scenario is not in `usage`), `contribution_per_s = contribution / latency_s` when
     `latency_s > 0` else `None`, and `contribution_per_usd = contribution / cost_usd` when
     `cost_usd` is not `None` and `> 0` else `None`;
   - `totals`: for each severity in first-appearance order, then each layer in `LAYERS` order,
     `{"severity", "layer", "contribution", "n_calls", "latency_s", "cost_usd",
     "contribution_per_s", "contribution_per_usd"}`: `contribution` sums the rows' contributions for
     that severity and layer; `n_calls`, `latency_s` and `cost_usd` sum `usage[scenario][layer]` over
     the distinct scenarios of that severity (a scenario's calls are shared by its tiers, so each
     severity counts them once); `cost_usd` is `None` when it is `None` for every one of those
     scenarios, else the sum of the ones that are not; the two ratios as in `rows`;
   - `overhead`: `{"n_calls", "latency_s", "cost_usd"}` summed over every scenario in `usage`
     (`cost_usd` `None` when `None` for all).

   It raises `ValueError` for two attributions with the same `(scenario, severity)`. The inputs are
   not mutated.

5. **Wiring** (covered by the implementer in `tests/unit/`).
   - `trace.capture.CallRecord` gains `cost_reported: bool = False`, serialised by `to_dict` as
     `"cost_reported"`. `record_rest_call` sets `cost_usd` and `cost_reported = True` from
     `response_cost(response)` when it is not `None` (a raised `ValueError` is logged as a warning and
     the call recorded without a cost; tracing never stops a run). `record_llm_call` sets
     `cost_reported = True`.
   - `ablation_runner` binds a trace collector around the run (tagging each scenario as the stress
     runner does), writes the trace sidecar `data/pricer/scenario/<date>/trace/<run_id>.jsonl`, and
     adds `"trace"` (the collector summary) and `"layer_cost"`
     (`layer_value(layer_usage([r.to_dict() for r in records]), <its scenario records>)`) to its
     report. It logs one line per layer of the totals per severity.

Out of scope: adding usage blocks to MacroRAG/XbrlRag (separate repos, handoffs), the stress runner's
report, the BFF and UI (step 15f), and cost budgets or alerts.

## Acceptance criteria

- **C1**: `call_layer` maps each case of Requirement 1 by hand (MacroRAG `/query`, XbrlRag
  `/api/v1/narrative/search`, `/api/v1/facts/lookup`, another XbrlRag path, an LLM call, an unknown
  service). Property: the result is always one of `LAYERS + ("overhead",)` and never `news`,
  `crowding` or `state`. Oracle: Requirement 1 by hand.
- **C2**: `response_cost` returns `None` for each absent case, the value for an `int` and a `float`
  cost (including `0`), and raises `ValueError` for negative, NaN, infinite, `bool` and string costs.
  Property: for any finite number `>= 0` it returns that number as a float. Oracle: Requirement 2 by
  hand.
- **C3**: `layer_usage` returns the hand-built dict for a mixed record list (two scenarios, calls
  on several layers, an error, reported and unreported costs, an overhead call), `{}` for no records,
  and raises `ValueError` for a negative latency or a negative reported cost. Properties: every
  scenario has all seven keys; the `n_calls` of a scenario sum to its number of records; `latency_s`
  sums to the scenario's total latency / 1000; splitting a record list in two and merging the counts
  gives the same result (order does not matter). Oracle: Requirement 3 by hand.
- **C4**: `layer_value` returns the hand-built rows, totals and overhead for two scenarios at two
  severities (a layer with reported cost, layers without calls, a missing `shapley` key, a scenario
  absent from `usage`), and raises `ValueError` for a duplicate `(scenario, severity)`. Properties:
  every ratio is `None` exactly when its denominator is `None` or `0`; per severity and layer the
  total contribution equals the sum of the rows' contributions; adding a second severity of the same
  scenario never changes the first severity's totals of calls, latency and cost. Oracle:
  Requirement 4 by hand.

## Interface

```python
# src/scenariogen/layer_cost.py (new)
OVERHEAD: str = "overhead"
def call_layer(service: str, purpose: str) -> str
def response_cost(response: Any) -> float | None
def layer_usage(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]
def layer_value(usage: Mapping[str, Mapping[str, Mapping[str, Any]]],
                attributions: Sequence[Mapping[str, Any]]) -> dict[str, Any]
```

`LAYERS` is imported from `src/scenariogen/ablation.py`. The stub gate allows only functions and
imports in a new stub module, so the implementer adds the `OVERHEAD` constant; the tests use the
literal `"overhead"`.

## Notes

- Oracles are hand calculations on synthetic records and attributions. No `data/`, network or current
  date.
- MacroRAG answers can come from its answer cache (`"cached": true` in the response), which makes a
  call fast and understates the layer's latency; step 15c's `no_cache` flag removes that for
  stability runs. This change reports latency as recorded.
- Clarifications (from writing the tests):
  - `call_layer` matching is case-sensitive; a purpose containing both `/narrative/` and `/facts/`
    maps to `xbrlrag_narrative` (checked first).
  - `response_cost` accepts any `int` or `float` instance (subclasses such as numpy floats included,
    `bool` excluded); other numeric types (`Decimal`, `Fraction`) raise `ValueError`. A `usage` that
    is not a mapping (a list, a number, `None`) is absent, not an error.
  - `layer_usage`: a record with `cost_reported` false has its `cost_usd` ignored, even when negative;
    a record whose `scenario` is missing or `None` is grouped under `""`; a reported cost of `0.0`
    gives `cost_usd = 0.0`, not `None`.
  - `layer_value`: a scenario with attributions but absent from `usage` adds 0 calls, `0.0` s and no
    cost to the totals as well as the rows; `overhead` includes scenarios in `usage` with no
    attribution; keys in `shapley` outside `LAYERS` are ignored; `layer_value({}, [])` returns
    `{"rows": [], "totals": [], "overhead": {"n_calls": 0, "latency_s": 0.0, "cost_usd": None}}`.
- Contribution uses `|shapley|`: a layer that moves the loss either way changes the number, and the
  value table is about how much each layer moves it per unit of latency or cost.
