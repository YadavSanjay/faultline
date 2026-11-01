# llm-anthropic-backend: native Anthropic backend behind the ChatBackend Protocol

## Requirement

Step 3 of [the multi-provider design](../design/llm_multi_provider_design.md) (§5.2; decisions D2, D6,
D7, D11). Today, choosing the `anthropic` profile fails with "the anthropic backend is not available
yet". This change adds `AnthropicChatBackend`. It is built on the official `anthropic` SDK
(`0.109.1`) and translates the flow's OpenAI-shaped requests into Anthropic Messages API calls, and
the responses back into a `RawCompletion`. The gateway is unchanged: retry, cost, the budget
breaker, finish-reason handling and the served-model check still apply.

When this change is done:

1. **Construction.** `AnthropicChatBackend(client=None, *, api_key=None, base_url=None,
   timeout_s=600.0, models=None, prompt_caching=True)`.
   - `models` maps each model name to `{"max_output_tokens": int, "effort": str | None, "thinking":
     str | None}`.
   - With `client=None`, the backend imports `anthropic` at construction and builds
     `anthropic.Anthropic(**openai_client_kwargs(api_key=..., base_url=..., timeout_s=...))`, so the
     SDK's own retries are off (design D3). The implementer covers this with a unit test.
2. **It always streams.** `create` calls `client.messages.stream(**request)` as a context manager and
   uses its `get_final_message()`. It never calls `client.messages.create`. Streaming keeps long
   outputs safe from HTTP timeouts.
3. **Request translation.** The request passed to `messages.stream` is built as follows:
   - `model`: the requested model.
   - `max_tokens`: `max_completion_tokens`, capped at the model's `max_output_tokens`, or the
     model's `max_output_tokens` when `max_completion_tokens` is `None`.
   - `system`: the `content` of every `role: "system"` message, in order, joined with `"\n\n"`.
     The key is left out when there are none.
   - `messages`: the remaining messages in order, translated as in Requirement 4.
   - `tools`: each `{"type": "function", "function": {name, description, parameters}}` becomes
     `{"name": name, "description": description, "input_schema": parameters}`. The key is left out
     when `tools` is empty or `None`.
   - `tool_choice` (sent only when tools are sent): `"auto"` → `{"type": "auto"}`, `"none"` →
     `{"type": "none"}`, `None` → left out.
   - `thinking`: `{"type": <thinking>}` when the model's `thinking` is set, otherwise left out.
   - `output_config`: `{"effort": <effort>}` when the model's `effort` is set. It also gets
     `"format": {"type": "json_schema", "schema": <schema>}` when `response_format` is
     `{"type": "json_schema", "json_schema": {"schema": <schema>, ...}}` (D7). The key is left out
     when neither applies.
   - `response_format={"type": "json_object"}` sends nothing: the stage's prompt already asks for
     JSON, and the flow extracts it.
   - `cache_control`: `{"type": "ephemeral"}` when `prompt_caching` is true (the default), otherwise
     left out (Requirement 9).
   - No other keys are sent.
4. **Message translation.**
   - A `user` message keeps its `content`.
   - A `tool` message becomes a `tool_result` block, `{"type": "tool_result", "tool_use_id":
     <tool_call_id>, "content": <content>}`. Consecutive `tool` messages become one `user` message
     with their blocks in order.
   - An `assistant` message whose `_native` is `{"provider": "anthropic", "content": [...]}` is sent
     as `{"role": "assistant", "content": <that list, unchanged>}`. This keeps thinking blocks
     exactly as they were returned.
   - Any other `assistant` message is rebuilt as:
     - a `{"type": "text", "text": <content>}` block when its content is non-empty;
     - then a `{"type": "tool_use", "id": ..., "name": ..., "input": <arguments parsed as JSON>}`
       block for each tool call. Arguments that are not a JSON object give `{}`.

     Other keys, such as `reasoning_content`, are dropped.
5. **Refused before calling.** These raise `FatalChatError` without calling the client:
   - a `temperature` that is not `None` (Claude models reject sampling parameters);
   - a `tool_choice` other than `"auto"`, `"none"` or `None`, whether or not tools are given
     (forced tool use is rejected);
   - a model that is not in `models`.
