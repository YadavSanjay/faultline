# MacroRAG → AITrade Stress Engine: Scenario-Synthesis Endpoint Spec

**Audience:** the MacroRAG module owners.
**Purpose:** define *exactly* what MacroRAG must expose so the AITrade stress engine
(`src/scenariogen/macro/`) can source correlated stress-scenario **drivers** (factor moves +
citations) from MacroRAG. This is the concrete realization of the catalog's §9B "numeric
handoff" / §11.3 "scenario-synthesis generation step."

**Status:** the AITrade consumer is **built and tested** against this contract (client,
field mapping, freshness/citation GATE, provider with graceful fallback). **Update: the endpoint
is deployed and `configs/scenariogen/macro.json` is now `enabled: true`** — the graceful static
fallback still triggers on any failure/gate-miss, but MacroRAG is live.

**Authoritative AITrade-side references** (keep both ends in sync with these):
- consumer client/models: `src/scenariogen/macro/client.py`, `…/models.py`
- field → factor mapping (the controlled vocabulary + units): `src/scenariogen/macro/mapping.py`
- freshness + citation GATE: `src/scenariogen/macro/gate.py`
- merge/fallback policy: `src/scenariogen/macro/provider.py`
- request config (what we send per scenario): `configs/scenariogen/macro.json`

---

## 1. What MacroRAG already implements (reused as-is — no change)

From `src/api/app.py` + `src/api/models.py`, AITrade reuses the live REST surface unchanged:

| Endpoint | Use by AITrade |
|---|---|
| `GET /healthz` | liveness probe before enabling macro sourcing; returns index manifest |
| `POST /query` | the call we extend (see §3); today returns cited prose |
| `GET /citations/{chunk_id}` | optional drill-down on a cited chunk |
| `POST /feedback` | not used by the stress engine |

Reused mechanisms that are correct already:
- **API-key auth** via the `X-API-Key` header (AITrade sends it; `connection.api_key_env` in
  `macro.json` names the env var holding the key).
- **Facet filtering** on `factor_tags`, `entity_tags`, `doc_types`, `publishers`,
  `date_from`/`date_to` — AITrade sends these to target retrieval (see §6).
- The **time-series store**, **plan-execute agent**, and **faithfulness verifier** — these are
  exactly the machinery that should *produce* the numeric shock vector in §3.
- The existing `/query` response fields (`summary`, `chunks[]`, `refused`, …) — we keep
  consuming `summary` (as a human-readable rationale) and `chunks[]` (for freshness).

**Existing `/query` response shape** (mirrored in `models.py::MacroAnswer`):
```jsonc
{
  "summary": "...", "refused": false,
  "details": [{"text": "...", "chunk_ids": ["..."]}],
  "unanswered": [], "used_chunk_ids": ["..."],
  "timeseries": [{"source": "...", "publisher": "...", "n_rows": 12, "note": "..."}],
  "route": "DECOMPOSE", "cached": false,
  "chunks": [{"chunk_id": "...", "source_id": "...", "publisher": "...",
              "publication": "...", "release_date": "2026-06-10", "doc_type": "rule",
              "rerank_score": 0.81}]
}
```

---

## 2. The gap (what is missing today)

1. **No machine-readable shock vector.** `/query` returns prose (`summary`, `details`). AITrade
   needs *numeric, per-field, cited* driver values, not free text. (We deliberately chose **not**
   to NLP-parse the prose — too fragile.)
2. **`timeseries` exposes only `n_rows`, not the values.** Even the numeric hydration path is not
   machine-consumable for driver extraction in the current serialization.
3. **No per-response provenance id** to pin the macro "world view" used (catalog §10 asks for
   versioned world-view snapshots).

All three are closed by adding the **synthesis response** in §3.

---

## 3. What to add — the scenario-synthesis response

### 3.1 Trigger (recommended: extend `/query` with `mode`)

AITrade already calls `POST /query` with an extra body field `"mode": "synthesize"`
(`client.py::MacroRAGClient.synthesize`). Today MacroRAG's pydantic `QueryRequest` ignores
unknown fields, so the flag is silently dropped.

