# long-history-ingest — decades of free price, rate and spread history, mapped onto scenario factors

## Requirement

The severity rule in `docs/design/scenario_set_and_ablation_design.md` §3.3 anchors the adverse tier
at the worst move on record, including the dot-com (2000–02) and 2008 analogs. Today equity history
starts in 2018 (a config start date), Treasury rates in 2022, and the ICE BofA OAS series are only
served by FRED as a rolling ~3-year window (an ICE licensing restriction). This change ingests the
long history that is freely available and turns it into daily moves per scenario factor, splicing a
proxy in front of factors whose own history is too young.

Sources:
- **Yahoo chart API** (already used by `equity/yahoo_downloader.py`) for daily adjusted closes from
  1990: the book's names that existed then (NVDA from 1999, TSM 1997, MU, INTC, AMD, AAPL, MSFT,
  XLK 1998) and the indices `^SOX` (from 1994), `^IXIC` and `^GSPC`, plus `XLU` as the power proxy.
  Stooq now answers with a JavaScript bot check and is not used.
- **FRED `fredgraph.csv`** (already used by `stress/fred_macro_downloader.py`) for Treasury yields
  (`DGS1`, `DGS2`, `DGS5`, `DGS10`, `DGS30`), `VIXCLS`, Moody's `BAA10Y` (the free long credit proxy
  the risk engine already uses) and FX (`DEXJPUS` JPY per USD, `DEXTAUS` TWD per USD).

When this change is done:

