# review-core suppressions (faultline)

Findings listed here are not re-raised by `review-core`. One line each:
`- <finding id> — false-positive|accepted-risk|wontfix — <reason> — <yyyy-mm-dd>`

`false-positive` entries feed each lens's measured precision — use it only when the
finding was wrong, not when it was right but not worth fixing.
- ai-authored:tests/bff/test_bff_quantlib_free.py:_DEFERRED_IMPORTS:hand-copied-deferred-list-mislabelled-and-docs-overclaim-enforcement — accepted-risk — P3 in a test; the hand-kept list is good enough for now (user decision) — 2026-10-03
- change-integrity:tools/standards/check_standards.py:stop:fingerprint-saved-even-when-findings-reported — accepted-risk — Latent until the Stop hook gates (advisory=false); tracked as a TODO in stop() before gating is turned on — 2026-10-06
- change-integrity:tools/standards/check_standards.py:stop:mypy-timeout-discards-all-stop-findings — accepted-risk — Stop hook is advisory; a mypy timeout only loses one advisory Stop report (user decision) — 2026-10-06
- change-integrity:tools/standards/check_standards.py:ratchet:burndown-file-lint-settings-not-ratcheted — accepted-risk — Burn-down file edits are visible in PR diffs and the standards job is advisory (user decision) — 2026-10-06