**Action:** add an optional field to `QueryRequest` and branch on it:
```python
class QueryRequest(BaseModel):
    question: str
    filters: Optional[FilterModel] = None
    mode: str = "answer"          # NEW: "answer" (default) | "synthesize"
```
When `mode == "synthesize"`, run the normal retrieve→(agent)→generate pipeline **and**
additionally emit a top-level **`shock_vector`** object (§3.2). Keep all existing response
fields. *(Acceptable alternative: a dedicated `POST /synthesize` route returning the same body;
if you choose that, tell us and we change one path string in `client.py`. The `mode`-on-`/query`
approach is preferred — it reuses your auth/rate-limit/caching/trace wiring untouched.)*

### 3.2 The `shock_vector` object (the core deliverable)

Add to the `mode=="synthesize"` response:
```jsonc
{
  // ... all existing /query fields (summary, refused, chunks[], ...) ...
  "world_view_id": "macro-2026-06-18",          // OPTIONAL but recommended (see §5)
  "shock_vector": {
    "<FIELD>": {
      "value": <number>,                         // signed magnitude in `unit`
      "unit": "bp" | "pct" | "pts" | "rel",      // see §4
      "chunk_ids": ["<chunk_id>", "..."],        // ≥1; MUST also appear in chunks[]
      "rationale": "<= 1 short sentence"          // optional, human context
    }
    // ... one entry per supported field ...
  }
}
```

Parsed by `models.py::MacroSynthesis.from_response`. A field with no `chunk_ids`, or whose
`chunk_ids` are not present (with a `release_date`) in `chunks[]`, **fails the GATE** and is
discarded by AITrade (§5).

---

## 4. Controlled field vocabulary, units & sign conventions (MUST match exactly)

This is the heart of the contract. `mapping.py` translates each field to an AITrade canonical
risk factor. **Only these field names are consumed**; unknown fields are ignored.

| `shock_vector` field | Meaning | `unit` | Sign convention (POSITIVE means…) | AITrade factor |
|---|---|---|---|---|
| `UST_2Y` `UST_5Y` `UST_10Y` `UST_30Y` | UST par-yield change at that tenor | `bp` | yields **rise** | `ir:DGS2/5/10/30` |
| `UST` | parallel UST shift (all tenors) | `bp` | yields **rise** | all `ir:DGS*` nodes |
| `IG_OAS` | IG credit OAS change | `bp` | spreads **widen** | `credit` |
| `HY_OAS` | HY credit OAS change | `bp` | spreads **widen** | `credit` *(used only if `IG_OAS` absent)* |
| `MOVE` | rates-vol change | `rel` *(preferred)* or `pts` | vol **rises** | `ratesvol` |
| `VIX` | equity-vol change | `pts` | vol **rises** | `eqvol` |
| `USDTWD` | USD/TWD spot move | `pct` | **TWD depreciates** (more TWD per USD) | `fx:TWD` |
| `USDJPY` | USD/JPY spot move | `pct` | **JPY depreciates** | `fx:JPY` |
| `XCCY_BASIS` | USD cross-currency basis | `bp` | — | *(no factor today — dropped; see §7)* |

**Unit semantics (exactly how AITrade converts — `mapping.py::_convert`):**
- `bp` → decimal × `1e-4` (e.g. `UST_10Y: {value: 75, unit: "bp"}` → +0.0075 rate).
- `pct` → decimal ÷ `100` (e.g. `USDTWD: {value: 8, unit: "pct"}` → +0.08 spot return).
- `pts` for **`VIX`** → ÷ `100` (vol points → decimal IV; `VIX: {value: 20, unit:"pts"}` → +0.20).
- `MOVE`: send **`unit: "rel"`** with a relative change (e.g. `0.40` = +40% normal-vol) — this is
  unambiguous and preferred. If you must send `pts` (index points on a ~100 base), AITrade
  divides by `units.move_base` (default `100.0` in `macro.json`); coordinate the base if it differs.

**Equity legs are NOT expected from MacroRAG.** Per-name equity (`eq:NVDA`, `eq:TSM`, …) is set
house-side / derived by AITrade's covariance propagation. Do **not** rely on MacroRAG to drive
single-name equity (you may include an equity field; AITrade currently ignores it — see §7).

