# CLAUDE.md — Faultline

Project-wide guidance for Claude Code. Module-specific `CLAUDE.md` files under
`src/marketdata/`, `src/pricer/`, `src/scenariogen/`, and `ui/` add detail when you work there.
For humans: [README.md](README.md) (overview) and [docs/guides/README.md](docs/guides/README.md)
(operating).

## What this is

A file-based QuantLib pricing / risk / stress-testing platform (Python 3.12). Four data-pipeline
subsystems — **marketdata → pricer (incl. risk, sizing, governance) → scenariogen → ui** — chained
through an immutable, content-addressed market-data **snapshot**. No database server: everything
runs from source over CSV + Parquet under `data/` (gitignored), queried in-process with DuckDB.

## Environment — read first

- **Python: always** `conda run --no-capture-output -n py312 <cmd>`. `conda` is on the **PowerShell**
  PATH, **not** the Bash PATH — use the PowerShell tool for Python/pytest/ruff/mypy. QuantLib is the
  **PyPI wheel** (`pip`, `pypi_0`) installed **into** the `py312` conda env — not a conda-forge
  package — so `import QuantLib` still fails outside `py312`, and the container images use the same
  wheel (conda-forge `quantlib` is C++-only, no Python bindings).
- **PYTHONPATH:** module runs need the repo root on the path: `$env:PYTHONPATH = $PWD.Path` from the repo root. Runners mutate `sys.path` themselves (hence the `E402` ruff exemptions in `pyproject.toml`).
- **Node** (UI only): v24.14.0 (not necessarily on PATH).

## The gate — run before you commit

**Test-first:** every new change or bug fix starts with locked spec tests (`spec/<id>` → `impl/<id>`,
spec in `docs/specs/<id>.md`) — use the `tdd-spec` skill; pack and oracle catalogue in
[.claude/tdd/](.claude/tdd/oracles.md). Spec tests live in `tests/spec/` and run only through
`python tools/tdd/tdd.py run <id>` (plain `pytest` ignores them).

```powershell
conda run --no-capture-output -n py312 python -m ruff check .
conda run --no-capture-output -n py312 python -m mypy          # strict; files= in pyproject.toml
conda run --no-capture-output -n py312 python -m pytest -q     # or a scoped subdir (below)
```

- **Standards:** Google Python Style Guide with house deviations, for new and touched code (global
  `rules/python.md`); quant rules in `.claude/rules/quant.md`. Hooks auto-fix on edit and report at
  Stop (advisory). Existing findings live in `ruff-burndown.toml`, which may only shrink — never add
  entries, ignores or overrides to get green; `tools/standards/check_standards.py ratchet` checks it.
- **ruff**: line-length 120; standards tier 1 + `NPY PD` on top of `E W F I UP B C4 SIM RET PIE PERF RUF`
  (see `pyproject.toml`). Auto-fix with `ruff check --fix`. No formatter.
- **mypy**: `strict = true` over `src/common`, `src/pricer`, `src/scenariogen`, `src/marketdata`,
  `tools/standards` (the one relaxation is `disallow_any_generics = false`). `mypy` follows imports, so it type-checks
  the whole tree — a run can surface a pre-existing error in a file you didn't touch; fix only what
  you own unless asked otherwise.
- **pytest**: full suite is green. A test that reads `data/marketdata` carries `needs_market_data` and
  skips until the core feeds are in; `FAULTLINE_REQUIRE_DATA=1` makes those tests fail instead. Scope while
  iterating: `pytest tests/pricer`, `tests/marketdata`,
  `tests/scenariogen`, `tests/scenariogen/llm`, `tests/bff`. A custom conftest teardown swallows the
  summary line — check `$LASTEXITCODE` (0 = pass) rather than grepping for "passed".
- **CI** (`.github/workflows/pricer-ci.yml`): the `standards` job (ruff, mypy, new-code and ratchet
  checks; advisory) and the `offline-tests` job (pytest with no market data, so the marked tests skip)
  run on every push and PR. The `lint-type-test` job is **manual-only** (`workflow_dispatch`) because it
  needs the gitignored `data/marketdata/`; it runs `ruff check .` + `mypy` +
  `pytest tests/pricer tests/marketdata` with `FAULTLINE_REQUIRE_DATA=1`.