1. **Yahoo chart parsing.** `parse_chart(payload, ticker)` turns one Yahoo chart response into rows
   `date, ticker, adj_close`:
   - the date of each point is its UTC `timestamp` plus `meta.gmtoffset` seconds, as a calendar date
     `YYYY-MM-DD` (the exchange's local trading date);
   - `adj_close` comes from `indicators.adjclose[0].adjclose`;
   - points whose `adj_close` is `null` are dropped; a repeated date keeps the last point;
   - rows are sorted by date;
   - a payload with a non-null `chart.error`, or no result, raises `ValueError`.
2. **Yahoo history download.** `YahooHistoryDownloader(config, credentials, *, client=None)` reads
   `config["sources"]["yahoo_history"]`:

   | Key | Meaning |
   |---|---|
   | `endpoint_template` | with `{yahoo_ticker}`, `{period1}`, `{period2}` (Unix seconds) |
   | `start_date` | first date requested (`period1` = that date at 00:00 UTC) |
   | `tickers` | list of `{"ticker", "yahoo"}` |
   | `required` | configured `ticker`s (not Yahoo symbols) that must succeed |
   | `min_rows` | the floor of parsed rows per required ticker |
   | `output_file` | written under the config's `asset_class` |

   `collect()` requests each ticker once with `client.get(endpoint)` and parses `response.json()`. A
   ticker whose request raises `HttpClientError`, whose payload doesn't parse, or which yields fewer
   than `min_rows` rows is skipped with a warning, **unless it is required**, in which case
   `collect()` raises `MarketDataIngestError` naming it. It returns all parsed rows (columns
   `date, ticker, adj_close`), with the `ticker` column holding the configured `ticker`, not the
   Yahoo symbol. `run()` is `collect()` plus a write to the output file with a provenance sidecar.
   The file is separate from `prices_daily.csv` and `dividends.csv`, which this source never
   touches.
3. **FRED long download.** `parse_fredgraph(text, series_id)` parses one `fredgraph.csv` response
   (header line, then `date,value`) into a float `Series` indexed by `YYYY-MM-DD` strings, dropping
   FRED's missing marker `"."` and blank values. `FredLongDownloader(config, credentials, *,
   client=None)` reads `config["sources"]["fred_long"]` (`endpoint_template` with `{series_id}`,
   `series` list, `required` list, `start_date`, `min_rows`, `output_file`). Its `collect()` returns
   one wide frame with a `date` column and one column per series that succeeded, restricted to
   dates ≥ `start_date` and sorted by date. Failure handling and the floor work as in
   Requirement 2. `run()` writes it with a provenance sidecar.
4. **Factor moves.** `level_moves(levels, factor)` turns a level series (sorted by index) into the
   factor's daily moves in the canonical units of `factor_keys.py`:
   - for `eq:` and `fx:` factors, simple returns `L_t / L_{t-1} − 1`;
   - for every other factor (`ir:`, `credit`, `eqvol`), the change in percent points divided by
     100, `(L_t − L_{t-1}) / 100`;
   - the first point, which has no predecessor, is dropped.
5. **Long history per factor.** `build_long_history(prices, fred, mapping, *, min_overlap=250)`
   returns `(series, info)`. `prices` has `date, ticker, adj_close`; `fred` is a wide frame with
   `date` plus one column per series; `mapping` is factor key → spec. A spec is either
   `{"ticker": T}`, `{"ticker": T, "proxy": P}`, or `{"fred": COLUMN}`. For each factor:
   - its own moves are `level_moves` of `T`'s adjusted closes or of the FRED column;
   - with a `proxy`, the proxy's moves are `level_moves` of `P`'s closes. If the own and proxy
     moves share at least `min_overlap` dates, `β = σ_own / σ_proxy`, using sample standard
     deviations (ddof 1) over the shared dates. The proxy's moves dated before the first own move,
     times `β`, are prepended to the own moves. With fewer shared dates there is no splice: the
     series is the own moves only;
   - if a proxied factor's own ticker is missing from `prices`, its series is the proxy's moves with
     `β = 1`;
   - a factor whose source is missing entirely (no own and no proxy data) is left out of `series`;
   - `info[factor]` holds `source` (the ticker or FRED column), `proxy` (or `None`), `beta` (or
     `None` without a splice), `spliced` (bool), `own_start` (first own move date, or `None`),
     `start` (first date of the returned series, or `None`) and `missing` (bool).
     `missing` is true when the factor's own source (ticker or FRED column) has no data, and
     `spliced` is true when the series contains proxy moves. So the proxy fallback has `beta` 1.0,
     `spliced` true, `missing` true and `own_start` `None`; a factor with no data at all still has
     an `info` entry, with `missing` true, `spliced` false and `beta`, `own_start`, `start` `None`.

Out of scope: computing the severity anchors (the next change), using the long history in Σ or VaR,
the data-quality gate (nothing in pricing reads these files), and gold before GLD's 2004 start (no
free daily series).

## Acceptance criteria

- **C1**: `parse_chart` returns the rows of Requirement 1 for synthetic payloads: dates shifted by
  `gmtoffset` (including a point whose UTC date differs from its local date), `null` closes
  dropped, a repeated date keeping the last value, and rows sorted. It raises `ValueError` for a
  payload with `chart.error` set, or with an empty result. Oracle: Requirement 1 by hand.
- **C2**: With a fake client at the HTTP boundary, `YahooHistoryDownloader.collect()` and
  `FredLongDownloader.collect()`:
  - return every ticker or series that succeeded;
  - skip a failing non-required one, whether it raises `HttpClientError`, has an error payload, or
    falls below `min_rows`;
  - raise `MarketDataIngestError` naming a failing required one;
  - request `period1` = `start_date` at 00:00 UTC (Yahoo);
  - restrict FRED dates to ≥ `start_date`;
  - put the configured ticker in the `ticker` column.

  `parse_fredgraph` drops `"."` and blank values. Oracle: Requirements 2–3 by hand.
- **C3**: `level_moves` returns simple returns for `eq:`/`fx:` factors and percent-point changes
  / 100 for the others (rel 1e-12), with the first point dropped. Scaling a level series by a
  positive constant leaves `eq:`/`fx:` moves unchanged and scales other moves by the same constant.
  Oracle: Requirement 4 by hand; scaling as a property.
- **C4**: `build_long_history` splices a proxy before the own start with `β = σ_own / σ_proxy` over
  at least `min_overlap` shared dates (rel 1e-12). Below the overlap it doesn't splice. It falls
  back to the proxy with `β = 1` when the own ticker is missing, omits a factor with no data, takes
  FRED factors from their column, and fills `info` as in Requirement 5. Own moves are never
  altered by a splice. Multiplying the proxy's prices by a constant changes neither `β` nor the
  spliced series. Oracle: Requirement 5 by hand; invariances as properties.

## Interface

- `src.marketdata.equity.yahoo_history_downloader.parse_chart(payload: dict[str, Any], ticker: str)
  -> pd.DataFrame`
- `src.marketdata.equity.yahoo_history_downloader.YahooHistoryDownloader.__init__(self, config: dict,
  credentials: dict[str, str], *, client: Any = None) -> None`
- `src.marketdata.equity.yahoo_history_downloader.YahooHistoryDownloader.collect(self) -> pd.DataFrame`
- `src.marketdata.equity.yahoo_history_downloader.YahooHistoryDownloader.run(self) -> pd.DataFrame`
- `src.marketdata.stress.fred_long_downloader.parse_fredgraph(text: str, series_id: str) -> pd.Series`
- `src.marketdata.stress.fred_long_downloader.FredLongDownloader.__init__(self, config: dict,
  credentials: dict[str, str], *, client: Any = None) -> None`
- `src.marketdata.stress.fred_long_downloader.FredLongDownloader.collect(self) -> pd.DataFrame`
- `src.marketdata.stress.fred_long_downloader.FredLongDownloader.run(self) -> pd.DataFrame`
- `src.scenariogen.long_history.level_moves(levels: pd.Series, factor: str) -> pd.Series`
- `src.scenariogen.long_history.build_long_history(prices: pd.DataFrame, fred: pd.DataFrame,
  mapping: Mapping[str, Mapping[str, Any]], *, min_overlap: int = 250)
  -> tuple[dict[str, pd.Series], dict[str, dict[str, Any]]]`

**The client contract**, which tests fake at the HTTP boundary:
- `client.get(endpoint: str)` returns a response with `.json()` (Yahoo) and `.text` (FRED).
- A failed request raises `src.marketdata.common.http_client.HttpClientError`.
- With `client=None` the downloader builds the project's `HttpClient` from the source config.

`MarketDataIngestError` is `src.marketdata.common.errors.MarketDataIngestError`. Series indices are
`YYYY-MM-DD` strings or dates sorted ascending; tests compare by value.

## Notes

- **Intended mapping** (implementer's `configs/scenariogen/long_history.json`, not tested):
  - **Own history, no proxy:** NVDA, TSM, MU, INTC, AMD, AAPL, MSFT and XLK, each from its own
    ticker; `eq:SPX` from `^GSPC`.
  - **Proxied:** AVGO and SMH via `^SOX`; QQQ, META, GOOGL and TSLA via `^IXIC`; CEG and VST via
    `XLU`.
  - **No proxy:** GLD (own history only, from 2004).
  - **FRED:** `ir:DGS*` from `DGS*`; `credit` from `BAA10Y`; `eqvol` from `VIXCLS`; `fx:JPY` from
    `DEXJPUS`; `fx:TWD` from `DEXTAUS`.
- Spec tests use synthetic payloads, CSV text and frames only; no network or `data/`.
- Yahoo is already a source in this repo; its responses are adjusted for splits and dividends.
