# Shared spec-test snapshots

Committed, pinned market data for spec tests (`tests/spec/**`). Public-domain or synthetic only —
see [.claude/tdd/oracles.md](../../../../.claude/tdd/oracles.md). Files here are never refreshed in
place: a new snapshot gets a new file name.

## `ust_par_yields_20260908_20261005.csv`

- **What:** U.S. Treasury Daily Par Yield Curve Rates, 20 business days 2026-09-08 … 2026-10-05.
- **Source:** U.S. Department of the Treasury,
  `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/` (daily Treasury
  par yield curve). Copied from faultline's ingest (`data/marketdata/bonds/treasury_par_yields.csv`,
  fetched 2026-10-06).
- **Licence:** a work of the U.S. Government, public domain (17 U.S.C. §105).
- **Columns:** `date` (ISO), then par yields in **percent** (4.84 = 4.84%) for
  `t1mo t2mo t3mo t6mo t1yr t2yr t3yr t5yr t7yr t10yr t20yr t30yr t4mo` (bond-equivalent,
  semiannual). `t4mo` is the 4-month bill, listed last.
- **sha256:** `7b0972848f636ab1cf8b0ea1ae863fe867bc8425ffdb37867b69f266ce7b58eb`
- **Matches** `valuation_date = "2026-10-05"` in `.claude/tdd/profile.toml` (the last row).
