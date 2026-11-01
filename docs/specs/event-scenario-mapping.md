# event-scenario-mapping — theme-relevant key events and crowding on every equity leg

## Requirement

Step 9 of `docs/design/scenario_set_and_ablation_design.md` §9 (gaps in §5.3, "Key events" and
"Market intelligence (crowding)"). Today:

- the news multiplier counts every allowlisted 8-K item the same way in every scenario, so one
  name gets the same multiplier in a tariff scenario as in an AI-capex scenario;
- news and crowding scale only XbrlRag company legs; every other equity name in a scenario is
  untouched by them;
- only item 5.02 marks a scenario as live (`keyman_nvda`).

This change makes key events and crowding theme-relevant, reaches every single-name equity driver,
and maps 8-K items to the scenarios they make live. All new logic lives in a new module
`src/scenariogen/events.py`, as pure functions over already-loaded data.

When this change is done:

1. **Config.** `load_events_config(raw, scenarios)` validates a parsed `events.json` against the
   set of scenario ids `scenarios` and returns an `EventsConfig`. The top-level keys are:
   - `description` (any string; ignored), keys starting with `_` (comments; ignored);
   - `item_weights`: 8-K item code → weight, a finite number ≥ 0;
   - `excluded`: tickers never scaled by news or crowding (the ETFs); upper-cased on load;
     optional, default empty;
   - `crowding_min_obs`: an integer ≥ 1; `crowding_max_staleness_days`: an integer ≥ 0;
   - `themes`: scenario id → theme (optional, default no themes). A theme's keys are `news_items` (list of item codes), `tone`
     (bool), `crowding` (bool), `trigger_items` (list of item codes) and `trigger_names` (list of
     tickers, upper-cased on load; empty = any name). Every key is optional; a missing list is
     empty and a missing bool is `false`. Keys starting with `_` are ignored.

   Every scenario id in `scenarios` that has no theme gets the empty theme (no news items, no tone,
   no crowding, no triggers). It raises `ConfigError` (`src.common.config.models.ConfigError`) for:
   an unknown top-level or theme key; a theme for a scenario id not in `scenarios`; an item in
   `news_items` or `trigger_items` that is not a key of `item_weights`; a weight that is negative,
   not a number or not finite; a `tone` or `crowding` that is not a bool; a missing `item_weights`,
   `crowding_min_obs` or `crowding_max_staleness_days`; `crowding_min_obs < 1` or
   `crowding_max_staleness_days < 0`.

2. **Theme event score.** `theme_event_score(events, theme, item_weights, as_of, cfg)` is the
   age-decayed sum, over the events with `0 ≤ age ≤ cfg.window_days` (age in days from
   `filing_date` to `as_of`), of each event's **theme weight**: the sum of `item_weights[i]` over
   the item codes `i` of the event (comma-separated in `item_codes`, each code stripped of
   surrounding whitespace, blanks dropped, each code counted once) that are in `theme.news_items`. Decay is `0.5 ** (age / cfg.decay_halflife_days)`,
   or 1 when `cfg.decay_halflife_days ≤ 0`. It returns `(score, contributing)`: `contributing` is
   the in-window events with at least one theme item, in input order. An event with an unparsable
   date is skipped. `cfg` is `src.scenariogen.news.mapping.NewsMapConfig`; events are
   `src.scenariogen.news.mapping.EventRow`.

3. **Theme news multiplier.** `theme_news_multiplier(ticker, events, tone_z, as_of, cfg, theme,
   item_weights)` returns a `src.scenariogen.news.mapping.NewsLeg` with
   - `multiplier = clamp(1 + cfg.beta_event · score + t, cfg.min_scale, cfg.max_scale)`, where
     `score` comes from Requirement 2 and `t = cfg.beta_tone · (−tone_z)` when `theme.tone` is true
     and `tone_z` is not `None`, else 0;
   - `event_score = score`; `tone_z` = the input when `theme.tone`, else `None`;
   - `n_events` = the number of contributing events; `accessions` = their accessions in order;
     `item_codes` = the theme items found in them, each once, in order of first appearance;
   - `applied` = `multiplier != 1.0` or there is a contributing event or (`theme.tone` and `tone_z`
     is not `None`).

   So a theme with no news items and no tone always yields multiplier exactly 1.0.

