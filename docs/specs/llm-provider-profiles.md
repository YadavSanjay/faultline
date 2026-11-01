# llm-provider-profiles: model registry, provider profiles and governed pricing

## Requirement

Step 2 of [the multi-provider design](../design/llm_multi_provider_design.md) (§6, §7; decisions D2,
D5, D8, D9, D10).

Today `configs/scenariogen/llm/llm.json` has one `provider` and a flat `model_tiers` map, loaded
into plain dataclasses that accept any key. `configs/scenariogen/llm/pricing.json` prices input and
output tokens only. A model with no pricing row costs $0, so the budget breaker never trips for it.

When this change is done:

1. **Config shape.** `llm.json` is loaded by `load_llm_config(doc, pricing, *, profile=None,
   environ=None)` from a dict with exactly these top-level keys: `active_profile`, `providers`,
   `models`, `profiles`, `stage_params`, `retry`, `budgets`. Any other key at any level raises
   `ConfigError` (`src.common.config.models.ConfigError`), and so does a missing required key.
   - `providers`: name → `{api_key_env: str, timeout_s: float, base_url: str (optional)}`. Provider
     names are `openai`, `anthropic` and `deepseek`; any other name raises.
   - `models`: model name → `{provider, max_output_tokens: int > 0, json_mode: "json_object" |
     "json_schema", temperature: bool (optional, default true), effort: str (optional),
     thinking: str (optional)}`. `provider` must be a key of `providers`.
   - `profiles`: profile name → a map from **every** stage to a model in `models`. The stages are
     `discover`, `evidence`, `propose`, `calibrate`, `narrate`, `verify` and `attribution`. A missing
     or unknown stage raises.
   - `stage_params`: stage → `{max_tokens: int > 0 (optional), temperature: float (optional)}`. An
     unknown stage raises.
   - `retry`: `{max_attempts: int ≥ 1, min_wait_s: float ≥ 0, max_wait_s: float ≥ min_wait_s}`.
   - `budgets`: `{max_tool_calls_per_phase: int ≥ 1, max_total_cost_usd: float > 0,
     max_scenarios: int ≥ 1, max_candidates: int ≥ 1}`.
   - Every timeout and rate must be a finite number; every timeout must be > 0.
2. **Active profile.** `active_profile` may hold `${VAR}` / `${VAR:-default}` placeholders, which
   are expanded from `environ` (`os.environ` when `None`). The committed value is
   `"${SCENARIOGEN_LLM_PROFILE:-openai}"`. An explicit `profile` argument (the runner's
   `--llm-profile`) overrides it. An unknown profile name raises `ConfigError`.
3. **Per-model rules** (each violation raises `ConfigError`):
   - a stage `temperature` is refused when that stage's model has `temperature: false`;
   - `effort` is allowed only for `anthropic` models (`low`, `medium`, `high`, `xhigh`, `max`) and
     `deepseek` models (`none`, `low`, `high`, `max`). Any other value, or `effort` on an `openai`
     model, raises;
   - `thinking` is allowed only for `anthropic` models: `adaptive`, `between_tools` or `disabled`.
     `between_tools` and `disabled` additionally require that `effort` is absent or one of `low`,
     `medium`, `high`.
4. **Output cap.** `max_tokens_for(stage)` is the stage's `max_tokens`, capped at its model's
   `max_output_tokens` (with a logged warning when capped), or `None` when the stage sets none.
5. **Pricing is complete (D9).** `pricing` maps model name → a rate row, in USD per 1,000 tokens:
   - required: `input`, `output`;
   - optional: `cached_input`, `cache_write`;
   - optional `tiers`: a list of `{above_prompt_tokens: int > 0, ...rates}`;
   - optional `peak`: `{weekdays_utc: ["HH:MM-HH:MM", ...], ...rates}`.

   Every model a profile uses (any profile, not only the active one) must have a row, or
   `load_llm_config` raises `ConfigError` naming the model. Unknown keys raise. Rates must be finite
   and ≥ 0. A row may not have both `tiers` and `peak`. A window must have start < end, with both
   times valid 24-hour times.
6. **Cost of one call.** `cost_usd(rates, *, prompt_tokens, completion_tokens,
   cached_prompt_tokens, cache_write_tokens, at)` returns

   `(uncached × input + cached × cached_input + written × cache_write + completion × output) / 1000`,

   where `uncached = prompt_tokens − cached_prompt_tokens − cache_write_tokens`.
   - A missing `cached_input` or `cache_write` rate falls back to `input`.
   - **Tier:** when `prompt_tokens` is greater than a tier's `above_prompt_tokens`, the rates that
     tier gives replace the base rates. With several tiers, the one with the largest threshold
     below `prompt_tokens` applies.
   - **Peak (D10):** when `at` falls Monday–Friday (UTC) inside a `weekdays_utc` window (start
     inclusive, end exclusive), the rates `peak` gives replace the base rates.
   - `at` must be timezone-aware, and is converted to UTC. A naive datetime, or
     `cached_prompt_tokens + cache_write_tokens > prompt_tokens`, raises `ValueError`.
