# signoff-legacy-seq — sign-off records written before `seq` existed stay readable

## Requirement

`SignoffStore` (`src/scenariogen/signoff.py`) is an append-only audit log of approve/reject decisions,
where the latest decision for a scenario version wins. It orders records by a monotonic `seq` column.
Records written before `seq` was added have no such column, and today a single one of them makes
every read fail (`ORDER BY seq` on a dataset without the column). That blocks `signoff_runner --list`,
`--approve`, `--reject` and the runner's `--require-signoff` gate.

When this change is done:

1. **Ordering.** `order_records(df)` returns the records ordered newest first:
   - a record without a `seq` value (the column is absent, or the value is missing) is a legacy
     record and gets `seq = -1` in the result;
   - records are ordered by `seq` descending, then by `recorded_at` descending (ISO-8601 UTC
     strings, compared as text), so every legacy record ranks after every sequenced one, and legacy
     records rank among themselves by when they were recorded;
   - the result has a `seq` column of integers and the input's other columns and values unchanged;
     the index is reset to `0..n-1`;
   - an empty frame (with or without a `seq` column) returns an empty frame with a `seq` column;
   - the input is not modified.
2. **Next sequence number.** `next_seq(df)` returns one more than the largest `seq` among sequenced
   records, or 0 when there are none (empty frame, no `seq` column, or only legacy records).
3. **Wiring** (covered by the implementer in `tests/unit/`). `SignoffStore` reads the dataset without
   ordering in the query and applies `order_records`; `record()` numbers the new record with
   `next_seq`. A store holding a legacy record then lists it, keeps it in `history()`, and lets a new
   decision for the same version win over it.

Out of scope: rewriting or deleting stored records (the log stays append-only), and the sign-off
CLI's output format.

## Acceptance criteria

- **C1**: `order_records` orders sequenced records by `seq` descending and legacy records after them
  by `recorded_at` descending, sets legacy `seq` to -1, keeps every other column and value, resets
  the index, handles an empty frame, and leaves its input unchanged. Properties: the output is a
  permutation of the input rows (compared on every original column); its `seq` is non-increasing;
  shuffling the input rows doesn't change the output when `(seq, recorded_at)` pairs are distinct.
  Oracle: hand-ordered fixtures; properties.
- **C2**: `next_seq` is `max(seq) + 1` over sequenced records and 0 with none (empty frame, no `seq`
  column, only legacy rows). Properties: appending a legacy record doesn't change it; appending a
  record with `seq = next_seq(df)` raises it by exactly 1. Oracle: hand calculation; properties.

## Interface

New functions in `src/scenariogen/signoff.py`:

- `src.scenariogen.signoff.order_records(df: pd.DataFrame) -> pd.DataFrame`
- `src.scenariogen.signoff.next_seq(df: pd.DataFrame) -> int`

Frames carry the sign-off schema columns `scenario_version, scenario, status, approver, recorded_at,
note` and, when present, `seq` (integers, or floats with `NaN` for legacy rows after a union read).
Existing signatures stay unchanged.

## Notes

- Spec tests build frames in memory; no `data/`, parquet files or services.
- The legacy record that prompted this is a June 2026 smoke-test approval of an old `ai_bust` version;
  it stays in the log as written.
