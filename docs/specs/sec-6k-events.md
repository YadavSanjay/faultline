# sec-6k-events — foreign private issuers' 6-K filings join the SEC event stream

## Requirement

The SEC event stream (`src/marketdata/news/sec_events_downloader.py`, `news/sec_events.csv`) keeps
Form 8-K filings whose item codes are in the allowlist; scenariogen turns them into a per-name news
multiplier. TSM (the book's #2 long) is a foreign private issuer: it files Form 6-K, which carries no
item codes (EDGAR's `items` is empty and `primaryDocDescription` is just "6-K"), so it has no events
today (`docs/design/scenario_set_and_ablation_design.md` §5.2, step 7).

TSM filed 165 6-Ks from 2024-01-01 to 2026-10-16, about five a month; most are routine (monthly
revenue, month-end shareholding reports, dividend adjustments, quarterly financial statements).
Counted at a flat weight, they would hold TSM's news multiplier permanently high. The filing's
primary-document file name does say what it is (`tsm-revenue20261008.htm`,
`a20251105changeofnaceo.htm`, `tsm-20261015x6k.htm` on an earnings day). So a 6-K is mapped to the
8-K item it is equivalent to by ordered file-name rules, and a 6-K no rule maps is left out, as an
8-K with no allowlisted item is today.

When this change is done:

1. **Classification.** `classify_6k(primary_document, rules)` returns the 8-K item code a 6-K is
   equivalent to, or `None`:
   - `rules` is an ordered sequence of `{"pattern": <regex>, "item": <code or null>}`; the first rule
     whose pattern matches the file name (`re.search`, case-insensitive) decides;
   - a matching rule with `item: null` marks the filing routine (`None`);
   - no matching rule gives `None`;
   - an invalid regex raises `ValueError`.
2. **6-K event rows.** `six_k_rows(recent, rules, allowlist, *, ticker, cik, start, end)` turns an
   EDGAR submissions `filings.recent` block into event rows for Form 6-K filings dated in
   `[start, end]` (ISO strings, inclusive):
   - one row per 6-K that `classify_6k` maps to an item in `allowlist`, with the same columns as an 8-K
     row: `ticker, cik, filing_date, form, item_codes, accession, doc_url, weight`, where `form` is
     `"6-K"`, `item_codes` the mapped code, `weight` its allowlist weight (rounded to 4 places) and
     `doc_url` the EDGAR filing index `https://www.sec.gov/Archives/edgar/data/<cik without leading
     zeros>/<accession without dashes>/<accession without dashes>-index.htm`;
   - other forms (including `6-K/A` and `8-K`) are ignored, as are 6-Ks mapped to `None` or to a code
     not in `allowlist`;
   - rows are in the block's order; a block with no qualifying filing gives an empty list;
   - a rule whose `item` is neither `null` nor in `allowlist` raises `ValueError`, whatever the block
     holds (a mistyped rule must not silently drop events).
3. **Wiring** (covered by the implementer in `tests/unit/` and `tests/marketdata/`).
   - `SecEventsDownloader` adds `six_k_rows` for every filer to its 8-K rows, from the same
     submissions JSON (no extra request); the output schema and the systemic-failure floor are
     unchanged. 8-K handling is byte-identical for the 8-K filers.
   - `configs/marketdata/news/news_marketdata.json` gains `six_k_rules`, adds `TSM` to `tickers` and
     a GDELT query for it. The committed rules, in order: routine → `null` for `revenue`, `monthend`,
     `dividend`, `fsx`, `agm`; officer or director change (`changeof`) → `5.02`; quarterly results
     (`^tsm-\d{8}x6k\.htm$`) → `2.02`; board or directors' resolutions (`board`, `directorsresolut`)
     → `8.01`; an acquisition or disposal (`tosell`, `acqui`) → `2.01`, added to the allowlist with weight 0.5 (as `1.01`/`1.02`, the other
     deal items). Anything else is left out.
   - The scenariogen news provider reads `form` without assuming `8-K` (a 6-K row is an event like
     any other).

Out of scope: reading 6-K document text (the stream stays structured metadata, no NLP), 20-F annual
reports, and filers other than TSM (the rules apply to any 6-K filer, but only TSM is configured).

## Acceptance criteria

- **C1**: `classify_6k` returns the first matching rule's item (or `None` for a routine rule), `None`
  with no match, matches case-insensitively, and raises `ValueError` for an invalid regex. Properties:
  rules after the first match never change the result; appending a rule that matches nothing changes
  nothing; prepending a catch-all routine rule (`".*"` → `null`) makes every result `None`. Oracle:
  hand-classified TSM file names from the Requirement; properties.
- **C2**: `six_k_rows` returns exactly the in-window 6-Ks mapped to allowlisted items, with the column
  values, weight and `doc_url` computed by hand; ignores other forms, out-of-window dates (both ends
  inclusive), routine and unmapped filings; keeps the block's order; and raises `ValueError` for a
  rule item outside the allowlist. Properties: the rows are a subsequence of the block's 6-Ks; every
  row's weight equals `allowlist[item_codes]`; narrowing the window never adds a row; a block of only
  8-Ks gives no rows. Oracle: hand calculation on a synthetic `filings.recent` block built in the test
  (EDGAR's parallel-array shape); properties.

## Interface

New functions in `src/marketdata/news/sec_events_downloader.py`:

- `src.marketdata.news.sec_events_downloader.classify_6k(primary_document: str,
  rules: Sequence[Mapping[str, str | None]]) -> str | None`
- `src.marketdata.news.sec_events_downloader.six_k_rows(recent: Mapping[str, Sequence[str]],
  rules: Sequence[Mapping[str, str | None]], allowlist: Mapping[str, float], *, ticker: str, cik: str,
  start: str, end: str) -> list[dict[str, object]]`

`recent` has EDGAR's parallel arrays `form`, `filingDate`, `accessionNumber`, `primaryDocument` (and
others, ignored); `cik` is the 10-digit zero-padded CIK the downloader already uses.

## Notes

- Spec tests use synthetic `filings.recent` blocks and file names copied from TSM's public EDGAR
  index (U.S. government-hosted filing metadata); no network or `data/`.
- File-name rules are issuer-specific conventions, not an SEC taxonomy: they are config, reviewed
  like the 8-K allowlist, and a new TSM naming pattern simply falls out (no event) until a rule
  covers it.