---

## 5. Citation, freshness & provenance requirements (the GATE)

AITrade applies a GATE (`gate.py`) before using any macro driver; failing it ⇒ silent fallback to
static drivers. To pass, MacroRAG must guarantee:

1. **Citation coverage.** Every `shock_vector` field carries **≥ `min_chunks_per_driver`**
   `chunk_ids` (default **1**, `macro.json::gate`).
2. **Cited chunks are present & dated.** Every `chunk_id` referenced by any field **must appear in
   the response `chunks[]`** with a populated **`release_date`** in ISO `YYYY-MM-DD`. Include the
   citing chunks in `chunks[]` even if they did not surface in the prose answer.
3. **Freshness.** AITrade computes `age = valuation_date − release_date`; any cited chunk older
   than **`freshness_days`** fails the GATE. NOTE: the code default is 120, but the deployed
   `configs/scenariogen/macro.json` sets `freshness_days: 550` — config wins. Prefer the most
   recent primary source (catalog §10).
4. **Refusal.** If evidence is insufficient, set `"refused": true` and/or return an empty/absent
   `shock_vector`. AITrade treats this as "no macro signal" and falls back — this is expected and
   safe, not an error.
5. **Provenance (recommended).** Return a stable `world_view_id` (or reuse the index-manifest
   version) identifying the corpus snapshot behind the numbers, so an AITrade stress run can pin
   `{scenario_version, cov_id, world_view_id, snapshot_id, …}` for full reproducibility
   (catalog §10 "versioned world-view snapshots"). If omitted, AITrade still works but loses this
   pin.

**HTTP semantics:** return `200` for a normal answer (including `refused: true`). AITrade treats
any non-2xx (401 bad key, 429 rate-limited, 5xx) as failure → fallback. Keep auth/rate-limit
behavior as-is.

---

## 6. Facet vocabulary AITrade sends (please align retrieval)

For each scenario, AITrade posts a `filters` block (from `macro.json`). MacroRAG retrieval should
recognize these tag values (or tell us your canonical vocabulary so we re-map):

- **factor_tags:** `fx`, `credit_spread`, `rates_vol`, `equity_vol`, `term_premium`,
  `ai_capex`, `data_center_power`
- **entity_tags:** `taiwan`, `china`, `TSMC`, `nvidia`, `hyperscalers`, `semiconductors`
- **publishers:** `Federal Reserve`, `U.S. Treasury`, `CBO` (exact strings as MacroRAG stores them)

If any of these don't exist in your controlled vocabulary, that's a coordination item — either add
synonyms on your side or send us the correct tokens and we'll update `macro.json`.

---

## 7. Known mapping limitations (so MacroRAG knows the consumer's behavior)

- **Single credit factor.** The book has one systematic `credit` driver, so `IG_OAS` is mapped;
  `HY_OAS` is consumed **only when `IG_OAS` is absent**. Send both if you have them — IG wins.
- **`XCCY_BASIS` is dropped today** (the book holds basis at 0). Send it if available; AITrade will
  start consuming it when the basis factor is added. No harm in including it.
- **Equity fields are ignored** (see §4). A future AITrade extension may consume an `AI_EQUITY`
  systematic-index field; coordinate before adding.
- **Tenors:** only `2Y/5Y/10Y/30Y` UST nodes are consumed (the curve control grid). Other tenors
  are ignored; use `UST` (parallel) for a single-number curve shift.

---

## 8. Worked example — Taiwan scenario (use as an acceptance test)

**AITrade sends** (this is the live `taiwan_geopolitical` entry in `macro.json`):
```jsonc
POST /query
X-API-Key: <key>
{
  "question": "Given an escalation of US-China semiconductor export controls and a Taiwan
               foundry-supply disruption, quantify the 10-day shocks to USD/TWD, USD/JPY,
               IG and HY credit spreads (OAS), the MOVE index, VIX, and the UST 2/5/10/30
               curve, with citations.",
  "filters": {
    "factor_tags": ["fx","credit_spread","rates_vol","equity_vol","term_premium"],
    "entity_tags": ["taiwan","china","TSMC"]
  },
  "mode": "synthesize"
}
```

