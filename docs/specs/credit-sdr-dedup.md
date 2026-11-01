# credit-sdr-dedup — keep every DTCC credit record, and price CDS only from new trades

## Requirement

The DTCC SDR credit ingest has the defect `sdr-dedup` fixed for rates, plus two of its own:

- `src/marketdata/credit/dtcc_credit_downloader.py` writes `credit/dtcc_single_name_cds.csv` (SEC
  path, ORCL/META) and `credit/dtcc_index_cds.csv` (CFTC path, CDX.NA.IG/HY) through `upsert_csv`
  keyed on `date`, so every re-run collapses a day to **one** record. The files hold 408 and 590 rows
  over ~2 years; one real day (2026-10-05) carries 16 ORCL, 6 META, 420 CDX.NA.IG and 462 CDX.NA.HY
  records. CDS trades #28–30 and the ORCL/META bond spreads (#17/#18) are priced from these files.
- `src/marketdata/bonds/dtcc_credits_downloader.py` (`bonds/dtcc_corp_cds_spreads.csv`) has the same
  collapse **and** filters action types `["NEW", "MODI", "REVI"]` by exact match, so new trades
  (`NEWT`) never pass: the file holds 142 `MODI` + 2 `REVI` rows and no new trade at all.
- The CDS consumer (`src/pricer/loader/credit_adapter.py`, and the bond adapter's issuer-spread path
  that reads the same single-name file) takes the median spread of **every** row in its window. Once
  every record is kept, lifecycle events, novations and clearing records would enter that median.

When this change is done:

1. **Both credit feeds keep every record**, keyed on `Dissemination Identifier`, with CFTC Part 43
   corrections and cancellations applied exactly as in `sdr-dedup` (chain root, `EROR` removes the
   chain, latest `CORR` by `Event timestamp` wins, lifecycle records kept as their own records), every
   value held as an exact string, the legacy collapsed files replaced and their window re-fetched,
   a rolling window long enough for the consumer's longest lookback (365 calendar days), and the
   cancelled-roots sidecar `sdr-dedup` added for late-fetched days.
2. **A column-agnostic merge.** `merge_sdr_records` adds the rates output columns to whatever it
   merges. The credit feeds need the same chain semantics on their own columns: a new
   `merge_sdr_chains` returns exactly the columns of its inputs.
3. **A credit filter** keeps the rows whose filter column contains any include keyword and none of
   the exclude keywords (both case-insensitive), for **every** action type, with the credit output
   columns and string values (no numeric coercion at ingest).
4. **Only price-forming prints reach the spread medians.** A print is price-forming when its
   `Action type` is `NEWT` **and** its `Event type` is `TRAD`. Everything else is excluded: lifecycle
   events (`MODI`, `TERM`, `REVI`), novations and clearing records (`Event type` `NOVA`, `CLRG`,
   `ETRM`, `CORP`), and corrections (`CORR` carries a blank `Event type`, so whether it corrects a
   new trade cannot be told from the stored record). Prints are windowed by their **execution date**
   (the UTC date of `Execution Timestamp`), not the file date (~13% of single-name new trades are
   disseminated after their execution day), and a print disseminated after the valuation date is
   never used (no look-ahead).
5. **The bonds feed is retired.** `bonds/dtcc_corp_cds_spreads.csv` is no longer downloaded or read:
   the bond adapter's issuer-spread path uses the credit single-name feed (already its primary
   source) and, failing that, the configured fallback. The issuer list stays ORCL/META; AAPL, MSFT
   and AMZN keep their configured fallback spreads (widening the list is a separate change).

Items 1 (wiring), 5 and the adapters' use of item 4 are outside the spec tests; the implementer
covers them with unit tests.

## Acceptance criteria

- **C1**: `merge_sdr_chains` resolves records like `merge_sdr_records`: for any input frames that
  carry exactly the `sdr-dedup` output columns, `merge_sdr_chains(E, N)` holds the same records
  (same identifiers and field values, order-insensitive) as `merge_sdr_records(E, N)`. Oracle: the
  locked, already-merged `merge_sdr_records` (consistency) plus hand-built chains (point).
- **C2**: `merge_sdr_chains` keeps the inputs' columns: its result has exactly the union of the
  columns of `new` and of a non-legacy `existing`, adds none, and returns every kept field value as the
  same string. A legacy `existing` (no `Dissemination Identifier`) is discarded. Oracle: hand-built
  credit-shaped frames with a column the rates schema lacks (e.g. `Price`) and without rates-only
  columns.
