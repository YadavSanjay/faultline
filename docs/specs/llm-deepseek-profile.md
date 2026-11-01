# llm-deepseek-profile: DeepSeek reasoning effort and cache-hit accounting

## Requirement

Step 4 of [the multi-provider design](../design/llm_multi_provider_design.md) (§5.3; decisions D5,
D10). Most of DeepSeek's path is already in place:
- the registry rows and peak/off-peak pricing (step 2);
- the OpenAI-compatible backend with DeepSeek's `base_url` (step 2);
- `reasoning_content` passed back in tool loops (step 1);
- its finish reasons (step 1).

Two gaps remain:
- The registry's `effort` for a DeepSeek model is never sent, so every call runs at DeepSeek's
  default effort instead of the configured one.
- DeepSeek reports cache hits in `usage.prompt_cache_hit_tokens`. The OpenAI backend reads only
  `usage.prompt_tokens_details.cached_tokens`, so DeepSeek cache hits may be priced at the full input
  rate.

When this change is done:

1. **Reasoning effort.** `reasoning_extra_body(provider, effort)` returns
   `{"reasoning_effort": effort}` when `provider` is `"deepseek"` and `effort` is not `None`, and
   `None` otherwise. That includes every `openai` model: their registry rows carry no effort, and
   OpenAI requests stay as they are today.
2. **Backends send it.** A backend from `build_provider_backend(config, "deepseek", environ)` sends
   `extra_body=reasoning_extra_body("deepseek", <model's effort>)` on every request for a model whose
   effort is set, and no `extra_body` otherwise. A backend for `"openai"` never sends `extra_body`.
   The implementer gives `OpenAIChatBackend` the per-model options it needs, for example a `models`
   argument like the Anthropic backend's.
3. **Cache hits are counted.** `OpenAIChatBackend` reports `cached_prompt_tokens` from
   `usage.prompt_tokens_details.cached_tokens` when that field is present and not `None`, otherwise
   from `usage.prompt_cache_hit_tokens`, otherwise 0. `prompt_tokens` is unchanged: DeepSeek's
   `prompt_tokens` already equals hits plus misses.
4. **Priced at the right rate.** Through the gateway, a DeepSeek call is priced by the committed
   `pricing.json` row for its model, at the call's UTC start time: peak or off-peak (D10), with the
   cache hits at `cached_input`.

Out of scope: the BFF and UI (step 5), and changing which models the `deepseek` profile uses.

## Acceptance criteria

- **C1**: `reasoning_extra_body` returns `{"reasoning_effort": e}` for `("deepseek", e)`, for each
  of `none`, `low`, `high` and `max`. It returns `None` for `("deepseek", None)` and for
  `("openai", any effort or None)`. Oracle: Requirement 1.
- **C2**: With the `openai` module replaced by a fake whose `OpenAI(**kwargs)` returns a fake client
  recording `chat.completions.create(**kwargs)`, and the committed config:
  - under `profile="deepseek"`, every request built by
    `build_provider_backend(config, "deepseek", environ={"DEEPSEEK_API_KEY": "k"})` for
    `deepseek-flash` and `deepseek-v4-pro` carries `extra_body == {"reasoning_effort": "high"}`;
  - under `profile="openai"`, requests for gpt-5, gpt-5-mini and gpt-5-nano from the `"openai"`
    backend carry no `extra_body` key.

  Oracle: Requirement 2 and the committed `llm.json` (effort `high` on both DeepSeek models).
- **C3**: Through `OpenAIChatBackend(client=<fake>)`, a response whose usage has `prompt_tokens` P,
  `prompt_cache_hit_tokens` H, `prompt_cache_miss_tokens` P − H and no `prompt_tokens_details`
  gives `cached_prompt_tokens == H` and `prompt_tokens == P`. When `prompt_tokens_details.cached_tokens`
  is present it wins; when it is `None`, H is used; with neither, the result is 0. Oracle:
  Requirement 3.
- **C4**: Through `InstrumentedChatClient` over the DeepSeek backend (fake client), with the committed
  `pricing.json` and a fixed `clock`, a `deepseek-flash` call with `prompt_tokens` 10000 (4000 hits),
  `completion_tokens` 1000 and `model` `"deepseek-flash"` costs:
  - at Monday 2026-10-19 02:00 UTC (peak):
    `(6000 × 0.0003 + 4000 × 0.000006 + 1000 × 0.0012) / 1000 = 0.003024`;
  - at Monday 12:00 UTC (off-peak):
    `(6000 × 0.00015 + 4000 × 0.000003 + 1000 × 0.0006) / 1000 = 0.001512`.

  The `deepseek-v4-pro` equivalents are computed by hand from its row the same way. Oracle:
  Requirement 4 and design §3 rates.

## Interface

New function in `src/scenariogen/llm/gateway.py`:

- `src.scenariogen.llm.gateway.reasoning_extra_body(provider: str, effort: str | None) ->
  dict[str, Any] | None`

Existing and exercised as is:
- `src.scenariogen.llm.profiles.build_provider_backend(config: LLMConfig, provider: str, environ:
  Mapping[str, str] | None = None) -> ChatBackend`;
- `src.scenariogen.llm.config.load_llm_settings(root: Path | None = None, *, profile: str | None =
  None, environ: Mapping[str, str] | None = None) -> LLMConfig`;
- `src.scenariogen.llm.gateway.OpenAIChatBackend(client: Any = None, *, api_key=None, base_url=None,
  timeout_s=600.0)` with `create(...)`;
- `InstrumentedChatClient(backend, cost_tracker, *, max_attempts=4, min_wait_s=1.0, max_wait_s=30.0,
  cost_budget_usd=None, clock=None)`;
- `src.scenariogen.llm.cost.CostTracker(pricing)`, with `LLMConfig.pricing` as the committed rate
  card.

## Notes

- **Fake `openai` module (C2).** It goes into `sys.modules["openai"]` with pytest's `monkeypatch`.
  Its `OpenAI` class accepts any keyword arguments. Its instance has `chat.completions.create(**kw)`,
  which records the kwargs and returns a namespace response with:
  - `model` equal to the requested model;
  - `choices[0].message` with `content` "ok" and `tool_calls` None, and `choices[0].finish_reason`
    "stop";
  - `usage` with `prompt_tokens` and `completion_tokens`.

  This fakes the SDK at the I/O boundary only.
- **Committed rates** (`pricing.json`, USD per 1,000 tokens, design §3):
  - `deepseek-flash`: off-peak `input` 0.00015, `cached_input` 0.000003, `output` 0.0006; peak 0.0003,
    0.000006 and 0.0012;
  - `deepseek-v4-pro`: off-peak 0.00066, 0.000022 and 0.00198; peak 0.00132, 0.000044 and 0.00396;
  - peak windows `01:00-04:00` and `06:00-10:00` UTC, Monday to Friday.
- **Live evidence** (2026-10-20, design §9): DeepSeek usage reported `prompt_cache_hit_tokens` and
  `prompt_cache_miss_tokens` summing to `prompt_tokens`, and responses named the requested model.
- No network, no `data/`, no current date (the clock is fixed in tests).