4. **Live triggers.** `scenario_triggers(events_by_ticker, as_of, window_days, themes)` returns,
   for every scenario whose theme has `trigger_items`, the list of `TriggerHit(item, ticker,
   filing_date, accession)`: one per (event, item) where the event's ticker is in
   `theme.trigger_names` (or that set is empty), the item is one of the event's item codes and in
   `theme.trigger_items`, and `0 ≤ age ≤ window_days`. Events filed after `as_of`, and events with
   an unparsable date, never count. Item codes are parsed as in Requirement 2.
   Hits are sorted by `(filing_date, ticker, item)`. A scenario with trigger items but no hit maps
   to `[]`; a scenario without trigger items is absent. `filing_date` is the first 10 characters of
   the event's `filing_date`. Ticker matching is case-insensitive; `TriggerHit.ticker` is
   upper-case.

5. **Equity-leg multipliers.** `equity_leg_multipliers(drivers, legs, news_mult, crowding_mult,
   theme, excluded, lead)` returns factor → compound multiplier for the single-name equity drivers
   that are not company legs. A factor `f` qualifies when it is in `drivers`, is an equity factor
   `eq:<T>`, is not in `legs`, is not `lead`, and `T` (upper-cased) is not in `excluded`. Its
   compound is `n · c`, with `n = news_mult.get(T, 1)` if the theme has news (non-empty
   `news_items` or `tone`) else 1, and `c = crowding_mult.get(T, 1)` if `theme.crowding` else 1.
   `news_mult` and `crowding_mult` are keyed by upper-case ticker. Only factors whose compound is
   not exactly 1 are returned.

6. **Driver residual scaling.** `scale_driver_residuals(drivers, systematic, multipliers, *, cap)`
   returns a new driver dict. For each `f` in both `drivers` and `multipliers`, with
   `s = systematic.get(f, 0)` (its co-move from the other drivers) and
   `m' = clamp(multipliers[f], 1/cap, cap)`, the driver becomes `s + (drivers[f] − s) · m'`: only the
   company-specific part is scaled and the factor stays a driver. Other drivers are unchanged; a
   multiplier for a factor that is not a driver is ignored (not validated). It raises `ValueError`
   for `cap < 1` or, for a factor that is a driver, a multiplier that is not finite or is ≤ 0.
   Inputs are not mutated.

7. **Crowding reliability.** `crowding_reliable(settlement_dates, as_of, *, min_obs,
   max_staleness_days)` is true when at least `min_obs` dates are on or before `as_of` and the
   latest of them is at most `max_staleness_days` days before `as_of`. Dates after `as_of` are
   ignored; with none left it is false. Dates are ISO strings; only the first 10 characters are read.