- **C3**: `filter_credit_records(raw, day, filter_column, include_keywords, exclude_keywords)` keeps
  exactly the rows whose `filter_column` contains any include keyword and no exclude keyword (both
  case-insensitive substrings; a blank value is dropped), for every action type; stamps
  `date = day`; returns exactly `CREDIT_OUTPUT_COLUMNS` (a column missing from `raw` comes back
  blank, extra raw columns are dropped); and returns every value as the identical string it had in
  `raw` (e.g. `Spread-Leg 1` `"0.005935"` stays `"0.005935"`). Oracle: hand-built raw frame with
  labelled keep/drop rows.
- **C4**: `price_forming_prints(records, as_of, lookback_days)` keeps exactly the rows with
  `Action type == "NEWT"`, `Event type == "TRAD"`, `date <= as_of`, and execution date `d` with
  `as_of - lookback_days < d <= as_of` (calendar days; `d` is the UTC date of `Execution Timestamp`);
  it returns them with their columns and values unchanged. Rows with a missing or unparseable
  `Execution Timestamp` are dropped. Oracle: hand-built records labelled keep/drop, including a
  late-disseminated new trade (executed before its file date), a print disseminated after `as_of`,
  and each excluded action/event type.
- **C5**: The `price_forming_prints` window is half-open and monotone: the execution date exactly
  `lookback_days` before `as_of` is excluded and `as_of` itself is included; widening `lookback_days`
  never removes a row; moving `as_of` later never removes a row whose execution date is still inside
  the window and whose `date` is still `<= as_of`. Oracle: boundary fixture (point); metamorphic.

## Interface

In `src/marketdata/rates/sdr_records.py` (existing module, new function):

- `src.marketdata.rates.sdr_records.merge_sdr_chains(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame`

New module `src/marketdata/credit/sdr_credit_records.py`:

- `src.marketdata.credit.sdr_credit_records.CREDIT_OUTPUT_COLUMNS: tuple[str, ...]` = `("date",
  "Dissemination Identifier", "Original Dissemination Identifier", "Action type", "Event type",
  "Event timestamp", "Execution Timestamp", "Effective Date", "Expiration Date",
  "Underlying Asset Name", "UPI Underlier Name", "UPI FISN", "Notional currency-Leg 1",
  "Notional amount-Leg 1", "Spread-Leg 1", "Fixed rate-Leg 1", "Price", "Price notation", "Cleared",
  "Platform identifier", "Package indicator")`
- `src.marketdata.credit.sdr_credit_records.filter_credit_records(raw: pd.DataFrame, day: str, filter_column: str, include_keywords: list[str], exclude_keywords: list[str]) -> pd.DataFrame`
- `src.marketdata.credit.sdr_credit_records.price_forming_prints(records: pd.DataFrame, as_of: str, lookback_days: int) -> pd.DataFrame`

`CREDIT_OUTPUT_COLUMNS` is data: the tdd stub-only rule admits only imports and stub functions on a
spec branch, so the **implementer** defines it with exactly these values; spec tests carry their own
copy and do not import it.

## Notes

- **Data:** all fixtures are synthetic Part 43 credit records (column names as above), built in the
  spec's own `conftest.py`. No DTCC data is committed (oracles.md). Identifiers are made-up 19-digit
  integers; spreads are decimal strings (`"0.005935"` = 59.35 bp); timestamps are ISO-8601 UTC.
- **Facts behind Requirement 4** (one real day, 2026-10-05, both credit files): every `CORR` has a
  blank `Event type`; new trades are `NEWT`/`TRAD` (or `NEWT`/`NOVA` for novations); `TERM` carries
  `ETRM`, `CLRG` or `NOVA`. 109 of 825 single-name `NEWT` records were executed before the file date.
- **Known loss:** excluding `CORR` drops trades that were later corrected (≈5–10% of prints on the
  sample day). Keeping them would need the corrected record's original `Event type`, which the merge
  does not retain. A later re-lock could carry it.
- **Point-in-time limit:** the stored file holds only the latest version of each chain, so pricing a
  historical date sees corrections disseminated after it. `price_forming_prints` excludes those
  corrections anyway (it keeps no `CORR`), and its `date <= as_of` rule stops later new trades.
- **Pricing impact:** after re-ingest the spreads for CDS #28–30 and the ORCL/META bond spreads (#17/#18)
  change, so their PVs move and governance golden PVs will need a rebaseline. That rebaseline is a
  separate, explicitly approved step, not part of this change.
- **Out of scope:** adding AAPL/MSFT/AMZN to the single-name feed; CDS term-structure methodology.
