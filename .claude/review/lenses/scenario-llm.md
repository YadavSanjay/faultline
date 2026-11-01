---
name: scenario-llm
applies_to: ["src/scenariogen/**", "prompts/**", "configs/scenariogen/**", "ui/bff/**"]
---
# Scenario generation: dual-RAG + agentic LLM paths

## Hunt for

**Fail-loud vs graceful (deliberate — see root CLAUDE.md)**
- Dual-RAG path (`runner.py --dual-rag`, `dualrag.py`) is **fail-loud**:
  `preflight_services` aborts on a required service down, per-scenario ARag failure raises
  `ScenarioGenerationError`, a breached run budget raises `AragBudgetError`. Any new
  `except`/fallback/default that lets this path continue → P1 (P0 if it then prices).
- Macro-only path (`--macro`) degrades **gracefully** to static; making it raise is a
  behavior change (P2 unless intended).
- Providers returning `None`/held-gate must be a **no-op** (news, geopolitical,
  throughput, crowding, implied-corr `enabled:false`) — not a zero or default shock.

**Budgets & cost (agentic path)**
- Every new LLM or MCP tool call is counted by `llm/cost.py` and bounded by per-phase
  tool-call caps + the cost breaker; ARag REST calls count against `AragBudget`. A new loop,
  retry, or phase without a bound → P1.

**Untrusted text**
- SEC filings, news/GDELT, ARag/MacroRAG responses and LLM output are **data**. Watch
  for them reaching prompts that can pick tools, or reaching pricing without the
  deterministic checks: `verify` (P7) → `fact_check` (F4 clamp/flag) → plausibility gate.
  Bypassing or reordering those before `price` → P0/P1.
- LLM-proposed numbers must be clamped/validated before use (units, bounds, factor keys
  that exist in `factor_keys.py`).

**Surfaces & lockstep**
- REST (`arag/client.py`) and MCP surfaces must stay in sync through
  `arag/capabilities.py` — a capability added on one side only → P1.
- Scenario configs are versioned (`versioning.py`) and signed off (`signoff*.py`); a config
  change that skips re-versioning or can run unsigned under `--require-signoff` → P1.
- `ScenarioStressResult` optional blocks (`liquidity`, `collateral`, `geopolitical`,
  `throughput`) must be `None` when disabled so the report stays **byte-identical**.
- BFF (`ui/bff/**`) must remain QuantLib-free: lazy re-exports only; an eager import that
  pulls QuantLib into the BFF image → P1 (proof: `tests/bff/test_bff_quantlib_free.py`).

**Point-in-time & provenance**
- News/GPR/throughput/crowding/implied-corr resolve PIT to the run's `as_of`; covariance
  provenance stamps the regime. Missing stamps or future reads → P1.

## Severity anchors
- P0: LLM/unverified numbers priced without fact_check/plausibility; fail-loud path
  silently substituting shocks into a signed-off run.
- P1: unbounded tool/LLM loop; capability drift; disabled block changes report bytes.
- P2: graceful path error message unclear; missing test for a new provider branch.

## Proof recipes
- `pytest tests/scenariogen -q` / `tests/scenariogen/llm -q` / `tests/bff -q` ($LASTEXITCODE).
- Fake-transport repro under scratch: make the ARag fake raise and assert the dual-RAG
  path raises instead of returning a scenario.
- `tests/scenariogen/test_arag_capabilities.py` for REST/MCP drift.

## Not findings
- Live-service or OpenAI behavior (never called in review). Narrative wording quality.