**MacroRAG must return** (shape; values illustrative):
```jsonc
{
  "summary": "Export-control escalation + foundry disruption: TWD depreciates, IG/HY widen, ...",
  "refused": false,
  "world_view_id": "macro-2026-06-18",
  "shock_vector": {
    "USDTWD": {"value": 8,   "unit": "pct", "chunk_ids": ["fedreg::001"], "rationale": "BIS rule"},
    "USDJPY": {"value": 5,   "unit": "pct", "chunk_ids": ["boj::012"]},
    "IG_OAS": {"value": 90,  "unit": "bp",  "chunk_ids": ["gfsr::004"]},
    "HY_OAS": {"value": 240, "unit": "bp",  "chunk_ids": ["gfsr::004"]},
    "MOVE":   {"value": 0.30,"unit": "rel", "chunk_ids": ["tbac::007"]},
    "VIX":    {"value": 18,  "unit": "pts", "chunk_ids": ["fsr::009"]},
    "UST_10Y":{"value": -35, "unit": "bp",  "chunk_ids": ["fsr::009"]}
  },
  "chunks": [
    {"chunk_id": "fedreg::001", "publisher": "BIS-Commerce", "release_date": "2026-06-10", "doc_type": "rule"},
    {"chunk_id": "boj::012",   "publisher": "Bank of Japan", "release_date": "2026-06-14"},
    {"chunk_id": "gfsr::004",  "publisher": "IMF",           "release_date": "2026-04-20"},
    {"chunk_id": "tbac::007",  "publisher": "U.S. Treasury", "release_date": "2026-05-01"},
    {"chunk_id": "fsr::009",   "publisher": "Federal Reserve","release_date": "2026-05-09"}
  ]
}
```

**AITrade then derives** (via `mapping.py`, after GATE passes): `fx:TWD=+0.08`, `fx:JPY=+0.05`,
`credit=+0.0090` (IG; HY ignored), `ratesvol=+0.30`, `eqvol=+0.18`, `ir:DGS10=−0.0035`. These
become the scenario's **macro drivers**; the conditional-covariance engine propagates the rest and
the per-name equity legs come from the house definition. (Equity is intentionally absent above.)

---

## 9. Cadence & operational expectations (catalog §9B/§10)

- **Weekly** invocability: AITrade re-queries `mode=synthesize` on a weekly cycle to refresh
  vectors; scenario *definitions* refresh quarterly. MacroRAG should be safe to call repeatedly
  (caching is fine; just keep `release_date`s accurate for freshness).
- **Release-calendar awareness** (FOMC/SEP, refunding, AEO/WEO/GFSR windows) lives MacroRAG-side so
  a weekly call picks up new primary sources.
- **Primary-source preference & recency-conflict surfacing** per catalog §10 — reflected in which
  `chunk_ids` you cite.

---

## 10. Acceptance checklist for MacroRAG

- [ ] `QueryRequest` accepts `mode` (or a `POST /synthesize` route exists).
- [ ] `mode=synthesize` returns a top-level `shock_vector` object alongside the existing fields.
- [ ] Field names/units/signs conform **exactly** to §4.
- [ ] `MOVE` returned as `unit:"rel"` (or coordinate `move_base`).
- [ ] Every field has ≥1 `chunk_id`; every cited chunk is in `chunks[]` with ISO `release_date`.
- [ ] `refused:true`/empty `shock_vector` when evidence is insufficient (no exception).
- [ ] `200` on success (incl. refusal); auth/rate-limit unchanged.
- [ ] (Recommended) `world_view_id` for provenance pinning.
- [ ] factor_tags/entity_tags/publishers in §6 are recognized (or vocabulary reconciled).
- [ ] The §8 Taiwan example round-trips: AITrade maps it to the listed canonical drivers and the
      GATE passes with fresh, cited chunks.

Once these are met, AITrade flips `configs/scenariogen/macro.json → connection.enabled: true`
(and points `base_url` at the deployed service) and `python -m src.scenariogen.runner --macro`
will source live, cited, gated macro drivers — no further AITrade code change required.
```
