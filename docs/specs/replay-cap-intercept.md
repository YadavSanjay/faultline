# replay-cap-intercept — cap a replay's anchored scale on the affine tier vector, with a margin

## Requirement

Track A item 1 of the 2026-10-18 fix list (after step 15 of
`docs/design/scenario_set_and_ablation_design.md` §9). Since MacroRAG's 221-day episode card landed,
`ai_deflation` resolves on the dual-RAG path but the plausibility gate holds it out at every tier:
`eq:CEG bound 96.02 vs 96` (mild and adverse), `96.08 vs 96` (severe).

Cause (diagnosed on the 2026-10-18 single-scenario run): `anchoring.anchor_tier` caps a replay's scale
`k` with `bound_scale_cap(unit, bounds)`, where `unit` is the tier vector at scale 1, so it assumes the
vector is proportional to `k`. It is affine: the covariance conditional mean has an intercept (with
zero drivers the vector is not zero; `eq:CEG` moves +0.000664), so `v(k) = c + k·(v(1) − c)`. At the
cap the overshoot is `c·(1 − k)` (0.000664 × 0.292 = +0.000194, the recorded 0.960194 against the
bound 0.96). Separately, even a proportional vector lands on the bound only up to rounding
(0.9600000000000002), and the gate's bound check is strict (`|observed| > cap` fails).

When this change is done:

1. **Affine cap.** `severity.affine_scale_cap(at_zero, at_one, bounds, *, margin=1e-9)` returns the
   largest scale `k >= 0` that keeps every bounded factor inside its bound for a vector affine in `k`,
   shrunk by a relative margin:
   - for each factor `f` in `bounds`: intercept `c_f = at_zero.get(f, 0.0)`, slope
     `s_f = at_one.get(f, 0.0) − c_f`;
   - the raw cap is `bound_scale_cap({f: s_f}, bounds, overlays={f: c_f})` (the largest `k >= 0` with
     `|c_f + k·s_f| <= bounds[f]` for every bounded `f`; `math.inf` when every slope is zero);
   - it returns `raw × (1 − margin)` when the raw cap is finite, else `math.inf`.

   Factors in `at_zero` / `at_one` but not in `bounds` are ignored. It raises `ValueError` when
   `margin` is not in `[0, 1)`, a bound is not positive, or an intercept alone exceeds its factor's
   bound (`|c_f| > bounds[f]`, as `bound_scale_cap` does). The inputs are not mutated.

2. **Wiring** (covered by the implementer in `tests/unit/`).
   - `anchoring.anchor_tier` with `bounds` computes `at_zero` as the materialized vector of the
     composed definition at scale 0 and `at_one` at scale 1 (both through the tier's generator, with
     `_scaled`), and caps `k` with `affine_scale_cap(at_zero, at_one, bounded)` (default margin)
     instead of `bound_scale_cap(unit, bounded)`. `k_cap` and `cap_binding` in the severity provenance
     keep their meaning (`cap_binding` is the bounded factor with the smallest
     `(bounds[f] − sign(s_f)·c_f) / |s_f|`).
   - A unit test with a generator whose conditional mean has an intercept shows the capped tier passes
     the plausibility bound check, and that a vector without an intercept caps as before (up to the
     margin).
   - Equity and FX factors floored at `RETURN_FLOOR` at scale 1 can make the slope smaller than the
     unfloored one; downside bounds sit beyond the floor (equity bound 96% > floor 95%), so a floored
     factor never binds. No change for it.

Out of scope: the lead-anchor correction step (`k *= target / got`), the plausibility bounds
themselves, crisis Σ, and non-replay scenarios (they have no cap).

## Acceptance criteria

- **C1**: `affine_scale_cap` returns the hand-computed cap for a vector with intercepts (several
  bounded factors, both signs, the binding factor on the outward side), equals
  `bound_scale_cap(at_one, bounds) × (1 − margin)` when `at_zero` is all zeros, returns `math.inf` when
  every bounded slope is zero, ignores unbounded factors and missing keys (counted as 0), and raises
  `ValueError` for a margin outside `[0, 1)`, a non-positive bound and an intercept beyond its bound.
  Properties: at the returned `k` (finite), `|c_f + k·s_f| <= bounds[f]` for every bounded factor, with
  strict inequality on every factor when `margin > 0`; with `margin = 0` some bounded factor sits on its
  bound within `1e-12` relative; the cap never increases when the margin increases. Oracle:
  Requirement 1 by hand.

## Interface

```python
# src/scenariogen/severity.py (new function)
def affine_scale_cap(at_zero: Mapping[str, float], at_one: Mapping[str, float],
                     bounds: Mapping[str, float], *, margin: float = 1e-9) -> float
```

`bound_scale_cap(moves, bounds, overlays=None)` already exists in the same module.

## Notes

- Oracles are hand calculations on synthetic vectors. No `data/`, network or current date.
- Clarifications (from writing the tests): a NaN margin raises `ValueError`; validation covers every
  bounded factor, even one with a zero slope or missing from `at_one`; a raw cap of 0 (an intercept on
  its bound with an outward slope) returns 0; strict inequality under a positive margin holds only
  for intercepts strictly inside their bounds; which check fires first among several errors is not
  specified.
- The 1e-9 margin moves a 96% bound by about 1e-7 percentage points: invisible in P&L, enough to
  clear the strict check.
