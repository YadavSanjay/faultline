# issuer-scenario-support — issuer credit keys in authoring, and a per-scenario severe-tier covariance

## Requirement

Step 11b of `docs/design/scenario_set_and_ablation_design.md` §9 (scenario #8, AI-capex credit
repricing) needs two things the engine doesn't support yet, found while building #8 as a replay of
Oracle's own 2025-26 CDS repricing (lead `credit:ORCL`):

1. **Authoring rejects issuer keys.** `authoring.validate` accepts `credit` but not
   `credit:<ISSUER>` (added by `issuer-credit-factors`) in drivers, overlays or a replay's
   idiosyncratic labels.
2. **The severe tier can't use a crisis covariance for an issuer event.** The severe tier
   materializes through the crisis Σ (2022 windows, `crisis-sigma-tiers`). In those windows the issuer
   factors are pure backfill, exact multiples of `credit`. Conditioning on an ORCL-only widening then
   reads as a systemic credit crisis (IG about +640bp), and the tier fails the plausibility gate.
   An issuer event needs the production Σ, where the issuer has its own history.

When this change is done:

1. **Authoring accepts issuer keys.** `authoring.validate(doc)` accepts every key for which
   `factor_keys.is_issuer_credit(key)` holds wherever it accepts a factor key: a scenario's
   `drivers`, its `overlays`, and its `replay.idiosyncratic` labels. Every key it rejected before
   that is not an issuer key is still rejected with the same messages: for example `credit:`,
   `creditx:ORCL` and `bogus`. Everything else about validation is unchanged.

2. **Severe-tier covariance source.** `catalog.severe_covariance_source(spec)` returns the source of
   the covariance a scenario's severe tier materializes with: `spec["severity"]["severe_sigma"]`
   when the scenario has a `severity` object holding that key, else `"crisis"`. The value must be
   `"crisis"` or `"production"`; anything else (including a non-string) raises `ConfigError`
   (`src.common.config.models.ConfigError`) naming the bad value. A spec without a `severity` object,
   or with a `severity` that is not a mapping, gives `"crisis"`.

3. **Wiring** (covered by the implementer in `tests/unit/`).
   - `ScenarioCatalog.covariance_for(tier, name)` returns the production Σ for the severe tier of a
     scenario whose `severe_covariance_source` is `"production"`, and otherwise behaves as today.
     It never raises the crisis-unavailable error for such a scenario.
   - The stress runner's per-tier generator and plausibility checker, and the ablation runner's
     generator, use the scenario's own covariance (today they are cached or built per tier only).
   - The severity block in the report records which covariance the tier used (`"production"` or
     `"crisis"`).
   - `authoring` passes `severity.severe_sigma` through to `scenarios.json` (the `severity` block
     already passes through whole).

Out of scope: the #8 configuration itself (a config change after this one), the issuer backfill
(unchanged), and golden PVs (pricing is untouched).

## Acceptance criteria

- **C1**: `authoring.validate` accepts a document using `credit:ORCL` and `credit:META` as a driver,
  an overlay and a replay idiosyncratic label (no exception), and still raises `AuthoringError` for
  each of `credit:`, `creditx:ORCL` and `bogus` in each of those three places, with the message naming
  the key. Property: for any name of uppercase letters, a document with `credit:<name>` as a driver
  validates. Oracle: Requirement 1; the issuer-key rule of `factor_keys.is_issuer_credit`.
- **C2**: `severe_covariance_source` returns `"production"` and `"crisis"` for those values, `"crisis"`
  for a spec without `severity`, with `severity` lacking `severe_sigma`, and with a non-mapping
  `severity`; it raises `ConfigError` for `"Production"`, `"none"`, `""`, `1` and `None` as
  `severe_sigma`. Property: the result is always one of the two values or an exception, and adding
  unrelated keys to the spec or the severity block never changes it. Oracle: Requirement 2 by hand.

## Interface

```python
# src/scenariogen/catalog.py (new function)
def severe_covariance_source(spec: Mapping[str, Any]) -> str
```

`authoring.validate` and `AuthoringError` are existing (`src/scenariogen/authoring.py`).

## Notes

- Oracles are hand-built authoring documents and spec dicts. No `data/`, network or current date.
- A minimal valid authoring scenario needs a snake_case name, a non-empty `narrative`, and at least
  one driver, company leg or replay window; `required_services` may be empty.
