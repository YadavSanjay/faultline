# Market Data — UI Plan

> **Parent:** `ui/docs/UI_DESIGN.md` (§5 Market Data). Siblings: `PRICING_UI.md`, `RISK_UI.md`, `SCENARIOGEN_UI.md`.
> **Requirement (verbatim):** *"feeds that are available, linkage to sources, then details about each feed, and their relationship with portfolio/trades."*
> **North star:** a market-data steward and a curious desk user should move top→down — *which feeds exist → where each comes from → what's in it → which trades depend on it* — and always see whether the data is fresh, clean, and traceable to source.
> **Grounded in:** `src/marketdata/<asset_class>/` downloaders + runners, the per-CSV `<file>.provenance.json` sidecars, the DQ gate (`src/common/quality/gate.py`), content-addressed snapshots (`src/common/snapshot.py`), and the `portfolio[]` linkage in each `configs/marketdata/<asset>/…json`.
> **Backend reality:** market data is **library-only — no REST at all.** This is a pure greenfield read layer; the whole plan is a BFF over files (configs, CSVs, provenance sidecars, DQ output, snapshot manifests, run audit Parquet).

---

## 1. UX philosophy — "follow the data, top to bottom"

The Market Data ladder is the cleanest of the four: it literally follows a datum from source to trade.

| Altitude | Question | Page | Who |
|---|---|---|---|
| **L1 Catalog** | *What feeds do we have, from where, and are they healthy?* | Feed Catalog | steward, desk |
| **L2 Feed** | *Everything about one feed — source, provenance, quality, consumers* | Feed Detail | steward |
| **L3 Data** | *What are the actual values?* | Data Viewer | steward, quant |
| **Cross** | *Which trades depend on which feeds?* | Feed ↔ Trade Coverage | steward, risk |

Three commitments keep it **intuitive**:
1. **Source is always visible.** Every feed shows its origin (NY Fed, FRED, Pensford, DTCC, ECB, CBOE, Yahoo, EIA…) and a one-click path to its provenance (URL, fetch time, content hash).
2. **Freshness & quality are never hidden.** A `DqBadge` (fresh ✓ / stale ⚠ / blocked ✗) rides every feed at every altitude — the steward's primary job is "is the data good?"
3. **The trade relationship is bidirectional.** From a feed you see its consuming trades; from a trade you see its feeds — the bridge into Pricing lineage.

---

## 2. Personas & jobs-to-be-done

- **Market-data steward (business internal):** "Is everything fresh and passing DQ? What's stale or blocked, and which trades does that affect?" → L1 + DQ dashboard + coverage.
- **Desk / quant (business end user):** "Where does the SOFR curve / this vol surface come from, and what's the latest value?" → L2 + L3.
- **Platform / SRE (technical):** "Trace an ingestion run; diff two snapshots; which adapter consumes this CSV." → Technical lens.

---

## 3. Market Data workspace — navigation map

```
MARKET DATA                                       [Business ⇄ Technical]
│
├─ BUSINESS
│   ├─ MD-B1  Feed Catalog        (L1)  feeds × asset class, source, freshness, DQ, used-by-trades
│   ├─ MD-B2  Feed Detail         (L2)  source config · provenance · DQ checks · consumers · history
│   ├─ MD-B3  Data Viewer         (L3)  the actual series / curve, PIT-aware
│   ├─ MD-B4  Feed ↔ Trade Coverage     bidirectional dependency matrix
│   └─ MD-B5  DQ Dashboard               portfolio-wide quality posture
│
└─ TECHNICAL
    ├─ MD-T1  Source / Adapter Explorer  downloader → CSV → loader adapter → domain data class
    ├─ MD-T2  Ingestion Run Trace        one pipeline run: per-source fetch → DQ gate → snapshot
    └─ MD-T3  Provenance / PIT Explorer   snapshot manifests, diff two snapshots
```

Cross-links: feed → MD-B2; MD-B2 consumer → **Pricing ▸ Lineage** (where the feed is used); coverage cell → MD-B2 + Pricing trade; DQ finding → MD-B2 + ingestion run; provenance chip → **System/Ops ▸ Run**.

---

## 4. Business pages — the plan

### MD-B1 · Feed Catalog (L1: "feeds available, linkage to sources")

**Data:** `GET /md/feeds` — assembled from each asset-class config (the `sources` + `portfolio` blocks) joined with each CSV's `<file>.provenance.json`.

**Layout**
- **Grouped grid by asset class** — Rates · Credit · Equity · FX · Options · Bonds · Stress · Reference. Each feed row: **name**, **source** (origin badge), enabled flag, **last fetch** (`fetched_at`), **freshness** (days vs valuation date), **row count**, **date range** (`date_min`–`date_max`), **DQ badge**, **#consuming trades**.
- **Filters:** asset class, source, status (fresh / stale / blocked), "used by trade", search.
- Row click → MD-B2.