6. **Response translation.** The final message becomes a `RawCompletion`:
   - `content`: its `text` blocks joined in order, with no separator;
   - `tool_calls`: one `ToolCall(id, name, json.dumps(input))` per `tool_use` block;
   - `finish_reason`: `end_turn` and `stop_sequence` → `stop`, `max_tokens` → `length`, `tool_use`
     → `tool_calls`, `refusal` → `refusal`, any other value unchanged;
   - `prompt_tokens` = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
     (a missing or `None` field counts as 0);
   - `cached_prompt_tokens` = `cache_read_input_tokens`;
   - `cache_write_tokens` = `cache_creation_input_tokens`;
   - `completion_tokens` = `output_tokens`;
   - `served_model`: the message's `model`;
   - `assistant_message`, when there are tool calls: `{"role": "assistant", "content": <text or
     None>, "tool_calls": [{"id", "type": "function", "function": {"name", "arguments"}}...],
     "_native": {"provider": "anthropic", "content": [<each content block as a dict>]}}`. Each
     block dict is the SDK block's `model_dump(exclude_none=True)`. Without tool calls,
     `assistant_message` is `None`.
7. **Errors.** An exception from the client is classified exactly as `OpenAIChatBackend` does:
   transient by class name or status (408, 409, 425, 429, ≥ 500), with a `retry-after` header
   carried as `retry_after_s`; everything else is fatal. Anthropic's 529 "overloaded" is therefore
   transient.
8. **In the flow.** Through `InstrumentedChatClient` and `run_tool_loop`, a turn that returns
   `thinking` and `tool_use` blocks is followed by a request whose assistant message is exactly those
   blocks, then a user message with the `tool_result`. A `refusal` stop raises `FatalChatError`
   (D6).
9. **Prompt caching.** Anthropic caches a prompt prefix only when the request asks for it, unlike
   OpenAI and DeepSeek, which cache automatically. With `prompt_caching` on, every request carries
   the top-level `cache_control: {"type": "ephemeral"}`, which caches the prompt up to its last
   block for 5 minutes. Each tool-loop turn then reads the system prompt, the tool definitions and
   the earlier turns from the cache, and calls that repeat a stage's prompt across scenarios do the
   same. Cache writes and reads reach the gateway as `cache_write_tokens` and
   `cached_prompt_tokens` (Requirement 6), and the step-2 rate card prices them: a write at
   `cache_write`, a read at `cached_input`.

**The implementer also** (covered by unit tests in `tests/unit/`):
- makes `build_provider_backend(config, "anthropic")` return an `AnthropicChatBackend`, with
  `models` built from the config registry;
- logs a refusal's `stop_details.category`;
- makes `propose` and `verify` send `response_format={"type": "json_schema", "json_schema":
  {"name": ..., "schema": ...}}` when `settings.json_mode_for(stage) == "json_schema"`, with
  hand-written schemas for their outputs (`additionalProperties: false`, every property required),
  and `{"type": "json_object"}` otherwise, as today;
- adds `anthropic==0.109.1` to `requirements.txt`;
- updates the step-2 unit test that expects "not available yet".

Out of scope: explicit cache breakpoints and the 1-hour cache TTL, server-side fallbacks (off, D6),
DeepSeek (step 4), and the BFF and UI (step 5).

## Acceptance criteria

- **C1**: For hand-built inputs, the request the fake client receives in `messages.stream(...)`
  equals the dict written out by hand from Requirement 3. The cases are: system joining; tools and
  `tool_choice`; the `max_tokens` cap and default; `thinking` and `effort` present or absent; and
  `json_schema` versus `json_object`. No `messages.create` call is made. Oracle: Requirements 2 and
  3.
- **C2**: Message translation matches Requirement 4 for:
  - user messages;
  - one tool result, and three consecutive tool results merged into one user message;
  - an assistant turn with an Anthropic `_native`, sent unchanged, including a thinking block with a
    signature;
  - an assistant turn without `_native`, with and without text, and with invalid JSON arguments;
  - a `_native` from another provider, which is ignored.

  Oracle: Requirement 4, with expected messages written by hand.
- **C3**: A non-`None` `temperature`, a forced `tool_choice` (`"required"`, or a dict), or an unknown
  model raise `FatalChatError`, and the fake client records no call. Oracle: Requirement 5.
- **C4**: The response translation matches Requirement 6 for: text only; text with two tool uses;
  thinking with a tool use; each stop reason in the mapping, plus an unmapped one; and usage with
  and without cache fields. Every `RawCompletion` field is compared with values written by hand.
  Oracle: Requirement 6.
- **C5**: Client exceptions are classified as in Requirement 7:
  - a `RateLimitError`-named exception with `retry-after: "2"` gives a `TransientChatError` with
    `retry_after_s == 2.0`;
  - status 529 and status 500 are transient;
  - status 400 and a plain `ValueError` are fatal.

  Oracle: Requirement 7.