8. **Wiring** (covered by the implementer in `tests/unit/`).
   - **`configs/scenariogen/events.json`** holds `item_weights` equal to the 8-K allowlist in
     `configs/marketdata/news/news_marketdata.json` (a unit test checks they match),
     `excluded = ["XLK", "SMH", "QQQ", "SPX", "GLD"]`, `crowding_min_obs = 24`,
     `crowding_max_staleness_days = 30`, and these themes:

     | Scenario | `news_items` | `tone` | `crowding` | `trigger_items` | `trigger_names` |
     |---|---|---|---|---|---|
     | `ai_deflation` | 2.02, 7.01, 2.06 | true | true | 2.02 | GOOGL, META, MSFT, AMZN, ORCL |
     | `ai_efficiency_shock` | 2.02, 7.01, 8.01 | true | true | — | — |
     | `taiwan_geopolitical` | 7.01, 8.01 | true | false | — | — |
     | `tariff_shock` | 7.01, 8.01 | true | false | — | — |
     | `crowded_unwind` | 2.02 | false | true | — | — |
     | `ai_rotation` | — | false | true | — | — |
     | `keyman_nvda` | 5.02, 4.02, 2.02 | true | false | 5.02, 4.02 | NVDA |

     `inflation_repricing` and `rate_capital` have no theme. Item 2.03 → AI-capex credit (#8) is
     added with that scenario (step 11).
   - **Company legs.** The news multiplier of an XbrlRag leg comes from `theme_news_multiplier` with
     its scenario's theme; its crowding multiplier applies only when the theme has `crowding` and
     `crowding_reliable` holds for that name. The compound cap (`cap_leg_inputs`, 1.5) is unchanged.
   - **Other equity drivers.** In the anchored tier pipeline, after the company-leg step, the
     drivers get `scale_driver_residuals` with `equity_leg_multipliers` (lead = the scenario's
     severity lead; legs = its XbrlRag legs; crowding only for reliable names), `systematic` from
     Σ's conditional mean given the other drivers at scale 1, and `cap` = the severity config's
     `leg_cap`. The news and crowding parts belong to the `news` and `crowding` layers of the
     ablation: with a layer off its multiplier is 1.
   - **Triggers.** `scenario_triggers` replaces `keyman_items`: the stress report and the forward
     log record, per scenario, its trigger hits (`news_items` in `live_triggers` becomes the
     per-scenario hits). `keyman_items` is removed from `news.json`.
   - **Provenance.** Each scenario's dual-RAG provenance records, per scaled name, the theme news
     leg (`NewsLeg.to_dict()`), the crowding leg and whether it was reliable, and the compound
     multiplier applied.
   - **Composition rule.** `anchoring.ANCHORED_RULE` becomes `"dualrag-v3"`.

Out of scope: item 2.03 and scenario #8 (step 11), text-level filters on 8-Ks (the feed carries item
codes only), propagated names with no driver (they have no company-specific part to scale), and
golden PVs (pricing is untouched; scenario results change).

## Acceptance criteria

- **C1**: `load_events_config` returns the parsed config (weights, upper-cased `excluded` and
  `trigger_names`, the two crowding integers, a theme per scenario id with the empty theme for ids
  without one) and raises `ConfigError` for each invalid input listed in Requirement 1. Property:
  adding `_`-prefixed keys at the top level or in a theme never changes the result. Oracle:
  Requirement 1 by hand.
- **C2**: `theme_event_score` equals the hand-computed decayed sum of theme weights (rel 1e-12) and
  returns the contributing events in input order; events outside `[0, window_days]`, without a
  theme item or with an unparsable date add nothing. Properties: the score is 0 for an empty
  `news_items`; adding an event without a theme item leaves the result unchanged; the score is
  additive over a disjoint split of the events. Oracle: Requirement 2 by hand.
- **C3**: `theme_news_multiplier` returns the hand-computed multiplier (rel 1e-12) and the
  `NewsLeg` fields of Requirement 3, including the clamp at both ends and the tone switch.
  Properties: the multiplier lies in `[min_scale, max_scale]`; with no news items and no tone it is
  exactly 1.0 for any events and tone; with `tone` false it does not depend on `tone_z`. Oracle:
  Requirement 3 by hand.
- **C4**: `scenario_triggers` returns the hand-listed hits of Requirement 4, sorted, with `[]` for a
  scenario with trigger items but no hit and no entry for one without trigger items. Properties:
  adding an event filed after `as_of` or outside the window changes nothing; the result does not
  depend on the order of the input events. Oracle: Requirement 4 by hand.
- **C5**: `equity_leg_multipliers` returns exactly the qualifying factors of Requirement 5 with
  compound `n · c` (rel 1e-12), excluding legs, the lead, excluded tickers, non-equity factors and
  compounds of exactly 1, and switching news and crowding off per theme. Property: every returned
  factor is an `eq:` driver not in `legs`, not `lead`, with its ticker not excluded. Oracle:
  Requirement 5 by hand.