**Intuitive touches**
- Source origin shown as a recognizable badge (e.g. "NY Fed", "Pensford", "DTCC", "CBOE") so "linkage to sources" is immediate.
- Stale/blocked feeds float to a visible "Needs attention" group at the top.

```
Market Data ▸ Feed Catalog                       val 2026-05-29 · snapshot 56a863cf
┌ RATES ────────────────────────────────────────────────────────────────────────┐
│ feed                     source     fetched     fresh  rows  range          DQ  ▸ │
│ Pensford SOFR Forward    Pensford   2d ago      ✓ 0d   312   2024→2026-05  ✓  3 │
│ NY Fed SOFR              NY Fed     today        ✓ 0d   980   2022→2026-06  ✓  4 │
│ FRED rates (DGS…)        FRED       today        ✓ 0d  1.2k   2010→2026-06  ✓  6 │
│ DTCC SDR IRS             DTCC       6d ago      ⚠ 6d   4.1k   2023→2026-06  ⚠  2 │
└─────────────────────────────────────────────────────────────────────────────────┘
┌ FX ─────────  CBC TWD NDF · CBC · ✗ blocked (stale 9d > 7d)  → 1 trade (#26)  ───┐
```

### MD-B2 · Feed Detail (L2: "details about each feed")

**Data:** `GET /md/feeds/{id}` — the provenance sidecar + the config source block + latest DQ `CheckResult`s + the consuming trades + ingestion-run history.

**Sections**
1. **Source linkage** — `source_url`, auth type, timeout, rate-limit/retry, output file(s). (From the asset-class `SourceConfig`.)
2. **Provenance** — `fetched_at`, `git_sha`, `content_sha256`, `row_count`, `columns`, `date_min/date_max`.
3. **Data quality** — the gate's `CheckResult`s for this file: present · readable · min_rows · required_columns · staleness · null_fraction, each with severity (ok/warn/block) and message.
4. **Consumers** — the trades that depend on this feed (primary vs fallback), each linking to **Pricing ▸ Lineage** for that trade. ("relationship with portfolio/trades")
5. **History** — recent ingestion runs that wrote this feed (link to MD-T2).
6. CTA **"View data" → MD-B3**.

### MD-B3 · Data Viewer (L3: "the actual values")

**Data:** `GET /md/feeds/{id}/series` — the CSV contents (paginated / down-sampled).

- **Curves** (SOFR forward, UST par) → `CurveChart` (value by tenor) — the *same component* pricing lineage reuses, so a steward and a quant see the curve identically.
- **Series** (VIX history, FX rates, CDS spreads, power demand) → `SeriesChart` with a date-range selector.
- **PIT-aware:** an "as-of" selector mirrors the pricing snapshot pick (latest on/before date), so the steward sees exactly what a pricing run would have consumed.

### MD-B4 · Feed ↔ Trade Coverage ("relationship with portfolio/trades")

**Data:** `GET /md/coverage` — built from the `portfolio[]` arrays across asset-class configs.

- **Bidirectional matrix:** rows = trades (1–38), columns = feeds; cell = primary / fallback dependency. Select a **trade** → highlight its feeds; select a **feed** → highlight dependent trades.
- This is the explicit bridge: clicking a cell jumps to the feed (MD-B2) or the trade's pricing lineage (PR-B3 market-data inputs).

**Intuitive touch:** a coverage gap (a trade with a stale/blocked primary feed) is flagged red — "this trade is at risk because its data is stale."

### MD-B5 · DQ Dashboard (internal maintenance)

**Data:** `GET /md/dq` — the gate output across declared files.

- Portfolio-wide posture: counts by severity, stalest feeds, blocking gaps; per-file detail (staleness days, null %, row count, missing columns), filterable by asset class. Each finding → MD-B2 + the ingestion run that produced it. Mirrors the `marketdata_dq_total{severity}` metric.

---

## 5. Technical lens — the plan

- **MD-T1 Source / Adapter Explorer:** the data path as a clickable chain — `src/marketdata/<asset>/<source>_downloader` → CSV (+ provenance sidecar) → `src/pricer/loader/<asset>_adapter` → domain data class (`ParOISCurveData`, `EquitySpotData`, `CdsSpreadData`, …). Shows source config, transport (retry/backoff/rate-limit), and the `pick_snapshot` PIT rule each adapter applies.
- **MD-T2 Ingestion Run Trace:** one `python -m src.marketdata.<asset>.runner` run as a `RunTrace`: pipeline start → per-source fetch (status, rows, duration) → DQ gate → snapshot fingerprint → run-audit manifest (`data/marketdata/store/runs/`). Surfaces structured-log context (run_id, source, asset_class).
- **MD-T3 Provenance / PIT Explorer:** browse snapshot manifests (`<snapshot_id>.json`: per-file sha256, size, mtime, missing). **"Diff two snapshots"** shows exactly which feeds changed between two pricing runs — the data-side answer to "why did a price move?"