7. **No $0 for an unknown model.** `estimate_cost(model, prompt_tokens, completion_tokens,
   pricing)` raises `ValueError` naming the model when `model` has no row, instead of returning 0.
8. **Backend settings and API keys.**
   - `backend_kwargs(config, provider, environ=None)` returns `{"api_key": <key>, "base_url":
     <base_url or None>, "timeout_s": <timeout_s>}` for that provider.
   - The key is read from the env var named by `api_key_env`. When that is unset or empty, it is
     read from the file named by `<api_key_env>_FILE`, with surrounding whitespace stripped.
   - When neither yields a non-empty key, it raises `ConfigError` naming the env var.
   - `check_api_keys(config, environ=None)` raises one `ConfigError` naming every missing key's env
     var for the providers the active profile uses, and returns `None` when all are present.
9. **Lookups and provenance.** For the active profile:
   - `model_for`, `provider_for`, `effort_for`, `thinking_for`, `json_mode_for` and
     `temperature_for` return the stage's model, its provider, the model's `effort` and `thinking`
     (or `None`), the model's `json_mode`, and the stage's `temperature` (or `None`). An unknown
     stage raises `KeyError`.
   - `providers_in_use()` is the sorted list of distinct providers across all seven stages.
   - `provenance()` returns:

     ```
     {"profile": <active profile>,
      "stages": {<stage>: {"model": <model>, "provider": <provider>}, ...},   # all seven stages
      "models": {<model>: {"provider": <p>, "effort": <effort or None>,
                           "thinking": <thinking or None>}, ...}}          # only models in use
     ```
10. **Committed config (D2, D5, D8).** The committed `llm.json` and `pricing.json` load with an
    empty `environ` and no `profile`. The `openai` profile reproduces today's stage models and
    `max_tokens`. The `anthropic` and `deepseek` profiles map the stages as in the design's §6
    table.

**The implementer also** (covered by unit tests in `tests/unit/`):
- rewrites `llm.json` and `pricing.json` to the new shape. Rates come from design §3; confirm
  OpenAI's cached-input rates from OpenAI's pricing page;
- makes `CostTracker` and the gateway charge each call with `cost_usd`, the call's cached and
  written tokens, and its UTC start time (an injectable clock for tests);
- builds backends from `backend_kwargs` through one factory. `anthropic` raises `ConfigError`
  ("not available yet") until step 3;
- moves the runner, the narrative and the flow onto `LLMConfig`, keeping the phases' `model_for` /
  `max_tokens_for` calls working;
- adds `--llm-profile` to the runner;
- calls `check_api_keys` before a run, while the narrative stays fail-soft;
- writes `provenance()` and the `openai` / `anthropic` SDK versions into the run manifest;
- keeps every caller of `load_llm_settings`, `load_pricing_overrides` and `MODEL_PRICING` working
  or migrates it.

Out of scope: the Anthropic and DeepSeek backends (steps 3–4), and the BFF and UI (step 5).

## Acceptance criteria

- **C1**: A valid config dict built in the test loads. For each profile and each stage, `model_for`,
  `provider_for`, `effort_for`, `thinking_for`, `json_mode_for` and `temperature_for` return the
  values written in the dict. Oracle: Requirements 1 and 9, read off the test's own dict.
- **C2**: Profile choice:
  - an explicit `profile` beats the expanded `active_profile`;
  - `${SCENARIOGEN_LLM_PROFILE:-openai}` gives `openai` with an empty `environ`, and the variable's
    value when set;
  - an unknown profile raises `ConfigError`.

  Oracle: Requirement 2.
- **C3**: Each invalid config raises `ConfigError`:
  - an unknown key at the top level, in a provider, in a model, in a profile, in `stage_params`, in
    `retry` or in `budgets`, or in a pricing row;
  - an unknown provider name;
  - a profile model missing from `models`;
  - a profile missing a stage;
  - a model without a pricing row (in any profile);
  - a stage temperature on a `temperature: false` model;
  - an `effort` not allowed for its provider, or any `effort` on an `openai` model;
  - `thinking` on a non-`anthropic` model, or `between_tools` / `disabled` with effort `xhigh` or
    `max`;
  - a timeout ≤ 0, NaN or infinite;
  - a negative or NaN rate;
  - a row with both `tiers` and `peak`;
  - a malformed or reversed peak window.

  A valid config with exactly one such change raises, and the unchanged config loads. Oracle:
  Requirements 1, 3 and 5.
- **C4**: `max_tokens_for` returns `min(stage max_tokens, model max_output_tokens)`, or `None` when
  the stage sets none. Oracle: Requirement 4.