- **C6**: `scale_driver_residuals` returns `s + (d − s) · clamp(m, 1/cap, cap)` for scaled drivers
  and the inputs unchanged otherwise (rel 1e-12), raises `ValueError` for `cap < 1` and for a
  multiplier that is ≤ 0 or not finite, and does not mutate its inputs. Properties: with every
  multiplier 1 the drivers are unchanged; when `drivers[f] = s` the driver is unchanged for any
  multiplier; `|d' − s| ≤ cap · |d − s|`. Oracle: Requirement 6 by hand.
- **C7**: `crowding_reliable` returns the hand-checked verdict of Requirement 7 at the `min_obs` and
  staleness boundaries (inclusive). Properties: adding dates after `as_of` never changes it; it is
  monotone (more dates on or before `as_of` never turn true into false when the latest date is
  unchanged). Oracle: Requirement 7 by hand.

## Interface

```python
# src/scenariogen/events.py (new)
class ScenarioTheme(NamedTuple):
    news_items: frozenset[str]
    tone: bool
    crowding: bool
    trigger_items: frozenset[str]
    trigger_names: frozenset[str]

class EventsConfig(NamedTuple):
    item_weights: dict[str, float]
    excluded: frozenset[str]
    crowding_min_obs: int
    crowding_max_staleness_days: int
    themes: dict[str, ScenarioTheme]

class TriggerHit(NamedTuple):
    item: str
    ticker: str
    filing_date: str
    accession: str

def load_events_config(raw: Mapping[str, Any], scenarios: Collection[str]) -> EventsConfig
def theme_event_score(events: Sequence[EventRow], theme: ScenarioTheme,
                      item_weights: Mapping[str, float], as_of: date,
                      cfg: NewsMapConfig) -> tuple[float, list[EventRow]]
def theme_news_multiplier(ticker: str, events: Sequence[EventRow], tone_z: float | None,
                          as_of: date, cfg: NewsMapConfig, theme: ScenarioTheme,
                          item_weights: Mapping[str, float]) -> NewsLeg
def scenario_triggers(events_by_ticker: Mapping[str, Sequence[EventRow]], as_of: date,
                      window_days: int, themes: Mapping[str, ScenarioTheme]
                      ) -> dict[str, list[TriggerHit]]
def equity_leg_multipliers(drivers: Iterable[str], legs: Collection[str],
                           news_mult: Mapping[str, float], crowding_mult: Mapping[str, float],
                           theme: ScenarioTheme, excluded: Collection[str],
                           lead: str | None) -> dict[str, float]
def scale_driver_residuals(drivers: Mapping[str, float], systematic: Mapping[str, float],
                           multipliers: Mapping[str, float], *, cap: float) -> dict[str, float]
def crowding_reliable(settlement_dates: Sequence[str], as_of: date, *, min_obs: int,
                      max_staleness_days: int) -> bool
```

`EventRow`, `NewsLeg` and `NewsMapConfig` are the existing classes in
`src/scenariogen/news/mapping.py`; `ConfigError` is `src/common/config/models.py`'s.

## Notes

- Oracles are hand calculations from the formulas above, on synthetic events (made-up tickers,
  accessions and dates are fine). No `data/` reads, no network, no current date.
- Item weights in examples can be any non-negative numbers; the production values (2.02 = 0.6,
  5.02 = 1.0, 7.01 = 0.2, 8.01 = 0.2, ...) are a convenient source.
- `NewsMapConfig(beta_event=0.15, beta_tone=0.10, window_days=90, decay_halflife_days=30.0,
  min_scale=0.8, max_scale=1.5)` are the production values.
- Equity factor keys are `eq:<TICKER>` (`src/scenariogen/factor_keys.py`: `is_equity`,
  `equity_name`).
