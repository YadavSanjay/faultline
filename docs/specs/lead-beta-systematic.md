# lead-beta-systematic — one-factor systematic part for the dual-RAG company-specific split

## Requirement

Track A item 2 of the 2026-10-18 fix list (decision 2026-10-19: option A). On the anchored dual-RAG
path, `anchoring.leg_transform` splits each single-name equity move into a systematic part and a
company-specific part, and the news, crowding and XbrlRag-facts multipliers scale only the
company-specific part (`residual = authored − systematic`). Today the systematic part is the Gaussian
conditional mean given **every other driver** of the scenario. In a replay those are ~27 strongly
correlated factors (SPX, XLK, SMH, QQQ, four Treasury tenors, vol, credit, FX, style ...), and the
regression is ill-posed: in the `ai_deflation` replay (the 2022 AI drawdown) the systematic part comes
out at MU +20.2%, AMD +14.6% and NVDA −91.1% against observed −38.8%, −65.4% and −66.4%. The error
lands in the company-specific part, which the multipliers then amplify (MU is pushed towards the −95%
floor). Smaller conditioning sets and ridge shrinkage do not fix it.

Decision: the systematic part of a single name is its **one-factor beta to the scenario's lead
factor** (the `severity.lead` of the anchored scenario, e.g. `eq:XLK` for `ai_deflation`).

When this change is done:

1. **One-factor systematic move.** `severity.lead_systematic(cov, lead, lead_move, targets)` returns,
   for each target factor, its conditional mean given the lead factor alone:
   `mean[t] + cov[t, lead] / cov[lead, lead] × (lead_move − mean[lead])`
   (`mean` and `cov` from the `FactorCovariance`, indexed by `cov.factors`).
   - Targets not in `cov.factors`, and a target equal to `lead`, are left out of the result (callers
     treat an absent factor as systematic 0, i.e. the whole move is company-specific).
   - Duplicate targets appear once. The result's keys follow the targets' first-appearance order.
   - It raises `ValueError` when `lead` is not in `cov.factors`, when `cov[lead, lead] <= 0`, or when
     `lead_move` is NaN or infinite. The inputs are not mutated.

2. **Wiring** (covered by the implementer in `tests/unit/`).
   - `anchoring.leg_transform(generator, cap, record, *, lead: str | None = None)`. With a `lead`:
     - company legs (`_company_legs`): `systematic = lead_systematic(cov, lead, base[lead], legs)` when
       the lead is in `base` (the macro or authored drivers the layers use) and in Σ; otherwise
       `systematic = {}` (no systematic part);
     - non-leg single-name drivers (`_scale_equity_drivers`): `systematic[f] =
       lead_systematic(cov, lead, base[lead], [f]).get(f, 0.0)` under the same condition, instead of
       the conditional mean on every other driver;
     - `record["systematic"] = {"rule": "lead_beta", "lead": lead, "lead_move": base[lead] or None}`
       for the report.
     Without a `lead` (no caller after this change, kept for safety) the old conditional-mean rule
     applies unchanged.
   - Every caller passes the scenario's lead: `runner._materialize_anchored`, the ablation runner's
     anchored subsets, the event-replay runner and the stability runner (wherever `leg_transform` is
     built).
   - `anchoring.ANCHORED_RULE` becomes `"dualrag-v4"`, so forward-log and ablation results made with the
     old split are never compared with the new ones.
   - **Final leg move (amendment 2026-10-19, decision: correction overlay).** A company leg is taken out
     of the drivers and becomes an overlay (`ablation.compose_definition`), so its final move is the
     propagation of the remaining drivers plus the overlay. Replacing only the company-specific rule
     would leave the all-driver propagation in the final move and break "final = authored when every
     multiplier is 1". So, with a `lead`, `_company_legs` also returns
     `leg_corrections[f] = systematic[f] − propagated[f]` for each leg, where `propagated[f]` is the
     conditional mean of `f` given the non-leg drivers of `base` (the old systematic rule) and
     `systematic[f]` the one-factor value (0 when absent). `compose_definition` gains an optional
     `leg_corrections: dict[str, float] | None = None`: each correction is added to that leg's overlay
     multiplied by `severity_scale` (it scales with the tier like the systematic parts) and **not** by
     the news/crowding multipliers. `None` (every non-anchored caller) leaves the composition unchanged.
     Result: a leg's final move is `systematic[f] + company-specific[f] × multipliers` (up to the
     Σ mean terms), and every other factor propagates exactly as before.
   - Unit tests on a synthetic 3-factor Σ: with every multiplier 1 a leg's final move equals its authored
     move (the existing "no double count" test keeps passing); with a multiplier m the final move is
     `systematic + m × (authored − systematic)`; a leg equal to the lead keeps its whole move as
     company-specific; `compose_definition` without `leg_corrections` is unchanged.

Out of scope: the non-anchored dual-RAG composition (`ablation.COMPOSITION_RULE`, no split; it passes no
`leg_corrections`), the
conditional-covariance propagation of the scenario vector itself (`ScenarioGenerator.materialize`),
the multiplier cap, and the choice of each scenario's lead (`scenarios.json` `severity.lead`).

## Acceptance criteria

- **C1**: `lead_systematic` returns the hand-computed moves on a synthetic 3-factor covariance with
  non-zero means (two targets, positive and negative covariance with the lead), leaves out the lead
  itself, unknown factors and duplicates, returns `{}` for no targets, and raises `ValueError` for an
  unknown lead, a zero-variance lead, and a NaN or infinite lead move. Properties: the result is affine
  in `lead_move` (doubling `lead_move − mean[lead]` doubles every `result[t] − mean[t]`); with
  `lead_move = mean[lead]` every result equals its target's mean; scaling the whole covariance matrix
  by `a > 0` leaves the result unchanged; the result for a target equals `cov.conditional_mean({lead:
  lead_move})[target]` within `1e-12` relative. Oracle: Requirement 1 by hand (and the existing
  conditional mean for the single-driver identity).

## Interface

```python
# src/scenariogen/severity.py (new function)
def lead_systematic(cov: FactorCovariance, lead: str, lead_move: float,
                    targets: Iterable[str]) -> dict[str, float]
```

`FactorCovariance(factors, mean, cov, n_obs, window, cov_id)` is in `src/scenariogen/covariance.py`. The stub spells the
last annotation `collections.abc.Iterable[str]` (same type): `severity.py` already imports `Mapping`
from `collections.abc`, and the stub lint and ruff's import sorting disagree on adding `Iterable` to
that line.

## Notes

- Oracles are hand calculations on a synthetic covariance. No `data/`, network or current date.
- Diagnosis (2026-10-19, `ai_deflation` dual-RAG mild tier, conditioning on all 27 non-leg drivers vs
  a 6-factor subset vs ridge λ = 0.1): every variant kept wrong-signed systematic moves for the semis;
  the one-factor beta to the lead is stable and has the sign of the lead's co-movement.
- Clarifications (from writing the tests): `targets` may be a one-shot iterator (read it once); an
  unknown lead raises `ValueError` (not the `KeyError` of `FactorCovariance.index`); a negative lead
  variance raises like a zero one; which check fires first, and whether an empty `targets` still
  raises for a bad lead or move, is not specified.