- **C6**: In `run_tool_loop` over `InstrumentedChatClient` over `AnthropicChatBackend` with a fake
  client:
  - a turn returning `[thinking(signature "sig-1"), tool_use]` is followed by a request whose
    messages end with the assistant message `{"role": "assistant", "content": [<those two block
    dicts>]}` and then a user message with the matching `tool_result`;
  - the loop's final answer is the second turn's text;
  - through the gateway, a `refusal` stop raises `FatalChatError` after one call.

  Oracle: Requirements 4, 6 and 8.
- **C7**: Prompt caching:
  - every request from a backend built without `prompt_caching`, or with `prompt_caching=True`,
    carries `cache_control == {"type": "ephemeral"}`;
  - with `prompt_caching=False` the request has no `cache_control` key and is otherwise identical;
  - through `InstrumentedChatClient` with the Notes pricing, a turn whose usage is `input_tokens` 4,
    `cache_creation_input_tokens` 16494, `cache_read_input_tokens` 0 and `output_tokens` 4 costs
    `(4 × 0.002 + 16494 × 0.0025 + 4 × 0.01) / 1000`;
  - the same turn with the 16494 tokens read from the cache instead costs
    `(4 × 0.002 + 16494 × 0.0002 + 4 × 0.01) / 1000`.

  Oracle: Requirement 9, with costs worked by hand from the Notes pricing (the live token counts in
  Notes).

## Interface

New module `src/scenariogen/llm/anthropic_backend.py`:

- `src.scenariogen.llm.anthropic_backend.AnthropicChatBackend.__init__(self, client: Any = None, *,
  api_key: str | None = None, base_url: str | None = None, timeout_s: float = 600.0, models:
  Mapping[str, Mapping[str, Any]] | None = None, prompt_caching: bool = True) -> None`
- `src.scenariogen.llm.anthropic_backend.AnthropicChatBackend.create(self, *, model: str, messages:
  list[dict[str, Any]], max_completion_tokens: int | None = None, temperature: float | None = None,
  response_format: dict[str, Any] | None = None, tools: list[dict[str, Any]] | None = None,
  tool_choice: str | dict[str, Any] | None = None) -> RawCompletion`

Existing and exercised as is: `src.scenariogen.llm.gateway` (`RawCompletion`, `ToolCall`,
`InstrumentedChatClient`, `FatalChatError`, `TransientChatError`), `src.scenariogen.llm.cost.CostTracker`
(pass a pricing dict with rows for the models used), `src.scenariogen.llm.mcp.loop.run_tool_loop`,
and `src.scenariogen.llm.mcp.registry.McpToolRegistry` with `src.scenariogen.llm.mcp.client.ServerSpec`
and `ToolListing`.

## Notes

- **Fake client.** An object whose `messages.stream(**kwargs)` records a deep copy of the kwargs and
  returns a context manager. Its `get_final_message()` returns a scripted message namespace with:
  - `content`: a list of block objects. Each has `type` and its fields: `text`; `id`, `name` and
    `input` for `tool_use`; `thinking` and `signature` for `thinking`. Each also has
    `model_dump(exclude_none=False)`, returning its fields as a dict and leaving out `None` values
    when `exclude_none=True`.
  - `stop_reason`, and `stop_details` (with `category`) for a refusal.
  - `usage` with `input_tokens`, `output_tokens` and, when scripted, `cache_read_input_tokens` and
    `cache_creation_input_tokens`.
  - `model`.

  The fake has no `messages.create`. It can be scripted to raise from `stream`. It stands in for the
  `anthropic` SDK at the I/O boundary; no project code is faked.
- **Pricing for gateway tests:** `{"claude-sonnet-5-5": {"input": 0.002, "output": 0.01,
  "cached_input": 0.0002, "cache_write": 0.0025}}` (USD per 1,000 tokens, design §3).
- **Live evidence** (2026-10-20, design §9):
  - a Haiku thinking block came back with empty `thinking` text and a 1,200-character signature, and
    was accepted when sent back unchanged;
  - `temperature` and `tool_choice: any` return a 400 on Sonnet;
  - `output_config.format` with a `json_schema` returned JSON that parsed.
- **Live evidence for caching** (2026-10-21): streaming with top-level
  `cache_control: {"type": "ephemeral"}` and a system prompt of about 16K tokens with one tool:
  - on both Haiku 5.5 and Sonnet 5.5, the first call reported `input_tokens` 4,
    `cache_creation_input_tokens` 16494, `cache_read_input_tokens` 0 and `output_tokens` 4;
  - the second call reported 4, 0, 16494 and 4.
- No network, no `data/`, no current date.