---

## 6. BFF contracts (Market Data) — pure greenfield read layer

All new; the BFF reads files. No engine changes.

| Need | Endpoint | Backed by |
|---|---|---|
| Feed catalog | `GET /md/feeds?valuation_date=` | asset configs (`sources`,`portfolio`) + provenance sidecars |
| Feed detail | `GET /md/feeds/{id}` | `<file>.provenance.json` + source config + DQ + consumers |
| Feed series/curve | `GET /md/feeds/{id}/series?as_of=` | the CSV (down-sampled/paginated) |
| DQ posture | `GET /md/dq?valuation_date=` | DQ gate output (`CheckResult[]`) |
| Coverage matrix | `GET /md/coverage` | `portfolio[]` across asset configs |
| Snapshots / diff | `GET /md/snapshots`, `/md/snapshots/{id}`, `/md/snapshots/diff?a=&b=` | snapshot manifests |
| Ingestion runs | `GET /md/runs?asset_class=` | `data/marketdata/store/runs/*.parquet` |

```jsonc
// GET /md/feeds → MD-B1 (one row per feed)
{ "feeds": [
  { "id":"rates/pensford_sofr_forward.csv","asset_class":"rates",
    "name":"Pensford SOFR Forward","source":"Pensford","enabled":true,
    "fetched_at":"2026-06-19T…","freshness_days":0,"row_count":312,
    "date_min":"2024-01-02","date_max":"2026-05-29","dq":"ok",
    "used_by_trades":["TRADE_22","TRADE_23","TRADE_25"] } ] }

// GET /md/feeds/{id} → MD-B2 (adds source config, full provenance, DQ checks, consumers, history)
{ "id":"rates/pensford_sofr_forward.csv", "source":{ "url":"https://…","auth":"none",
    "timeout_s":30,"rate_limit_delay_s":1.0,"output_file":"pensford_sofr_forward.csv" },
  "provenance":{ "fetched_at":"…","git_sha":"…","content_sha256":"9f3a…","row_count":312,
                 "columns":["date","1Y","2Y","5Y","10Y"],"date_min":"…","date_max":"…" },
  "dq":[ { "check":"staleness","severity":"ok","message":"0 days old" },
         { "check":"required_columns","severity":"ok","message":"all present" } ],
  "consumers":[ { "trade_id":"TRADE_22","role":"primary" } ],
  "history":[ { "run_id":"…","fetched_at":"…","rows":312,"status":"ok" } ] }
```

---

## 7. Build plan

Market Data is **all-greenfield BFF + a clean ladder UI** — straightforward but read-layer-heavy.

**Step 1 — BFF read layer.** `GET /md/feeds`, `/md/feeds/{id}`, `/md/feeds/{id}/series`, `/md/coverage`, `/md/dq`, `/md/snapshots`. Logic: parse asset configs, read provenance sidecars, run/serve DQ, project `portfolio[]` into coverage. No engine changes.
**Step 2 — MD-B1 Feed Catalog.** The L1 grid + filters + status grouping. Immediate steward value.
**Step 3 — MD-B2 Feed Detail.** Source linkage + provenance + DQ + consumers + history.
**Step 4 — MD-B3 Data Viewer.** Reuse `CurveChart` / `SeriesChart` (shared with Pricing).
**Step 5 — MD-B4 Coverage + MD-B5 DQ Dashboard.** The trade-relationship matrix and quality posture.
**Step 6 — Technical lens** MD-T1–T3 (adapter explorer, ingestion trace, snapshot diff).

**Reuse:** `CurveChart`, `SeriesChart`, `ProvenanceBadge`, `DqBadge`, `RunTrace` are shared with Pricing/System-Ops, so much of the kit pre-exists if Pricing built first.

**Sequencing note:** Market Data is a strong *first* slice if the goal is the gentlest build (clean read-only ladder, no engine changes, no heavy compute) — but it delivers steward value more than headline business value. Pairing it early with Pricing lineage is natural: Pricing's market-data input nodes link straight into MD-B2, so the two reinforce each other.

---

## 8. Open questions / decisions for Market Data

1. **Feed identity:** key feeds by relative CSV path (e.g. `rates/pensford_sofr_forward.csv`) as the stable id — confirm.
2. **Series volume:** down-sample large series server-side (e.g. CBC TWD ~200 pages) with a "download raw" escape — agree.
3. **DQ on-demand vs cached:** serve the last persisted DQ run, or re-run the gate on request? (Recommend cached/persisted, consistent with stored-only.)
4. **Coverage source of truth:** derive purely from `portfolio[]` in configs (recommended) vs also cross-checking the loader's resolved requirements at runtime.
