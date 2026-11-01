# issuer-credit-factors — per-issuer credit spread factors for ORCL and META

## Requirement

Step 11a of `docs/design/scenario_set_and_ablation_design.md` §9 (gap G8; §4.3 "issuer-level credit
factors"). Today one factor, `credit` (IG OAS daily change), moves every issuer's bond Z-spread and
CDS par spread by the same amount. The DTCC single-name feed shows the risk the book actually
carries is issuer-specific: ORCL 5Y CDS went from about 36bp (June 2025) to about 246bp (October
2026) while IG OAS stayed near 0.8%, and META from about 68bp to 96bp over 2026. This change adds
per-issuer factors `credit:ORCL` and `credit:META` with their own daily histories, used by
historical VaR, the scenario covariance and scenario repricing. Scenario #8 (AI-capex credit
repricing) is the next change (11b).

Only ORCL and META have single-name DTCC prints. AAPL, MSFT, AMZN and the CDX index stay on the
`credit` factor. Base curves (and so golden PVs) are unchanged: the factors move spreads in risk
and scenario runs only.

The pure logic lives in a new module `src/pricer/risk/issuer_credit.py`, a new transform in
`src/pricer/risk/bumps.py`, and three new key helpers in `src/scenariogen/factor_keys.py`.

When this change is done:

1. **Daily issuer spreads.** `issuer_daily_spreads(prints, *, min_tenor_years, max_tenor_years,
   outlier_ratio, outlier_lookback)` takes a frame with columns `date` (date or ISO string),
   `spread` (decimal) and `tenor_years`, and returns the daily 5Y-bucket spread as a `pd.Series`
   indexed by `pd.Timestamp`, ascending:
   - keep prints with `min_tenor_years ≤ tenor_years ≤ max_tenor_years` and `spread > 0`;
   - take the median spread per date;
   - walk the dates in order. A date's median is **kept** unless at least 3 earlier values are
     already kept and the median lies outside the closed band `[ref / outlier_ratio, ref ·
     outlier_ratio]`, where `ref` is the median of the last `outlier_lookback` kept values (the
     mean of the two middle values for an even count). A dropped date never enters a later `ref`.

   It raises `ValueError` for `outlier_ratio ≤ 1`, `outlier_lookback < 1`, `min_tenor_years >
   max_tenor_years`, or a missing column. No qualifying prints gives an empty series.

2. **Daily moves.** `issuer_spread_moves(daily, calendar, *, max_gap_days)` returns the daily
   additive spread change on the business-day `calendar` (ascending `pd.Timestamp`s):
   - restrict `calendar` to `[first date of daily, last date of daily]`;
   - place each `daily` value on its date (a `daily` date not in `calendar` is ignored) and
     forward-fill the next `max_gap_days` empty calendar dates at most (a longer gap stays partly
     empty after the filled dates, as pandas `ffill(limit=max_gap_days)`);
   - the move on a calendar date is its value minus the previous calendar date's value, where both
     exist after the fill (a filled date has move 0); dates without a move are absent.

   It raises `ValueError` for `max_gap_days < 0`.

3. **Proportional backfill.** `proportional_backfill(moves, proxy_moves, *, issuer_level,
   proxy_level)` extends `moves` backwards with the proxy's moves before the first date of `moves`,
   scaled by `beta = issuer_level / proxy_level` (spread changes proportional to the spread level).
   Dates of `moves` keep their own values; the result is sorted by date. Empty `moves` returns
   `proxy_moves · beta`. It raises `ValueError` for a non-positive or non-finite level.

4. **Issuer-level shift.** `bumps.shift_credit_issuers(shifts, default)` returns a transform that,
   like `shift_credit`, adds a decimal spread change to `CreditSpreadData` and `CdsSpreadData`
   (to `spread` and to every `curve_points` spread), with the change taken per item:
   `shifts.get(name, default)` where `name` is `CreditSpreadData.issuer` or
   `CdsSpreadData.reference`. Other items pass through unchanged. `shift_credit_issuers({}, d)`
   behaves exactly as `shift_credit(d)`.

5. **Factor keys.** `factor_keys.issuer_credit_key(issuer)` returns `"credit:" + issuer.upper()`;
   `factor_keys.is_issuer_credit(key)` is true exactly for keys starting with `"credit:"` followed
   by a non-empty name (case-sensitive prefix); `factor_keys.issuer_credit_name(key)` returns that
   name as written and raises
   `ValueError` for any other key. The plain `credit` key is not an issuer key.

