# Quant code rules (faultline)

Writing-time digest of the `numerics.md` review lens (`.claude/review/lenses/numerics.md`), which
keeps the detail and the failure modes reviewers hunt for. A wrong number that looks plausible is
the most expensive defect here.

- **Conventions are explicit.** Day counter, calendar, business-day convention, settlement days,
  end-of-month, compounding and frequency come from the trade, instrument or config — never from a
  hidden default argument.
- **Units and signs are in names or types:** rates decimal vs percent vs bp, normal (bp) vs
  lognormal vol, FX quote direction, notional scaling; long/short, payer/receiver, P&L = new − base,
  VaR/ES as a positive loss.
- **QuantLib state:** don't set `ql.Settings.instance().evaluationDate` outside the base pricer, and
  don't add threads around QuantLib objects (parallelism stays process-level). Curve-cache keys
  include every input that changes the curve.
- **Point-in-time:** read data as of the run's `as_of`; anything later is look-ahead.
- **Numerics:** vectorise hot numeric paths (object-per-instrument QuantLib code is fine).
  Validate inputs at boundaries and raise if a `NaN` reaches an output. No float `==` on computed
  values.
- **Model parameters:** clamp/bound constants and golden PVs change only with a design-doc note or a
  `rebaseline_history` entry.
