# scenario-historical-replay — replay real episodes, then re-derive them with today's sources

## Requirement

The approved scenario set (`docs/design/scenario_set_and_ablation_design.md` §4) anchors most
scenarios to real episodes. A replay applies the factor moves that actually happened. The enriched
version runs the same scenario through the four sources, so the gap between the two is what the
intelligence contributes (§4.1). This change adds the replay method and the new scenario set, and
fixes the credit anchor found in the first anchored run. There, raw Moody's BAA10Y (+168bp record
over 10 days) anchored the IG-OAS `credit` factor, and the gate held two tiers out.

**How a replay works.** A replay scenario's authored drivers are the episode's observed factor
moves, so no new generator method is needed:
- the conditional-covariance generator keeps the observed factors as drivers and fills only the
  factors without data from Σ;
- the authored-only composition (no layers) is the replay baseline;
- a dual-RAG run is the enriched version, re-derived with today's sources;
- the ablation's base step is the replay, so the ablation attributes the gap layer by layer.

When this change is done:

1. **Episode moves.** `episode_moves(series, start, end, factors=None)` returns each factor's
   realised move over its rows dated strictly after `start` and on or before `end`:
   - `series` maps factor key → daily-move series in canonical units, indexed by date;
   - equity and FX (`eq:`, `fx:`) moves compound, `Π(1 + r) − 1`; every other factor sums;
   - a factor with no rows in the window, or a missing value in it, is left out;
   - `factors`, when given, restricts the keys considered;
   - `start ≥ end` raises `ValueError`.
2. **Trading days.** `trading_days(series, start, end)` counts the rows of one series dated strictly
   after `start` and on or before `end`. A replay's `horizon_days` is this count for the market
   factor `eq:XLK`. `start ≥ end` raises `ValueError`.
3. **Idiosyncratic labels.** `replay_drivers(moves, idiosyncratic)` returns `(drivers, excluded)`:
   - `drivers` holds the moves of every factor not labelled idiosyncratic;
   - `excluded` holds the labelled ones that had a move.

   A labelled factor without a move is ignored. A labelled company move (for example INTC's
   earnings miss inside the yen-carry window) is therefore not replayed as part of the episode:
   Σ fills that factor from the others, and the observed move is reported separately.
4. **Volatility-matched proxy.** `vol_matched(proxy, target, *, min_overlap=250)` returns
   `(proxy × β, β)` with `β = σ_target / σ_proxy`:
   - both are sample standard deviations (ddof 1) over the dates the two daily-move series share;
   - the whole proxy series is scaled, not spliced;
   - fewer than `min_overlap` shared dates, or `σ_proxy = 0`, raises `ValueError`.

   The `credit` anchor uses BAA10Y moves scaled this way to the IG-OAS daily moves
   (`BAMLC0A0CM`, the series the `credit` factor is built from), the same treatment the risk engine
   gives its credit backfill.
