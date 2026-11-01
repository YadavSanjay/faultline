# CLAUDE.md — `src/marketdata`

Downloads free public market data into `data/marketdata/<asset_class>/*.csv` (+ a
`.provenance.json` sidecar per file), then a post-ingest data-quality gate guards what pricing
consumes. Operator guide: [../../docs/guides/marketdata_guide.md](../../docs/guides/marketdata_guide.md).
Design background: [../../docs/design/AITrade_quantlib_portfolio_marketdata.md](../../docs/design/AITrade_quantlib_portfolio_marketdata.md).

## Structure

One subpackage per asset class — `bonds/ credit/ crowding/ equity/ fx/ news/ options/ rates/ reference/
stress/` — each with a `runner.py` orchestrator + one downloader module per external source (`crowding/`
= F5 FINRA short interest; `python -m src.marketdata.crowding.runner`). Shared infra in `common/`:

- `http_client.py` — `HttpClient` (auth types none/user_agent_only/api_key_param/api_key_header/
  bearer; retry on 429/5xx; per-source throttle; raises `HttpClientError`). Session injectable for tests.
- `data_store.py` — `get_output_path(asset_class, file)`, `save_csv`/`load_csv`/`upsert_csv`
  (dedup on key cols, "new wins"), `write_provenance` sidecars.
- `config_loader.py` — `load_config(asset_class)` → validate (pydantic `config_models.py`) → resolve
  `"today"` date placeholders.
- `observability.py` — `begin_run`/`record_run` (Prometheus `marketdata_*` + run manifest).
- `settings.py` — `load_credentials()` from env/`.env`.
- `errors.py` — `MarketDataIngestError` (fail-loud ingest floor, harness-audit F1).

## Entry points

```powershell
python -m src.marketdata.<class>.runner [--sources NAME …]   # class ∈ bonds credit equity fx news options rates reference stress
python -m src.marketdata.news.runner [--sources cik_map sec gdelt]  # P3/A: 8-K events + GDELT salience (cik_map runs first)
python -m src.marketdata.stress.runner --sources gpr epu fred_throughput  # P7: F8 GPR/EPU + F9 IPG3344S semi-IP
python -m scripts.marketdata.dq_gate                          # exit 0=PASS / 1=FAIL
python scripts/rebuild_pensford_from_supabase.py              # the CURRENT SOFR forward curve
```

- `--sources` choices are the **runner's** argparse list, which match config keys for most domains
  but **not** `rates` (CLI `nyfed fred fred_real nyfed_acm pensford dtcc`; `fred_real` + `nyfed_acm`
  are P5/F6 — TIPS real/breakeven + ACM term premium, written to their own files, not `fred_rates.csv`)
  or `equity` (CLI `stooq nasdaq yahoo_events`) or `news` (CLI `cik_map sec gdelt` →
  `sec_company_tickers`/`sec_events`/`gdelt`; **order matters — cik_map (A0) must run before sec (A1)**,
  which reads the CIK map it writes).
  Default = all enabled sources.
- Output CSVs land in `data/marketdata/<class>/`; each has a `.csv.provenance.json` sidecar.

## Conventions

- **Adding a source:** write `<source>_downloader.py` (class `(config, credentials, *, client=None)`,
  `run()` → `get_output_path` + `save_csv`/`upsert_csv`); add a `sources.<key>` block to the domain
  config; register it in **both** the runner's `available` dict **and** its argparse `choices`
  (hand-maintained, can drift); if a pricing/risk path reads it, add it to `configs/marketdata/dq_gate.json`.
- **Fail-loud:** a downloader that can systemically fail (auth/endpoint) should raise
  `MarketDataIngestError` below a data floor rather than write an empty CSV (see the EDGAR downloaders).
- **Idempotent:** all writes are upserts; re-running is safe. Only Stooq is truly incremental.
- **Tests are offline:** inject a fake `HttpClient`/session; no network (see `tests/marketdata/`).
- Lint/type/test scoped: `ruff check src/marketdata`, `mypy` (src.marketdata.* also disables
  `import-untyped` for `requests`), `pytest tests/marketdata`.

## Gotchas

- **Pensford SOFR curve** is the binding USD discount input. The archive downloader (`--sources
  pensford`) lags to month-end; use `scripts/rebuild_pensford_from_supabase.py` for the current curve.
  A degenerate one-point-per-snapshot curve was a real bug (fixed) — don't reintroduce single-point dedup.
- **`edgar_xbrl` feed is DEPRECATED** (`enabled:false`, harness-audit F2) — superseded by ARag
  `facts_lookup`. Don't re-enable it as a fundamentals source without cause.
- Config `output_file` for pensford is ignored — the downloader hardcodes `pensford_sofr_forward.csv`.
