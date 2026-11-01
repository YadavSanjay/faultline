# Scenariogen LLM layer: multi-provider design (OpenAI, Anthropic, DeepSeek)

Status: **delivered** (steps 1–5, 2026-10-21); step 6, the live validation, is pending. Approved
2026-10-20 and amended the same day with the step-0 results (§9). Prompt caching for Claude, first
deferred, was folded into step 3 (`cache_control` ephemeral on every request, §5.2).

The scenariogen LLM flow calls OpenAI only. This design adds Anthropic (Claude Sonnet 5.5 and
Claude Haiku 5.5) and DeepSeek as selectable providers. Each provider gets its own backend behind
the existing `ChatBackend` Protocol, and a profile in config picks the models. The gateway keeps
retry, cost, the budget breaker and tracing, so none of these become provider-specific.

Scope: faultline's scenariogen gateway only. ARag and MacroRAG have their own LLM layers and are
not covered.

## Contents

1. [Current state](#1-current-state)
2. [Decisions](#2-decisions)
3. [Provider facts the design relies on](#3-provider-facts-the-design-relies-on)
4. [Gateway and Protocol changes](#4-gateway-and-protocol-changes)
5. [Backends](#5-backends)
6. [Configuration and cost](#6-configuration-and-cost)
7. [Provenance, BFF and operations](#7-provenance-bff-and-operations)
8. [Delivery plan](#8-delivery-plan)
9. [Step 0 results](#9-step-0-results)

---

## 1. Current state

Every LLM call goes through `InstrumentedChatClient.complete()`, which talks to a `ChatBackend`
Protocol ([gateway.py](../../src/scenariogen/llm/gateway.py)). The only OpenAI SDK call in `src/`
is in `OpenAIChatBackend`.

| Area | Where | What depends on OpenAI |
|---|---|---|
| Backend | [gateway.py](../../src/scenariogen/llm/gateway.py) | SDK call, response parsing, sorting errors into transient and fatal |
| Construction | [llm/runner.py](../../src/scenariogen/llm/runner.py), [narrative.py](../../src/scenariogen/narrative.py) | `OpenAIChatBackend()` built directly; narrative checks `OPENAI_API_KEY` |
| Tool loop | [mcp/loop.py](../../src/scenariogen/llm/mcp/loop.py), [mcp/registry.py](../../src/scenariogen/llm/mcp/registry.py) | OpenAI message and tool shapes; each assistant turn is rebuilt from text plus tool calls |
| JSON mode | [phases/propose.py](../../src/scenariogen/llm/phases/propose.py), [phases/verify.py](../../src/scenariogen/llm/phases/verify.py) | `response_format={"type": "json_object"}` |
| Config | [llm.json](../../configs/scenariogen/llm/llm.json), [config.py](../../src/scenariogen/llm/config.py) | One `provider` and a flat `model_tiers` map, loaded into plain dataclasses |
| Cost | [cost.py](../../src/scenariogen/llm/cost.py), [pricing.json](../../configs/scenariogen/llm/pricing.json) | gpt-5 rate card, input and output tokens only; an unknown model costs $0 |
| BFF | [ui/bff/app/llmops.py](../../ui/bff/app/llmops.py) | Reads `model_tiers` from the raw JSON |
| Run manifests | [flow.py](../../src/scenariogen/llm/flow.py) | Records `models`, not the provider |

The current OpenAI path has three defects that this design also fixes:

- **Retries are hidden.** The client is built with the SDK defaults: `max_retries=2` and a
  600-second timeout. Each of the gateway's 4 attempts can therefore make 3 calls, and the run
  trace under-counts retries.
- **Cached input is overcosted.** The cost tracker charges cached prompt tokens at the full input
  rate.
- **An unknown model costs $0.** A missing rate-card row costs nothing, so the budget breaker
  never trips.

## 2. Decisions

| # | Decision |
|---|---|
| D1 | **We write our own backends; LiteLLM is not used.** The flow sends requests through one narrow call (7 parameters), and the provider-specific rules (thinking, tool choice, JSON strategy, output caps, refusals, cache pricing) have to live in our code either way. DeepSeek's API is OpenAI-compatible, so it needs no translation. The Anthropic translation is a small amount of code that we unit-test directly. If a fourth provider or routing across several deployments of one model is ever needed, a LiteLLM backend can be added behind the same Protocol without touching the stages. |
| D2 | **Anthropic models: `claude-sonnet-5-5` and `claude-haiku-5-5` only.** Sonnet runs `propose` and `calibrate` (`calibrate` sets the numeric shock sizes). Haiku runs every other stage. |
| D3 | **One retry policy: the gateway's.** Every SDK client is built with `max_retries=0` and an explicit timeout from config. This changes OpenAI behaviour: the SDK's hidden retries go away. |
| D4 | **On the last loop turn, tools stay in the request with `tool_choice="none"`** instead of being removed. All three providers accept this. This changes the OpenAI request on that turn. |
| D5 | **DeepSeek may receive portfolio data for every stage.** `deepseek-v4-pro` runs `propose` and `calibrate`, mirroring Sonnet; `deepseek-flash` runs every other stage. |
| D6 | **No fallback to another model or provider, client-side or server-side.** This includes Anthropic's server-side refusal fallback. A refusal is a fatal error, so failures stay visible and the recorded model is the one that answered. We may revisit this once we can diagnose refusals in practice. |
| D7 | **JSON from Anthropic: structured outputs with a schema for each stage that needs one** (`propose`, `verify`). The other stages keep the prompt plus `extract_json_object`, as today. Step 0 confirms that the SDK pin supports it (§9). |
| D8 | **The default profile is `openai`.** Apart from D3 and D4, requests under it are identical to today's. |
| D9 | **Pricing is governed and complete.** Every model a profile uses must have a `pricing.json` row, or config loading fails. The rate card covers cached input, cache writes and prompt-length tiers. |
| D10 | **DeepSeek's time-of-day pricing is modelled.** `pricing.json` holds the UTC peak windows, and the call's UTC start time picks the peak or off-peak rate. Chinese public holidays are not modelled: a weekday holiday inside a peak window is costed at peak, which can overstate cost but never understates it. |
| D11 | **`anthropic` is pinned at `0.109.1`,** the version verified in step 0. It shares `httpx` with `openai` and `mcp` and is already installed in the shared `py312` environment. We move to a newer release only if this version causes a problem. |

## 3. Provider facts the design relies on

Anthropic facts come from the current Claude API reference (2026-10).

| | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | Claude Haiku 5.5 (`claude-haiku-5-5`) |
|---|---|---|
| Input / output, USD per 1M tokens | 2.00 / 10.00 | 0.10 / 0.50 for prompts up to 100K tokens; 0.50 / 2.50 above |
| Cache read / 5-minute cache write | 0.20 / 1.25 × input | 0.1 × input / 1.25 × input |
| Max output tokens | 128K | 128K |
| Thinking | Always on. `{type: "disabled"}` is rejected with a 400; the off switch is `{type: "between_tools"}`, accepted at effort `high` or below | Adaptive by default; `disabled` is accepted at effort `high` or below |
| Default effort | `high` | `medium` |
| Sampling (`temperature`, `top_p`) | A non-default value is rejected with a 400 | A non-default value is rejected with a 400 |
| `tool_choice` | `any` and `tool` are rejected with a 400; `auto` and `none` work | `auto` and `none` |
| JSON | Structured outputs: `output_config.format` with a JSON schema | Same |
| Thinking blocks | Must be sent back unchanged in later turns; editing earlier history can be rejected | Same |
| Refusal | `stop_reason: "refusal"` with `stop_details.category` | Same |

The `anthropic` Python SDK retries by default (`max_retries=2`) and has a 10-minute timeout. Its
exception class names (`RateLimitError`, `APIConnectionError`, `APITimeoutError`,
`InternalServerError`, `APIStatusError`) match the OpenAI SDK's, so the gateway's error
classification works for both.

DeepSeek facts come from its API documentation (2026-10) and the step-0 live calls (§9).

| | `deepseek-flash` (V4.1-Flash) | `deepseek-v4-pro` (V4-Pro) |
|---|---|---|
| Input, cache hit / cache miss, USD per 1M tokens (off-peak / peak) | 0.003 / 0.006 and 0.15 / 0.30 | 0.022 / 0.044 and 0.66 / 1.32 |
| Output, USD per 1M tokens (off-peak / peak) | 0.60 / 1.20 | 1.98 / 3.96 |
| Peak hours | 01:00–04:00 and 06:00–10:00 UTC, Monday to Friday, excluding Chinese public holidays | Same |
| Max output tokens | 384K | 384K |
| Thinking | On by default. `reasoning_effort` is `none`, `low`, `high` or `max`, and `none` turns thinking off | Same |
| JSON | `response_format={"type": "json_object"}` only, with JSON requested in the prompt | Same |
| `tool_choice` | `none` and `auto`; `required` and a named tool return a 400 while thinking is on | Same |
| `temperature` | Ignored while thinking is on (no error) | Same |
| Reasoning in tool loops | `reasoning_content` must be sent back on every later request when `tools` are present; the documentation says leaving it out returns a 400 | Same |
| Usage | `prompt_tokens` = `prompt_cache_hit_tokens` + `prompt_cache_miss_tokens`; `completion_tokens` includes reasoning | Same |
| Finish reasons | `stop`, `length`, `tool_calls`, `content_filter`, `insufficient_system_resource`, `aborted` | Same |

The API is OpenAI-compatible, served from `https://api.deepseek.com`.

## 4. Gateway and Protocol changes

These changes apply to every provider.

- **New `RawCompletion` fields:**
  - `assistant_message`: the assistant turn exactly as the provider returned it;
  - `cached_prompt_tokens` and `cache_write_tokens`;
  - `served_model`: the model the response says produced it.
- **Message format.** Messages inside the flow keep the OpenAI shape, and each backend translates
  them in and out. The provider's own assistant turn travels in a private `_native` key on the
  assistant message. Only the backend that produced it reads that key; every other backend strips
  it. The tool loop appends the turn it receives instead of rebuilding it, which keeps Anthropic
  thinking blocks and DeepSeek reasoning intact across tool turns. Trace prompt hashes are
  unaffected, because they read only the system and user text
  ([trace/capture.py](../../src/scenariogen/trace/capture.py)).
- **Refusal.** A `refusal` finish reason raises `FatalChatError`, the same way `content_filter`
  already does (D6). DeepSeek's `aborted` is also fatal. Its `insufficient_system_resource` is
  retried as a `TransientChatError`.
- **Served model.** `served_model` must be the requested model or a dated snapshot of it
  (`<requested>-YYYY-MM-DD`; OpenAI reports `gpt-5` as `gpt-5-2025-08-07`). Anything else is a
  fatal error. Cost and the recorded model always use the requested, bare name.
- **`retry-after`.** On a 429 the gateway waits for the `retry-after` header, capped at
  `max_wait_s`, instead of its own backoff.
- **Last turn.** `run_tool_loop` sends `tools` with `tool_choice="none"` once the tool budget is
  used up (D4).

## 5. Backends

### 5.1 `OpenAIChatBackend` (hardened)

- `max_retries=0` and a timeout from provider config (D3).
- New constructor arguments `base_url` and `api_key`, which §5.3 uses for DeepSeek.
- Reports `usage.prompt_tokens_details.cached_tokens` as `cached_prompt_tokens`.
- Fills `assistant_message` and `served_model` from the response.

### 5.2 `AnthropicChatBackend` (new)

Built on the official `anthropic` SDK; the import is deferred until construction, as for OpenAI.

**Request translation:**

| Flow (OpenAI shape) | Anthropic Messages API |
|---|---|
| `role: "system"` messages | Joined into `system=` |
| `{"type": "function", "function": {name, description, parameters}}` | `{name, description, input_schema}` |
| `tool_choice` `"auto"` / `"none"` | `{"type": "auto"}` / `{"type": "none"}`; a forced choice raises `FatalChatError` |
| Assistant turn with `_native` | `_native` content blocks sent back unchanged |
| Assistant turn without `_native` | Text block plus one `tool_use` block for each tool call |
| Consecutive `role: "tool"` messages | One user turn of `tool_result` blocks |
| `max_completion_tokens` | `max_tokens`, clamped to the model's cap |
| `response_format` for a stage with a schema | `output_config.format` with that stage's JSON schema (D7) |
| Thinking and effort | From the model registry: `thinking` and `output_config.effort` |
| `temperature` | Never sent; rejected when config loads (§6) |

**Transport.** The backend always streams internally (`messages.stream(...)` and then
`get_final_message()`), so the Protocol stays synchronous. Streaming keeps `propose` (24K output
tokens with thinking on) safe from HTTP timeouts.

**Response translation:**

| Anthropic | Flow |
|---|---|
| `text` blocks | `content` |
| `tool_use` blocks | `ToolCall(id, name, json.dumps(input))` |
| Whole `content` list | `assistant_message["_native"]` |
| `end_turn` / `max_tokens` / `tool_use` / `refusal` | `stop` / `length` / `tool_calls` / `refusal` |
| `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` | `prompt_tokens` |
| `cache_read_input_tokens` / `cache_creation_input_tokens` | `cached_prompt_tokens` / `cache_write_tokens` |
| `output_tokens` (includes thinking) | `completion_tokens` |
| `stop_details.category` | Logged with the refusal |

**Prompt caching** is on by default (constructor flag `prompt_caching`).
- Every request carries the top-level `cache_control: {"type": "ephemeral"}`, which caches the
  prompt up to its last block for 5 minutes. Each tool-loop turn, and each call that repeats a
  stage's prompt across scenarios, then reads the system prompt, the tools and earlier turns from
  the cache.
- OpenAI and DeepSeek cache automatically; Anthropic caches only on request.
- Writes and reads are priced at the rate card's `cache_write` and `cached_input` rates.
- Verified live (2026-10-21): 16,494 tokens were written on the first call and read on the second,
  on both Claude models.
- Explicit breakpoints and the 1-hour TTL are not used.

### 5.3 DeepSeek

DeepSeek is `OpenAIChatBackend` with `base_url="https://api.deepseek.com"` and `DEEPSEEK_API_KEY`;
no new dependency is needed.

Its registry rows:
- set `json_mode: "json_object"`;
- cap output tokens at 384K;
- set the model's `effort`, which the backend sends as `reasoning_effort` in `extra_body`.

Both models think by default, so every DeepSeek stage sends `reasoning_content` back. The backend
copies `reasoning_content` from the response into the assistant turn's `_native` key, and on later
turns sends it as a field of that assistant message (§4). This holds for every stage, so no stage is
restricted.

The backend reads cached input from `prompt_cache_hit_tokens`. The `json_object` stages already
ask for JSON in their prompts, as DeepSeek requires.

## 6. Configuration and cost

`llm.json` is loaded through pydantic models, and an unknown key raises `ConfigError`.

```jsonc
{
  "active_profile": "${SCENARIOGEN_LLM_PROFILE:-openai}",
  "providers": {
    "openai":    {"api_key_env": "OPENAI_API_KEY",    "timeout_s": 300},
    "anthropic": {"api_key_env": "ANTHROPIC_API_KEY", "timeout_s": 600},
    "deepseek":  {"api_key_env": "DEEPSEEK_API_KEY",  "timeout_s": 300,
                  "base_url": "https://api.deepseek.com"}
  },
  "models": {
    "gpt-5":             {"provider": "openai",    "max_output_tokens": 128000, "json_mode": "json_object"},
    "gpt-5-mini":        {"provider": "openai",    "max_output_tokens": 128000, "json_mode": "json_object"},
    "gpt-5-nano":        {"provider": "openai",    "max_output_tokens": 128000, "json_mode": "json_object"},
    "claude-sonnet-5-5": {"provider": "anthropic", "max_output_tokens": 128000, "json_mode": "json_schema",
                          "temperature": false, "thinking": "adaptive", "effort": "high"},
    "claude-haiku-5-5":  {"provider": "anthropic", "max_output_tokens": 128000, "json_mode": "json_schema",
                          "temperature": false, "thinking": "adaptive", "effort": "medium"},
    "deepseek-flash":    {"provider": "deepseek",  "max_output_tokens": 384000, "json_mode": "json_object",
                          "effort": "high"},
    "deepseek-v4-pro":   {"provider": "deepseek",  "max_output_tokens": 384000, "json_mode": "json_object",
                          "effort": "high"}
  },
  "profiles": { "...": "see table below" },
  "stage_params": { "...": "unchanged" },
  "retry": { "...": "unchanged" },
  "budgets": { "...": "unchanged" }
}
```

Profiles map each stage to a model:

| Stage | `openai` (today) | `anthropic` | `deepseek` |
|---|---|---|---|
| discover | gpt-5-nano | claude-haiku-5-5 | deepseek-flash |
| evidence | gpt-5-mini | claude-haiku-5-5 | deepseek-flash |
| propose | gpt-5 | claude-sonnet-5-5 | deepseek-v4-pro |
| calibrate | gpt-5-mini | claude-sonnet-5-5 | deepseek-v4-pro |
| narrate | gpt-5-mini | claude-haiku-5-5 | deepseek-flash |
| verify | gpt-5-nano | claude-haiku-5-5 | deepseek-flash |
| attribution | gpt-5-mini | claude-haiku-5-5 | deepseek-flash |

**Selecting a profile.** The `SCENARIOGEN_LLM_PROFILE` environment variable, overridden by
`--llm-profile` on the runner. Because profiles name models and each model names its provider, a
profile can mix providers.

**Checks when config loads** (each failure raises `ConfigError`):
- every model a profile names has a `models` row and a `pricing.json` row (D9);
- a stage `temperature` is rejected for a model with `"temperature": false`;
- the thinking and effort combination must be one the model accepts. For example,
  `between_tools` is rejected above effort `high`;
- a stage `max_tokens` above the model's cap is clamped to the cap, with a warning.

**API keys.** The runner checks the key of every provider the active profile uses, and fails before
starting if one is missing. The attribution narrative keeps failing soft: with a missing key it
falls back to deterministic attribution only.

**Backend factory.** One factory builds a backend for each provider from `providers`. The runner and
the narrative both use it, and neither constructs a backend class directly.

**Rate card.** `pricing.json` rows gain optional fields:

```jsonc
"claude-haiku-5-5": {
  "input": 0.0001, "output": 0.0005,           // USD per 1K tokens, as today
  "cached_input": 0.00001, "cache_write": 0.000125,
  "tiers": [{"above_prompt_tokens": 100000, "input": 0.0005, "output": 0.0025,
             "cached_input": 0.00005, "cache_write": 0.000625}]
}
```

DeepSeek rows add a `peak` block (D10). Its rates replace the base rates when the call starts
inside a peak window:

```jsonc
"deepseek-flash": {
  "input": 0.00015, "cached_input": 0.000003, "output": 0.0006,   // off-peak
  "peak": {"weekdays_utc": ["01:00-04:00", "06:00-10:00"],
           "input": 0.0003, "cached_input": 0.000006, "output": 0.0012}
},
"deepseek-v4-pro": {
  "input": 0.00066, "cached_input": 0.000022, "output": 0.00198,
  "peak": {"weekdays_utc": ["01:00-04:00", "06:00-10:00"],
           "input": 0.00132, "cached_input": 0.000044, "output": 0.00396}
}
```

`estimate_cost` charges uncached input, cached input, cache writes and output separately. It picks
the tier from the prompt size and, for a row with a `peak` block, the rate from the call's UTC start
time. The gateway passes in that start time, and tests supply a fixed one. An unknown model raises an
error instead of costing $0.

## 7. Provenance, BFF and operations

- **Run manifests** record:
  - the active profile;
  - the provider and model for each stage;
  - the `openai` and `anthropic` SDK versions;
  - the thinking and effort settings for each model.

  Model names stay bare (`gpt-5`, `claude-sonnet-5-5`). A manifest written before this change has
  no provider field; readers treat it as `openai`.
- **BFF.** [llmops.py](../../ui/bff/app/llmops.py) reads the typed settings instead of the raw
  `model_tiers`, and the LLM-ops console breaks cost down by provider.
- **Dependencies.** `anthropic==0.109.1` goes into `requirements.txt` (D11). DeepSeek adds no
  dependency. `anthropic` is already allowed by the `tools/tdd/tdd.py` import guard.
- **Secrets.** `.env.example` gains `ANTHROPIC_API_KEY` and `DEEPSEEK_API_KEY`, each with a `_FILE`
  variant. In AWS mode both keys live in SSM Parameter Store.
- **Network.** Any egress allowlist must include `api.anthropic.com` and `api.deepseek.com`.
- **Docs.** Update the scenariogen LLM-flow guide, `src/scenariogen/CLAUDE.md` and
  [scenariogen_llm_flow_design.md](scenariogen_llm_flow_design.md).

## 8. Delivery plan

Each step except 0, 5 and 6 is one spec: `spec/<id>` then `impl/<id>`, with spec tests locked before
implementation.

| Step | Spec / branch | Scope | Depends on |
|---|---|---|---|
| 0 | spike (not merged) | **Done 2026-10-20.** Results in §9 | — |
| 1 | `llm-native-turns` | §4 gateway and loop changes; §5.1 OpenAI hardening (D3, D4) | 0 |
| 2 | `llm-provider-profiles` | §6: pydantic config, model registry, profiles, checks at load, tiered and cache pricing, an unknown model is an error, backend factory, profile selection, key checks; §7 provenance fields | 1 |
| 3 | `llm-anthropic-backend` | §5.2. Tests use a fake client at the SDK boundary; expected request bodies are written by hand from the API reference | 2 |
| 4 | `llm-deepseek-profile` | §5.3: registry rows, peak and off-peak pricing, JSON mode, `reasoning_effort`, sending `reasoning_content` back, the extra finish reasons | 2 (runs alongside 3) |
| 5 | chore | Dependencies, `.env.example`, BFF and UI provider breakdown, docs | 3, 4 |
| 6 | live validation | A smoke test per profile, then one Mode-2 run per provider, compared on grounding, P&L and cost. Long, paid runs: each one needs explicit go-ahead | 5 |

## 9. Step 0 results

The spike ran on 2026-10-20 with `anthropic 0.109.1` and `openai 2.14.0`, using small live calls
(under $0.10 in total) and a fake tool. Its scripts were not committed.

**Anthropic (Sonnet 5.5 and Haiku 5.5):**
- `output_config` (effort and format), adaptive thinking, streaming with `get_final_message()`, and
  the usage fields in §5.2 all work with `0.109.1` (D11).
- The tool loop works when the assistant turn is sent back as returned and the last turn uses
  `tool_choice: none`.
- Structured output with a `propose`-shaped schema returned JSON that parses, on both models (D7).
- A Haiku thinking block (empty text, with a signature) was accepted when sent back unchanged.
  Dropping it was also accepted on this account, as was removing the tools while the history held
  tool calls. The design sends turns back unchanged anyway (§4), which preserves the model's
  reasoning.
- `temperature` returns a 400 on both models. Sonnet rejects `tool_choice: any` with a 400; Haiku
  accepts it, which the design does not use.

**DeepSeek (`deepseek-flash` and `deepseek-v4-pro`):**
- The model list returns only these two models. The names `deepseek-chat` and `deepseek-reasoner`
  assumed earlier no longer apply, and the §3 facts replace them.
- On both models, a tool loop that sends `reasoning_content` back with `tool_choice: none` works.
  Leaving `reasoning_content` out did not return the 400 the documentation describes, but the design
  follows the documentation and sends it back (§5.3).
- `json_object` returned JSON that parses. `tool_choice: required` returns a 400 while thinking is
  on. `temperature` is accepted and ignored.
- Usage reports `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`, which sum to
  `prompt_tokens`. `completion_tokens` includes reasoning.
