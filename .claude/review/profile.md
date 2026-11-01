---
python: "conda run --no-capture-output -n py312 python"
env: {"PYTHONPATH": "{repo}"}
gate: ["{python} -m ruff check {changed_py}", "{python} -m mypy", "{python} -m pytest -q {tests}"]
tests_map: {"src/pricer/**": ["tests/pricer"], "configs/pricer/**": ["tests/pricer"],
  "src/marketdata/**": ["tests/marketdata"], "configs/marketdata/**": ["tests/marketdata"],
  "src/scenariogen/**": ["tests/scenariogen"], "configs/scenariogen/**": ["tests/scenariogen"], "prompts/**": ["tests/scenariogen/llm"],
  "src/common/**": ["tests/common", "tests/pricer", "tests/marketdata", "tests/scenariogen", "tests/bff"],
  "ui/bff/**": ["tests/bff"], "tests/bff/**": ["tests/bff"], "tests/pricer/**": ["tests/pricer"],
  "tests/marketdata/**": ["tests/marketdata"], "tests/scenariogen/**": ["tests/scenariogen"], "tests/common/**": ["tests/common"]}
contract_surfaces: ["configs/pricer/golden_pvs.json", "configs/pricer/model_inventory.json", "tests/pricer/test_governance.py",
  "src/common/provenance.py", "src/common/snapshot.py", "src/pricer/loader/snapshot.py", "src/pricer/output/**",
  "src/common/config/**", "**/config_models.py", "**/portfolio*.json", "configs/scenariogen/**",
  "src/pricer/settings.py", ".env.example", "docker-compose*.yml", "src/pricer/service.py", "ui/bff/**",
  "src/scenariogen/arag/capabilities.py", "src/scenariogen/factor_keys.py", "src/scenariogen/stress.py"]
hotspots: ["src/common/**", "src/pricer/risk/**", "src/pricer/engine.py", "src/pricer/ql/common/**"]
gate_env_failures: ["FileNotFoundError.*data[/\\\\]", "pensford_sofr_forward\\.csv"]
snapshot_links: ["data"]
criticality: {"outputs": {
    "pv": ["src/pricer/engine.py"],
    "risk": ["src/pricer/risk/{var,historical_var,sensitivities,scenarios,pnl_explain,jtd,limits,backtest}.py"],
    "scenarios": ["src/scenariogen/{stress,capital,reverse,dualrag}.py"],
    "scenarios_llm": ["src/scenariogen/llm/flow.py"],
    "sizing": ["src/pricer/sizing/{optimizer,book_variants}.py"],
    "governance": ["src/pricer/governance/{golden,ipv}.py"]},
  "data_edges": ["src/marketdata/common/data_store.py -> src/pricer/loader/**",
    "src/marketdata/common/data_store.py -> src/pricer/risk/factor_history.py",
    "src/marketdata/common/data_store.py -> src/scenariogen/{news/**,geopolitical.py,throughput.py,crowding.py,implied_corr.py,liquidity.py,options/**}",
    "src/pricer/portfolio/*.json -> src/pricer/loader/**",
    "configs/pricer/** -> src/pricer/config.py", "configs/scenariogen/** -> src/scenariogen/config.py",
    "configs/marketdata/** -> src/marketdata/common/config_loader.py"],
  "non_value": ["src/common/obs/**", "src/pricer/obs/**", "src/marketdata/common/observability.py",
    "src/scenariogen/trace/**", "src/scenariogen/llm/trace.py", "src/scenariogen/{narrative,report,dashboard}.py",
    "src/pricer/risk/report.py", "src/pricer/governance/report.py"],
  "pins": {"trunk": ["configs/pricer/golden_pvs.json", "configs/pricer/model_inventory.json",
    "src/common/quality/gate.py", "src/marketdata/common/errors.py"]}}
---
# faultline — review profile (review-core pack)

The frontmatter drives the review-core scripts (gate, test scoping, blast map). This prose is
passed to reviewers verbatim.

## Coding standard

Review touched code against the Google Python Style Guide with the house deviations in the global
`rules/python.md` (refactored units in full, small edits on the changed lines only), plus
`.claude/rules/quant.md`. Tool-checked rules (ruff, mypy, `tools/standards/check_standards.py`) are
not review findings unless the tools were bypassed or their config loosened.

## Data/runtime availability

- `data/` is gitignored. `tests/pricer/*_real_data*`, `governance_runner`, `risk_runner` and
  scenario runs need `data/marketdata/`. When it is absent those tests fail with
  `FileNotFoundError` (e.g. `pensford_sofr_forward.csv`). Treat that as an environment gap,
  record it under **Skipped**, and never count it as a pass or as caused by the change.
- Governance proof (only when data is present): `python -m src.pricer.governance_runner`, where
  exit 0 means golden PVs are within tolerance.
- The conftest teardown swallows pytest's summary line, so trust the exit code, not the text.
- ARag (= the XbrlRag service) and MacroRAG are external services, and unit tests use fakes.
  Never call live services or OpenAI during a review.

## Contract-surface notes (beyond the globs)

- **`golden_pvs.json`** moves in lockstep with `tests/pricer/test_governance.py::VDATE`, and
  every re-mark needs a `rebaseline_history` entry.
- **`RunProvenance`** stamps git SHA, config hash, QuantLib version and portfolio version.
- **Snapshot id:** `_declared_inputs` decides which files are fingerprinted. `FactorHistory`
  snapshots are pinned in risk and scenarios.
- **`ScenarioStressResult`:** its optional blocks must be `None` when disabled, which keeps
  the report byte-identical.
- **BFF:** `ui/bff/**` must stay QuantLib-free (`tests/bff/test_bff_quantlib_free.py`).
- **Capabilities catalog:** `capabilities.py` is the single catalog behind the REST and MCP
  surfaces (a drift test enforces this).

## Terminology (do not flag)

- **ARag was renamed XbrlRag.** faultline code, modules (`scenariogen/arag/`), config keys
  and docs still say `arag`/ARag; treat them as the same service. Flag only a change that
  mixes both names *within* one contract.
- **QuantLibPricer was renamed faultline.** Leftover names are not findings. Some design docs
  keep the `AITrade_` prefix by design.
- `pricer.obs.*` / `pricer.provenance` are re-export shims; edits belong in `src/common`.

## Exclusions (project-wide non-findings)

- `E402` in runners (deliberate `sys.path` mutation, exempted in `pyproject.toml`).
- Model choices documented in `docs/pricing/` or `docs/design/`. Flag them only when the code
  diverges from the document.
- Golden PV moves that come with a matching `rebaseline_history` entry explaining them.
- The `PRICER_API_KEY` gate being a no-op when unset (documented local-dev behavior).