5. **Wiring** (covered by the implementer in `tests/unit/`).
   - **Replay specs.** A scenario with
     `"replay": {"start", "end", "idiosyncratic": {factor: reason}}` in `scenarios.json` gets its
     drivers and horizon from the episode when the catalog loads:
     - the moves come from `FactorHistory.factor_series(include_style=True)`, restricted to the
       factors in Σ;
     - its `method` is `conditional_covariance`;
     - its `overlays` stay as authored;
     - an episode the on-disk history doesn't cover is fail-loud (`ScenarioGenerationError` for
       that scenario).

     The materialized provenance carries `replay`: window, `horizon_days`, the observed factor
     count, and the excluded idiosyncratic moves with their reasons.
   - **Credit anchor.** `configs/scenariogen/long_history.json` maps `credit` to BAA10Y matched to
     IG OAS (`vol_matched`). The anchor book records `β`.
   - **The scenario set.** `scenarios.json` becomes the default set (§4.2, without #8 and #10, which
     follow in later changes):

     | # | Scenario | Kind | Window / note | Lead |
     |---|---|---|---|---|
     | 1 | `ai_deflation` | replay | 2021-11-29 → 2022-10-14 (multi-month) | `eq:XLK` down |
     | 2 | `ai_efficiency_shock` | replay | 2025-01-24 → 2025-01-27 | `eq:NVDA` down |
     | 3 | `taiwan_geopolitical` | authored, kept | — | `eq:TSM` down |
     | 4 | `tariff_shock` | replay | 2025-04-02 → 2025-04-08 | `eq:XLK` down |
     | 5 | `crowded_unwind` | replay | 2024-07-10 → 2024-08-05; `eq:INTC` labelled idiosyncratic | `fx:JPY` down |
     | 6 | `inflation_repricing` | replay | 2022-08-16 → 2022-10-14 | `ir:DGS10` up |
     | 7 | `rate_capital` | authored, reworked | bear steepener (2Y +50bp, 10Y +100bp, 30Y +150bp) plus a +30bp swap-spread overlay | `ir:DGS10` up |
     | 9 | `ai_rotation` | replay | 2024-07-10 → 2024-07-24 | `eq:SMH` down |
     | 10 | `keyman_nvda` | authored, interim until #10 | — | `eq:NVDA` down |

     `ai_bust` (replaced by #1), `real_rate_repricing` (replaced by #6) and `funding_basis_squeeze`
     leave the default set:
     - `momentum_crash`, `semis_dispersion` and `funding_basis_squeeze` move to a `sensitivities`
       block in `scenarios.json`, which `ScenarioCatalog.names()` doesn't list;
     - the stress runner runs them only with `--sensitivities`;
     - every new scenario gets `macro.json` and `arag.json` entries (synthesis question, fields and
       directions; company legs), so its enriched version uses the sources.

Out of scope: the AI-capex credit scenario (#8) and issuer credit factors; option-sized #10; crisis
Σ; BFF/UI.

## Acceptance criteria

- **C1**: `episode_moves` returns the compounded (`eq:`/`fx:`) or summed (other) moves over the rows
  in `(start, end]` (rel 1e-12):
  - a row dated `start` is excluded and a row dated `end` included;
  - a factor with a missing value or no rows in the window is left out;
  - `factors` restricts the result;
  - `start ≥ end` raises `ValueError`.

  Property: splitting a window at an interior date and combining the two parts (compounding or
  summing) gives the whole window's move. Oracle: Requirement 1 by hand; window additivity as a
  property.
- **C2**: `trading_days` counts the rows in `(start, end]`, and raises `ValueError` for
  `start ≥ end`. Property: the counts of two adjacent windows add up. Oracle: Requirement 2 by hand;
  additivity as a property.
- **C3**: `replay_drivers` splits the moves into non-labelled drivers and labelled excluded moves,
  and ignores labels without a move. Property: the two outputs partition the input's keys and keep
  its values. Oracle: Requirement 3 by hand; the partition as a property.
- **C4**: `vol_matched` returns `proxy × β` over all proxy dates, with
  `β = std(target) / std(proxy)` (ddof 1) over the shared dates (rel 1e-12). It raises `ValueError`
  below `min_overlap` and for a constant proxy. Properties:
  - after scaling, the proxy's standard deviation over the shared dates equals the target's;
  - scaling the target by `c > 0` scales `β` by `c`.

  Oracle: Requirement 4 by hand; the properties above.

## Interface

New module `src/scenariogen/replay.py`:

- `src.scenariogen.replay.episode_moves(series: Mapping[str, pd.Series], start: date, end: date,
  factors: Sequence[str] | None = None) -> dict[str, float]`
- `src.scenariogen.replay.trading_days(series: pd.Series, start: date, end: date) -> int`
- `src.scenariogen.replay.replay_drivers(moves: Mapping[str, float],
  idiosyncratic: Iterable[str]) -> tuple[dict[str, float], dict[str, float]]`
- `src.scenariogen.replay.vol_matched(proxy: pd.Series, target: pd.Series, *,
  min_overlap: int = 250) -> tuple[pd.Series, float]`

Series indices are dates (`YYYY-MM-DD` strings, `date`s or `Timestamp`s) sorted ascending, and are
compared as calendar dates. The implementer defines the catalog wiring, the config content and the
`--sensitivities` flag.

## Notes

- Spec tests use synthetic series and dicts only; no `data/`, services or QuantLib.
- Replay drivers come from data, so a scenario's version changes if the data under its window is
  revised. That is intended: the version content-addresses the economics.
- A multi-month replay is applied as an instantaneous shock to today's book (§3.3); the report says
  so.
