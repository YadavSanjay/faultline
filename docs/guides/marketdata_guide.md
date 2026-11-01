# 📥 Market Data — User & Operator Guide

> Downloads the raw market data that every other subsystem prices and stresses against, from
> ~two dozen **free public sources** (Treasury, FRED, NY Fed, ECB, BOJ, CBOE, DTCC, FINRA, Yahoo,
> Stooq, EIA, SEC EDGAR, GDELT, Caldara-Iacoviello GPR, …) across 10 asset classes, normalizes it to CSVs under
> `data/marketdata/`, and runs a **data‑quality gate** before the data is allowed downstream.

Source: [src/marketdata/](../../src/marketdata/) · Config: [configs/marketdata/](../../configs/marketdata/) · Scripts: [scripts/marketdata/](../../scripts/marketdata/) · Design background: [AITrade_quantlib_portfolio_marketdata.md](../design/AITrade_quantlib_portfolio_marketdata.md)

---

## 👔 For business users

### What it produces

One folder of CSVs per asset class, each backed by a `.provenance.json` sidecar (source URL,
fetch time, row count, content hash, date range) for auditability:

| Asset class | Examples of what's downloaded |
|---|---|
| **rates** | SOFR fixings & 30‑day average, Pensford SOFR forward curve, Treasury yields (DGS*), DTCC SDR swap trades |
| **equity** | Daily prices (NVDA, TSM, AVGO, MU, XLK, SMH, option underliers), dividends, splits |
| **fx** | ECB cross rates, Fed H.10, BOJ TONAR, Taiwan CBC TWD NDF fixings |
| **bonds** | Treasury par yields, TreasuryDirect auctions, EDGAR filings, FINRA TRACE, DTCC corp CDS |
| **credit** | DTCC single‑name CDS, CDX.NA.IG index |
| **options** | CBOE option chains + vol indices (VIX, VVIX, SKEW…), Yahoo chains; + derived option **signals** (IV/term/skew/VRP, RND tail probs, implied credit — B1–B3) |
| **stress** | FRED macro (IG/HY OAS, VIX), ICE MOVE index, EIA power demand; + **GPR** geopolitical-risk (daily global + monthly country GPRC_TWN/CHN) & **EPU** (F8), + **FRED IPG3344S** semiconductor IP (F9 throughput) |
| **news** | **SEC 8-K** item-code event stream + **GDELT** article-volume/tone salience (P3): a numeric, cited news feature — no document store |
| **crowding** | **FINRA consolidated short interest** (days-to-cover per name; F5 crowding + #6 SMH short-squeeze signal) |
| **reference** | OpenFIGI identifiers (EDGAR XBRL fundamentals _deprecated 2026-07, harness-audit F2 — superseded by ARag `facts_lookup`; feed disabled) |

### What "data quality" means here

After download, the **DQ gate** ([scripts/marketdata/dq_gate.py](../../scripts/marketdata/dq_gate.py),
config [configs/marketdata/dq_gate.json](../../configs/marketdata/dq_gate.json)) checks each required
file for: present, ≥1 row, required columns present (**blocking**), and not‑too‑stale / not‑too‑null
(**warning**). A `BLOCK` means downstream pricing should not trust the data until fixed.

---

## 🛠️ For technical operators

### Entry points — one runner per domain

All domain runners share the same invocation and the same single flag:

```powershell
$env:PYTHONPATH = $PWD.Path   # the repo root
python -m src.marketdata.<domain>.runner [--sources NAME [NAME …]]
```

| Domain | Command | Source: [runner.py](../../src/marketdata/) |
|---|---|---|
| Bonds | `python -m src.marketdata.bonds.runner` | [bonds/runner.py](../../src/marketdata/bonds/runner.py) |
| Credit | `python -m src.marketdata.credit.runner` | [credit/runner.py](../../src/marketdata/credit/runner.py) |
| Equity | `python -m src.marketdata.equity.runner` | [equity/runner.py](../../src/marketdata/equity/runner.py) |
| FX | `python -m src.marketdata.fx.runner` | [fx/runner.py](../../src/marketdata/fx/runner.py) |
| Options | `python -m src.marketdata.options.runner` | [options/runner.py](../../src/marketdata/options/runner.py) |
| Rates | `python -m src.marketdata.rates.runner` | [rates/runner.py](../../src/marketdata/rates/runner.py) |
| Reference | `python -m src.marketdata.reference.runner` | [reference/runner.py](../../src/marketdata/reference/runner.py) |
| Stress | `python -m src.marketdata.stress.runner` (`--sources gpr epu fred_throughput` for F8/F9) | [stress/runner.py](../../src/marketdata/stress/runner.py) |
| News | `python -m src.marketdata.news.runner` (`--sources cik_map sec gdelt`; **cik_map runs before sec**, which reads the CIK map) | [news/runner.py](../../src/marketdata/news/runner.py) |
| Crowding | `python -m src.marketdata.crowding.runner` (FINRA short interest) | [crowding/runner.py](../../src/marketdata/crowding/runner.py) |

**`--sources`** — `nargs="+"`; the accepted names are the runner's `choices` list (defined in each
`runner.py`'s argparse). These match the config source keys for most domains, but **not all** —
notably `rates` (CLI `nyfed fred pensford dtcc`) and `equity` (CLI `stooq nasdaq yahoo_events`)
use short CLI names that differ from their config keys. Default = **all enabled sources**.

```powershell
# All enabled sources for the domain
python -m src.marketdata.rates.runner

# Only specific sources (use the runner's CLI choices, not the config keys)
python -m src.marketdata.rates.runner --sources nyfed fred
python -m src.marketdata.bonds.runner --sources treasury_gov edgar
```

### Full vs incremental downloads

The system is **idempotent by design**: every write is an *upsert* that de‑duplicates on a key
(usually `date`, or `(date, ticker)` for prices) — see `upsert_csv()` in
[src/marketdata/common/data_store.py](../../src/marketdata/common/data_store.py). So you can re‑run a
runner as often as you like without corrupting history.

| Mode | How to do it | Behaviour |
|---|---|---|
| **From scratch (full)** | Delete (or move) the target CSV(s) under `data/marketdata/<domain>/`, then run the domain runner. | The runner fetches the full configured `time_window` and writes a fresh file. |
| **Incremental (latest)** | Just **re‑run the domain runner** on top of existing files. | Most sources re‑fetch the window and upsert (idempotent). Equity prices come from **Yahoo** (`yahoo_events`) — the primary source, which fetches a rolling range and upserts. (The **Stooq** downloader was truly incremental but is **disabled/broken** as of 2026-07 — its CSV endpoint now returns JS‑rendered HTML; see [equity/stooq_downloader.py](../../src/marketdata/equity/stooq_downloader.py).) |

> The download window is set per domain in `configs/marketdata/<domain>/<domain>_marketdata.json`
> under `time_window` (`start_date` / `end_date`, where `"today"` resolves at runtime).

### ⚠️ The SOFR forward curve (Pensford) — use the rebuild script

The Pensford SOFR forward curve (`data/marketdata/rates/pensford_sofr_forward.csv`) is the
**binding discount input** for the whole USD book, and it has a sharp edge:

- The **archive** downloader (`--sources pensford`) scrapes month-end Excel files, which **lag** —
  at the 2026-10-06 ingest the newest archived file was still 2026-05-29 (no June–September
  month-ends). (It used to also **collapse** the monthly path to one point per snapshot —
  degenerating the OIS curve to ~0% — because
  `upsert_csv` de-dupped on `as_of` alone; **fixed** to de-dup on `(as_of_date, settlement_date)`.
  See [01-sofr-multicurve.md](../pricing/methodology/01-sofr-multicurve.md).)
- The **current** curve lives in Pensford's live `/forward-curve` page, backed by a public
  **Supabase** DB (`forward_curve` + `archive_forward_curve`). Ingest the full monthly path with:

  ```powershell
  python scripts/rebuild_pensford_from_supabase.py
  ```

  It needs `PENSFORD_SUPABASE_ANON_KEY` in `.env`: the public anon key the page itself sends (browser
  dev tools → Network → any `supabase.co` request → the `apikey` header).

  This rebuilds the file with real paths (the current curve — 2026-10-06 at the last run — plus
  history incl. 2026-07-10 and 2026-05-29 backfilled) → a real curve (forwards ~3.9% → ~5.2% at
  2026-10-06). Use it for the **current** curve — the archive only has month-end files through
  2026-05-29. (`--sources pensford` no longer collapses the path — the downloader now
  de-dups on `(as_of_date, settlement_date)` — it just can't reach months the archive
  hasn't published.)

### Data‑quality gate

```powershell
python -m scripts.marketdata.dq_gate [--config configs/marketdata/dq_gate.json] [--as-of YYYY-MM-DD]
```

- Exit code **0 = PASS**, **1 = FAIL** (use it as a pipeline gate before pricing).
- Per‑file rules in [dq_gate.json](../../configs/marketdata/dq_gate.json): `min_rows`, `required_columns`,
  `max_staleness_days` (default 7, **warn**), `max_null_fraction` (default 0.5, **warn**).
  Missing file / zero rows / missing required column = **block**.

### Credentials per source

Read by [src/marketdata/common/settings.py](../../src/marketdata/common/settings.py) from `.env`:

| Env var | Needed by | If missing |
|---|---|---|
| `FAULTLINE_CONTACT_EMAIL` | the User-Agent contact for bonds/news/reference (SEC EDGAR) and stress (GPR, EPU); also sent to the other keyless sources | those sources **raise** (`HttpClientError`) — SEC requires a contact |
| `STOOQ_API_KEY` | equity — **Stooq needs no key** (public CSV); only the disabled Nasdaq source uses it | optional |
| `FINRA_API_KEY` + `FINRA_API_KEY_SECRET` | bonds (FINRA OAuth2) | runner errors |
| `EIA_API_KEY` | stress (EIA power) | warns and **skips** that source |
| `OPENFIGI_API_KEY` | reference (OpenFIGI) | optional — anonymous tier used |
| `FRED_API_KEY` | rates/stress | **not used** — the free `fredgraph.csv` endpoint is used |

### Output layout

```
data/marketdata/
├── rates/    fred_rates.csv, nyfed_sofr_rates.csv, pensford_sofr_forward.csv, dtcc_sdr_irs.csv …
├── equity/   prices_daily.csv, dividends.csv, splits.csv
├── fx/       ecb_fx_rates.csv, fed_h10_fx_rates.csv, boj_tonar.csv, cbc_twd_ndf_fix.csv
├── bonds/    treasury_par_yields.csv, edgar_bond_filings.csv, finra_treasury_daily.csv …
├── credit/   dtcc_single_name_cds.csv, dtcc_index_cds.csv
├── options/  chains/<UNDERLIER>_chains.csv, vol_indices/VIX_History.csv …
├── stress/   fred_stress_macro.csv, move_index.csv, eia_power_demand.csv
└── reference/ instruments.json, instruments_figi.csv
```

Each `*.csv` has a sibling `*.csv.provenance.json`. All output is **CSV** (one PDF for the CDX
annex). There is no database.

### Config structure

Each domain config (`configs/marketdata/<domain>/<domain>_marketdata.json`) holds: `portfolio`
(the trades that drive what to download), `time_window`, and a `sources` map. Each source entry
declares `enabled`, `base_url`, `auth` (`none` / `api_key_param` / `api_key_header` /
`user_agent_only` / `oauth2_client_credentials`), `timeout_s`, `retry`, `endpoint_template`, and
`output_file`. Toggle `"enabled": false` to silence an optional/unavailable source.

---

## 🔗 How other subsystems consume market data

Downstream code never reads these CSVs ad‑hoc. The pricer wraps them in a **content‑addressed
snapshot** ([src/pricer/loader/snapshot.py](../../src/pricer/loader/snapshot.py)) that fingerprints
every consumed file (SHA‑256) and derives a `snapshot_id`, which is then stamped into every
pricing/risk/scenario result for reproducibility.

```python
from src.pricer.loader import MarketDataSnapshot
snapshot = MarketDataSnapshot(...)            # built once per run
df = snapshot.read_csv("equity/prices_daily.csv")   # cached read used by all adapters
```

Required columns are enforced at read time by [src/pricer/loader/schema.py](../../src/pricer/loader/schema.py).
The **UI/BFF** reads the CSVs directly and enriches them with provenance + DQ + trade linkage via
`/api/md/*` — see [ui_guide.md](ui_guide.md).

---

## 🧰 Operations & verification

- **Per‑source sanity checks**: the `scripts/marketdata/verify_*.py` and `check_*.py` scripts
  (e.g. [verify_rates.py](../../scripts/marketdata/verify_rates.py), [check_stooq.py](../../scripts/marketdata/check_stooq.py))
  fetch and print a sample so you can confirm an endpoint is alive before a full run.
- **Logging**: structured logs via `src/marketdata/common/observability.py`; set `LOG_LEVEL=DEBUG`
  for verbose HTTP/parse detail. `PRICER_JSON_LOGS=1` for JSON logs.
- **Metrics**: per‑run row counts and source status are recorded (Prometheus `marketdata_*`).

---

## 🚑 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `FileNotFoundError: config not found` | Wrong domain name / cwd | Run from repo root; confirm `configs/marketdata/<domain>/<domain>_marketdata.json` exists. |
| `HTTP 401 / 403` | Missing/invalid key | Set `STOOQ_API_KEY` / `FINRA_API_KEY` / `FINRA_API_KEY_SECRET` in `.env`. |
| `No access_token in FINRA response` | FINRA OAuth2 failed | Verify the FINRA client id **and** secret are both set and valid. |
| Empty DataFrame / no rows | Window dates, endpoint down, or over‑tight filter | Run a single `--sources X`; check `time_window`; test the URL with a `check_*.py` script. |
| CSV parse error from a source | The external site changed its format | Compare against the downloader's parser; sources like CBC/Fed H.10 are HTML‑scraped and brittle. |
| DQ gate `BLOCK` | File missing / 0 rows / missing column | Re‑run that domain runner; inspect the named file; the gate prints which file failed. |
| DQ gate `WARN: stale` | Data older than `max_staleness_days` | Re‑download; or, if the source is genuinely weekly (e.g. Pensford), adjust the threshold. |
| Swaps/bonds price with ~0% discounting; 10Y payer "PV" ≈ −fixed·annuity·notional | `pensford_sofr_forward.csv` has one point per snapshot → OIS curve degenerates to 0% (the old collapse bug; downloader now de-dups on `(as_of_date, settlement_date)`) | `python scripts/rebuild_pensford_from_supabase.py` (full path from the live Supabase source). |
| Full book won't price at a date after the last archived Pensford snapshot (esp. credit) | Pensford archive only posts month-end files (lags); credit needs curve `as_of` == valuation date | Rebuild from Supabase (has the current daily curve, e.g. 2026-10-06), then value at that date. |
| Source silently skipped | Optional key unset (EIA/OpenFIGI) | Add the key, or accept the skip — it's by design. |
| S&P CDX annex returns 403 | `cdx_annex` blocks automation | Disabled by default; download the PDF manually if needed. |

**Common to Financial Portfolio Analytics systems:**
- **Stale closes over weekends/holidays** look like "frozen" data — expected; check the calendar
  before assuming a feed broke.
- **Survivorship / corporate actions**: splits & dividends are downloaded separately — a price
  "jump" is usually an unadjusted split, not bad data.
- **Curve points missing at the short end** (e.g. 1M Treasury) propagate into pricing warnings —
  verify the rates feed before blaming the pricer.
