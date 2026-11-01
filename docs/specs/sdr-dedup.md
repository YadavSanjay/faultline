# sdr-dedup — keep every DTCC SDR trade record, keyed on its dissemination identifier

## Requirement

The DTCC/CFTC SDR rates ingest (`src/marketdata/rates/dtcc_sdr_downloader.py`) writes
`dtcc_sdr_irs.csv` and `dtcc_sdr_ir_options.csv` through `upsert_csv` with its default key, the
`date` column. Every re-run therefore collapses each day's trades to **one** row: the files hold
~1 row per business day although one day's public CFTC file carries ~5,700 USD SOFR records
(~4,600 new trades, measured on 2026-10-05). The free trade tape that could stand in for licensed
SOFR swap quotes is thrown away, and conclusions drawn from it (e.g. "only ~27 rows/day", "the
IR-options feed is not calibratable") rest on the collapsed sample.

When this change is done:

1. Records are identified by the CFTC **`Dissemination Identifier`** (a 19-digit integer), never
   by date. Every distinct record survives a merge, any number per day.
2. Merging is **idempotent** and **new data wins** on an identical identifier.
3. **Corrections and cancellations are applied.** Under CFTC Part 43 a correction (`CORR`) or a
   cancellation (`EROR`) points at the record it amends through `Original Dissemination
   Identifier`. A trade's *chain* is identified by its root: `Original Dissemination Identifier`
   when present, else its own `Dissemination Identifier`.
   - `EROR` removes the whole chain (the root record and every `CORR` of it); the `EROR` row itself
     is not kept.
   - `CORR` replaces the root record and any earlier `CORR` of the same chain; only the latest
     version of the chain is kept. "Latest" is the larger `Event timestamp` (ISO-8601 UTC string).
   - Lifecycle records (`MODI`, `TERM`, `REVI`, and any other action type) are kept as their own
     records under their own identifier and remove nothing.
4. **Identifiers are exact.** They are stored and read back as strings, digit for digit, including
   in columns with blanks (`Original Dissemination Identifier` is blank for new trades, which would
   otherwise make pandas read it as float64 and lose precision above 2^53).
5. **The collapsed legacy files are replaced, not merged.** An existing file without a
   `Dissemination Identifier` column is legacy: it is discarded on merge, and every business date in
   the window is fetched again.
6. **The SOFR-swap filter keeps everything the later steps need.** It keeps every USD row whose UPI
   underlier names SOFR and that is not an option (blank `Option Premium Amount`), for **every**
   action type (so corrections and cancellations reach the merge; today only NEWT/MODI/REVI pass),
   with the columns listed under Interface.

The downloader's `run()` uses these functions for both output files. Its wiring (HTTP, zip, paths)
is outside the spec tests; the implementer covers it with unit tests.

## Acceptance criteria

- **C1**: Merging N records that share one `date` but have distinct identifiers into an empty or
  unrelated existing set yields all N plus the existing records, row count exact. Oracle: hand count
  of the fixture.
- **C2**: `merge_sdr_records` is idempotent — `merge(merge(E, N), N)` and `merge(E, N)` hold the same
  records (compared as sets of identifiers and field values, order-insensitive); `merge(E, E)` equals
  `E` for any valid set `E`. Oracle: invariant.
- **C3**: On an identical `Dissemination Identifier`, the record from `new` is kept with all of its
  field values, whatever the row order of either input. Oracle: hand-built fixture.
- **C4**: Correction and cancellation chains resolve as in Requirement 3: after merge, a chain with
  an `EROR` has no records; a chain with `CORR`s has exactly one record, the one with the largest
  `Event timestamp`; `MODI`/`TERM` records are present and their root records are untouched. This
  holds whether the root and its amendments arrive in the same merge or in separate merges: in either
  order for `CORR`/`MODI`/`TERM`; an `EROR` arrives in the same merge as its chain or a later one
  (see Notes). Oracle: hand-built chains; metamorphic (split/order of arrival does not change the
  result).
- **C5**: An existing frame without a `Dissemination Identifier` column is discarded:
  `merge_sdr_records(legacy, new)` equals `merge_sdr_records(empty, new)`. Oracle: invariant.
- **C6**: `sdr_dates_to_fetch(existing, dates)` returns every date in `dates` (in input order) when
  `existing` is empty or legacy (no `Dissemination Identifier` column), and otherwise exactly the
  dates in `dates` not present in `existing["date"]`. Oracle: hand-built fixture; set identity.
- **C7**: `write_sdr_records` then `read_sdr_records` round-trips every identifier column as the
  exact same string, including 19-digit values above 2^53 and blanks (read back as missing, not as
  `"nan"`), and preserves the row count. Oracle: the literal strings in the test; property over
  integers in [10^18, 10^19 − 1].
- **C8**: `filter_sofr_swap_records(raw, day, underlier_keywords, currency)` keeps exactly the rows
  that (a) have `Notional currency-Leg 1` equal to `currency`, (b) have a `UPI Underlier Name`
  containing any keyword (case-insensitive), and (c) have a blank `Option Premium Amount` — for every
  action type; it adds `date = day` and returns exactly the output columns listed under Interface
  (a column missing from `raw` is present and blank). Oracle: hand-built raw frame with labelled
  keep/drop rows.

## Interface

New module `src/marketdata/rates/sdr_records.py` (pure pandas; storage I/O through
`src.common.storage`):

- `src.marketdata.rates.sdr_records.ID_COLUMNS: tuple[str, ...]` =
  `("Dissemination Identifier", "Original Dissemination Identifier")`
- `src.marketdata.rates.sdr_records.OUTPUT_COLUMNS: tuple[str, ...]` = `("date",
  "Dissemination Identifier", "Original Dissemination Identifier", "Action type", "Event type",
  "Event timestamp", "Execution Timestamp", "Effective Date", "Expiration Date", "Cleared",
  "Platform identifier", "Package indicator", "Non-standardized term indicator",
  "Block trade election indicator", "UPI FISN", "UPI Underlier Name", "Notional currency-Leg 1",
  "Notional amount-Leg 1", "Fixed rate-Leg 1", "Fixed rate-Leg 2", "Spread-Leg 1", "Spread-Leg 2",
  "Fixed rate payment frequency period-Leg 1", "Fixed rate payment frequency period-Leg 2")`
- `src.marketdata.rates.sdr_records.merge_sdr_records(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame`
- `src.marketdata.rates.sdr_records.sdr_dates_to_fetch(existing: pd.DataFrame, dates: list[str]) -> list[str]`
- `src.marketdata.rates.sdr_records.filter_sofr_swap_records(raw: pd.DataFrame, day: str, underlier_keywords: list[str], currency: str) -> pd.DataFrame`
- `src.marketdata.rates.sdr_records.write_sdr_records(df: pd.DataFrame, path: Path | str) -> None`
- `src.marketdata.rates.sdr_records.read_sdr_records(path: Path | str) -> pd.DataFrame`

The two constants are data, not stubs. The tdd stub-only rule admits only imports and stub functions
on a spec branch, so the **implementer** defines them with exactly these values; spec tests carry
their own copy of the column list and do not import them.

## Notes

- **Data:** all fixtures are synthetic records in the CFTC Part 43 public-dissemination schema
  (column names as above), built inside the spec's own `conftest.py`. No DTCC data is committed
  (oracles.md: DTCC terms). Identifiers in fixtures are made-up 19-digit integers.
- **Semantics source:** CFTC Part 43 public dissemination (17 CFR Part 43, Appendix A data elements:
  Action type, Event type, Dissemination Identifier, Original Dissemination Identifier). The
  chain rule in Requirement 3 is this spec's reading of it; **reviewer: confirm or correct at spec
  PR time.**
- **`EROR` before its root:** because the `EROR` row is not kept, a root that arrives in a *later*
  merge than its cancellation would survive. A cancellation is disseminated after the record it
  cancels and the ingest fetches dates in order, so this is not constrained here. If out-of-order
  re-fetches become possible, keep `EROR` rows as tombstones (a re-lock).
- **Not specified, not tested:** a `MODI`/`TERM` inside a chain that also has a `CORR` or `EROR`;
  ties on `Event timestamp`; a duplicate identifier within one input frame.
- **Out of scope:** building SOFR par quotes or curves from the records — that is the follow-on spec
  `sofr-sdr-curve`. Re-downloading history is an operational step after the implementation merges.
- **Volume:** keeping every record makes the files much larger (~5.7k SOFR rows/day). How much
  history to keep is an implementation/config choice, not part of this spec.