- **C5**: `cost_usd` equals the formula in Requirement 6 computed by hand:
  - base rates, cached input, cache writes and missing-rate fallbacks;
  - a Haiku-style tier at 100,000 prompt tokens, with 100,000 itself not tiered;
  - DeepSeek-style peak windows `01:00-04:00` and `06:00-10:00`: Monday 01:00 is peak, 04:00 is
    off-peak, 09:59 is peak, 10:00 is off-peak, and Saturday 02:00 is off-peak;
  - a non-UTC aware `at` converted to UTC.

  A naive `at`, or more cached and written tokens than prompt tokens, raises `ValueError`. Oracle:
  Requirement 6; rates from design §3.
- **C6**: `estimate_cost` raises `ValueError` for a model with no row and still prices a known model
  as `p / 1000 × input + c / 1000 × output`. Oracle: Requirement 7.
- **C7**: `backend_kwargs` returns the key from the env var, falls back to the `_FILE` file (stripped)
  when the variable is unset or empty, and raises `ConfigError` naming the variable when both are
  missing. It returns `base_url` and `timeout_s` as configured (`base_url` is `None` when absent).
  `check_api_keys` names every missing variable for the providers in use, and ignores providers the
  active profile doesn't use. Oracle: Requirement 8.
- **C8**: `providers_in_use()` and `provenance()` equal the values written out by hand from the
  test's config, for a single-provider profile and for a mixed one. Oracle: Requirement 9.
- **C9**: The committed `configs/scenariogen/llm/llm.json` and `pricing.json` load with
  `environ={}`:
  - the `openai` profile gives `discover` gpt-5-nano, `evidence` gpt-5-mini, `propose` gpt-5,
    `calibrate` gpt-5-mini, `narrate` gpt-5-mini, `verify` gpt-5-nano and `attribution` gpt-5-mini,
    with `max_tokens` 8000, 16000, 24000, 16000, 16000, 8000 and 6000;
  - `profile="anthropic"` gives claude-sonnet-5-5 for `propose` and `calibrate`, and
    claude-haiku-5-5 elsewhere;
  - `profile="deepseek"` gives deepseek-v4-pro for `propose` and `calibrate`, and deepseek-flash
    elsewhere.

  Oracle: today's committed `llm.json` (values listed above) and design §6.

## Interface

New module `src/scenariogen/llm/profiles.py`:

- `src.scenariogen.llm.profiles.load_llm_config(doc: dict[str, Any], pricing: dict[str, Any], *,
  profile: str | None = None, environ: Mapping[str, str] | None = None) -> LLMConfig`
- `src.scenariogen.llm.profiles.LLMConfig`, with methods:
  - `model_for(self, stage: str) -> str`
  - `provider_for(self, stage: str) -> str`
  - `max_tokens_for(self, stage: str) -> int | None`
  - `temperature_for(self, stage: str) -> float | None`
  - `effort_for(self, stage: str) -> str | None`
  - `thinking_for(self, stage: str) -> str | None`
  - `json_mode_for(self, stage: str) -> str`
  - `providers_in_use(self) -> list[str]`
  - `provenance(self) -> dict[str, Any]`
- `src.scenariogen.llm.profiles.backend_kwargs(config: LLMConfig, provider: str, environ:
  Mapping[str, str] | None = None) -> dict[str, Any]`
- `src.scenariogen.llm.profiles.check_api_keys(config: LLMConfig, environ: Mapping[str, str] |
  None = None) -> None`

New function in `src/scenariogen/llm/cost.py`:

- `src.scenariogen.llm.cost.cost_usd(rates: dict[str, Any], *, prompt_tokens: int,
  completion_tokens: int, cached_prompt_tokens: int, cache_write_tokens: int, at: datetime) ->
  float`

Existing and exercised as is: `src.scenariogen.llm.cost.estimate_cost(model: str, prompt_tokens:
int, completion_tokens: int, pricing: dict[str, dict[str, float]] | None = None) -> float`, and
`src.common.config.models.ConfigError`.

`LLMConfig` is a plain class in the stub; the implementer chooses its fields and construction
(pydantic is expected) and may add attributes (such as `retry` and `budgets`). Tests construct it
only through `load_llm_config`.

## Notes

- Rates for C5, in USD per 1,000 tokens (from design §3):
  - Haiku-style row: `input` 0.0001, `output` 0.0005, `cached_input` 0.00001, `cache_write`
    0.000125, and above 100,000 prompt tokens `input` 0.0005, `output` 0.0025, `cached_input`
    0.00005, `cache_write` 0.000625;
  - DeepSeek-flash-style row: `input` 0.00015, `cached_input` 0.000003, `output` 0.0006; at peak
    `input` 0.0003, `cached_input` 0.000006, `output` 0.0012.
- **Dates for C5:** 2026-10-19 is a Monday and 2026-10-24 a Saturday. Build `at` with
  `datetime(..., tzinfo=timezone.utc)`, or with a fixed-offset `timezone(timedelta(hours=...))` for
  the conversion case.
- **Key files for C7:** written under pytest's `tmp_path`; `environ` is always a dict built in the
  test, never `os.environ`.
- **C9 reads only the two committed config files.** No network, no `data/`, no current date.