## Conventions that hold everywhere

- **Reproducibility spine.** Results are stamped with `RunProvenance` (git SHA, config hash,
  QuantLib version, portfolio version) + a content-addressed market-data `snapshot_id` (risk/scenarios
  also pin a `FactorHistory` snapshot). Don't break this — it's what makes a run auditable.
- **Config is validated.** JSON config is loaded through pydantic models (`src/common/config/`,
  `*/config_models.py`); a mistyped key raises `ConfigError`, not a silent default. Service *location*
  config supports `${VAR:-default}` env expansion (`src/common/config/env.py`).
- **Shared code lives in `src/common/`** (obs/logging/metrics, config, provenance, quality gate,
  snapshot, store/retention, **storage** — the dual-mode local-FS|S3 façade — and **jobs** — the
  deploy-mode job runner). Several `pricer.obs.*` / `pricer.provenance` modules are thin re-export
  shims to it — edit the `src/common` original.
- **Subsystems meet through data files and `src/common` only.** `src/pricer` never imports
  `src/marketdata`, and `src/common` imports no subsystem; a rule both sides need (e.g. which DTCC SDR
  records are prices, `src/common/sdr_prints.py`) lives in `src/common`.
  `tests/unit/test_import_boundaries.py` enforces this.
- **Dual-mode storage.** All `data/` I/O routes through `src/common/storage/`, keyed by
  `PRICER_DEPLOY_MODE` (`local`|`aws`) + `PRICER_DATA_URI` (local path | `s3://…`). Defaults =
  today's local behavior byte-for-byte; don't reintroduce raw `project_root/"data"/…` paths — build
  locations with `storage.data_base(root)`/`storage.join(...)` and do I/O via the façade. See
  [docs/guides/deployment_guide.md](docs/guides/deployment_guide.md) and the AWS design doc.
- **Fail-loud vs graceful is deliberate.** The dual-RAG/ARag scenario path and the ingest floors are
  fail-loud (raise, don't silently substitute); the macro-only path degrades gracefully to static.
  Don't "helpfully" swallow a fail-loud error.
- **Outputs are provenance-stamped and idempotent** (upserts / atomic writes). Re-running is safe.
- **Match the surrounding code** — comment density, naming, dataclass/pydantic idioms, and the
  existing test style (fakes behind Protocols, no network in unit tests).

## Git

- **Never commit or push directly to `main`** — not even when asked to "commit and push". Always
  create a feature branch (`feat/…`, `fix/…`, `chore/…`, `docs/…`), commit there, push the branch,
  and hand over the PR link. The user reviews the PR and merges it; do not merge to `main` yourself
  (no `git merge`/fast-forward into `main`, no push to `origin main`).

Commit/push only when asked. **Never add a `Co-Authored-By: Claude …` line (or any other
Claude/AI attribution) to commit messages or PR descriptions.** Prefer a message file
(`git commit -F`) for multi-line messages — PowerShell here-strings mangle `!=`, `<>`, and `[]`.

## Known gotchas

- **Golden PVs** are pinned to the portfolio default (2026-10-06, core book), so a default
  `governance_runner` (no `--valuation-date`) passes. If you change pricing/data and a golden breaks,
  review the driver first — rebaseline `configs/pricer/golden_pvs.json` **only when asked**, recording
  a `rebaseline_history` entry, and move `tests/pricer/test_governance.py`'s `VDATE` in lockstep if the
  baseline date changes.
- **Two ARag access surfaces by design** (deterministic REST + agentic MCP) — keep them in sync via
  the single catalog `src/scenariogen/arag/capabilities.py` (a drift test enforces it).
- **`data/` is gitignored** — pages/tests that read it are empty/skipped until a runner populates it.
- Docs: guides link **file-relative** (`../design/…`, `../../src/…`), not repo-root-relative.

## Where things are

Entry points, per-module structure, and doc-vs-code specifics are in each module's `CLAUDE.md`.
Operating detail (flags, troubleshooting) is in `docs/guides/`; per-trade pricing methodology is in
`docs/pricing/`; architecture/rationale is in `docs/design/`.