6. **Wiring** (covered by the implementer in `tests/unit/`).
   - **Config.** `pricer_config.json` `risk.hist_var.issuer_credit` lists the issuers with their
     DTCC name filters (the same `names` / `exclude` as the CDS curve build), the tenor bucket
     [4, 6] years, `outlier_ratio` 2.0, `outlier_lookback` 10, `max_gap_days` 5, and the backfill
     proxy (the IG OAS level series `BAMLC0A0CM`).
   - **Factor history.** `FactorHistory` reads the price-forming prints (`NEWT`/`TRAD`, as the curve
     build does) per issuer, builds `credit:<ISSUER>` with Requirements 1–3 (issuer level = the first
     kept daily spread, proxy level = the IG OAS on that date), and labels the backfilled part in the
     factor provenance. `DayShock` carries the issuer moves; its credit transform becomes
     `shift_credit_issuers(issuer moves, default=credit move)`.
   - **Scenarios.** `credit:<ISSUER>` factors enter the covariance with the other factors. In a
     scenario vector, `shift_credit_issuers({issuer: move}, default=vector["credit"])` replaces
     `shift_credit`, so an issuer key overrides `credit` for that issuer's bond and CDS curves.
     Plausibility bounds an issuer key with the `credit` class bound. Replays pick the issuer moves
     up from the history like any other factor.
   - **Reporting.** The factor list and the VaR / backtest reports show the new factors; the
     backfilled span is labelled.

Out of scope: scenario #8 (11b), issuer factors for AAPL / MSFT / AMZN (no CDS prints), per-issuer
CS01 sensitivities, the CDS-bond basis factor, and golden PVs (unchanged).

## Acceptance criteria

- **C1**: `issuer_daily_spreads` returns the hand-computed daily medians on a synthetic frame with
  out-of-bucket tenors, non-positive spreads, several prints per day and outliers on both sides
  (rel 1e-12), including the rule that an outlier never enters a later reference and that the first
  three kept values are never dropped. It raises `ValueError` for each invalid input. Properties:
  adding a print outside the tenor bucket changes nothing; the output dates are a subset of the
  input dates, ascending. Oracle: Requirement 1 by hand.
- **C2**: `issuer_spread_moves` returns the hand-computed moves on a synthetic calendar with gaps
  shorter than, equal to and longer than `max_gap_days` (rel 1e-12), and raises `ValueError` for a
  negative limit. Properties: with no gaps the moves sum to last − first; with `max_gap_days` large
  enough to bridge every gap the moves still sum to last − first. Oracle: Requirement 2 by hand.
- **C3**: `proportional_backfill` returns `proxy · beta` before the first issuer date and the issuer
  moves from it on (rel 1e-12), handles empty `moves`, and raises `ValueError` for bad levels.
  Properties: scaling both levels by the same factor changes nothing; the issuer's own dates are
  never altered. Oracle: Requirement 3 by hand.
- **C4**: `shift_credit_issuers` shifts each `CreditSpreadData` / `CdsSpreadData` by its issuer's or
  reference's own shift or the default, on `spread` and every curve point (rel 1e-12), leaves other
  items unchanged, and with no shifts equals `shift_credit(default)`. Property: applying shifts `a`
  then `b` equals applying `a + b` for every item. Oracle: Requirement 4 by hand.
- **C5**: the key helpers return the hand-listed results for `credit:ORCL`, lower-case input,
  `credit`, `credit:`, `eq:NVDA` and `creditx:ORCL`, and `issuer_credit_name` raises `ValueError`
  for non-issuer keys. Property: `issuer_credit_name(issuer_credit_key(n)) == n.upper()` for any
  non-empty name without a colon. Oracle: Requirement 5 by hand.

## Interface

```python
# src/pricer/risk/issuer_credit.py (new)
def issuer_daily_spreads(prints: pd.DataFrame, *, min_tenor_years: float, max_tenor_years: float,
                         outlier_ratio: float, outlier_lookback: int) -> pd.Series
def issuer_spread_moves(daily: pd.Series, calendar: Sequence[pd.Timestamp], *,
                        max_gap_days: int) -> pd.Series
def proportional_backfill(moves: pd.Series, proxy_moves: pd.Series, *, issuer_level: float,
                          proxy_level: float) -> pd.Series

# src/pricer/risk/bumps.py (new function)
def shift_credit_issuers(shifts: Mapping[str, float], default: float) -> Transform

# src/scenariogen/factor_keys.py (new functions)
def issuer_credit_key(issuer: str) -> str
def is_issuer_credit(key: str) -> bool
def issuer_credit_name(key: str) -> str
```

`CreditSpreadData` and `CdsSpreadData` are in `src/pricer/input/common/market_data.py`; `Transform`
and `shift_credit` in `src/pricer/risk/bumps.py`.

## Notes

- Oracles are hand calculations on synthetic prints and calendars. No `data/`, network or current
  date.
- Context, not oracles: with the 2026-10-08 feed, ORCL has 163 days of 5Y prints since 2025-05 and
  META 58 since 2026-03; proportional betas at the splice are about 0.42 (ORCL, 36bp vs IG 86bp) and
  0.84 (META, 68bp vs 81bp).
