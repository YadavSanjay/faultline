# llm-native-turns: provider-neutral gateway and loop changes, hardened OpenAI backend

## Requirement

Step 1 of [the multi-provider design](../design/llm_multi_provider_design.md) (§4, §5.1; decisions
D3, D4, D6). It changes only the gateway (`src/scenariogen/llm/gateway.py`) and the tool loop
(`src/scenariogen/llm/mcp/loop.py`). No new provider is added.

Today:
- `OpenAIChatBackend` builds `openai.OpenAI()` with the SDK defaults: two internal retries inside
  every gateway attempt, and a 600-second timeout.
- `run_tool_loop` rebuilds each assistant turn from text and tool calls, which drops any other
  field the provider returned (DeepSeek's `reasoning_content`).
- `run_tool_loop` removes the tools from the request once the tool budget is used up.
- Only `content_filter` is treated as a fatal finish reason.
- A 429's `retry-after` header is ignored.
- The model name in the response is never checked.
- Cached prompt tokens are not reported.

When this change is done:

1. **One retry policy (D3).** A new function `openai_client_kwargs` returns the keyword arguments
   for constructing the OpenAI SDK client. It always sets `max_retries=0` and `timeout=timeout_s`.
   It includes `api_key` and `base_url` only when they are not `None`, and returns no other keys.
   `timeout_s` must be a finite number > 0, or it raises `ValueError`. The implementer gives
   `OpenAIChatBackend` keyword arguments `base_url` and `timeout_s` (default `600.0`) beside
   `api_key`, builds its client from `openai_client_kwargs`, and covers that wiring with unit tests
   in `tests/unit/`.
2. **The assistant turn is passed back as returned.** When a response carries tool calls, the
   assistant message `run_tool_loop` appends for the next request is built from the response
   message:
   - `{"role": "assistant", "content": <content, or None when empty>, "tool_calls": [{"id": ...,
     "type": "function", "function": {"name": ..., "arguments": ...}}, ...]}`, as today;
   - plus `"reasoning_content": <value>` exactly when the response message has a `reasoning_content`
     attribute that is not `None`.

   Without `reasoning_content`, the request is identical to today's.
3. **Private message keys never reach the OpenAI API.** `OpenAIChatBackend.create` removes every
   top-level message key that starts with `_` (such as `_native`, which later steps use to carry
   another provider's turn) before calling the SDK. It keeps every other key, the message order and
   the number of messages. It doesn't mutate the caller's list or dicts.
4. **Tools stay on the last turn (D4).** Once the tool budget is used up, the next request still
   carries the same tool list, with `tool_choice="none"`. Requests while the budget lasts carry the
   tools with no `tool_choice` (as today). When the registry offers no tools for the stage,
   requests carry neither `tools` nor `tool_choice`.
5. **Finish reasons.** In `InstrumentedChatClient.complete`, whatever the backend:
   - `refusal` and `aborted` raise `FatalChatError` and are not retried (D6), like
     `content_filter`;
   - `insufficient_system_resource` is retried like a `TransientChatError` (same attempt limit and
     backoff). If every attempt returns it, a `TransientChatError` is raised.
6. **Served model check.** If the response names a model, it must be the requested model or a
   dated snapshot of it: the requested name followed by `-YYYY-MM-DD` (OpenAI reports `gpt-5` as
   `gpt-5-2025-08-07`). Anything else raises `FatalChatError` without a retry. An empty or missing
   response model skips the check. The result's `model` and its cost always use the requested
   (bare) name.
7. **`retry-after`.** When the SDK raises a retryable error carrying a `retry-after` header (seconds,
   integer or decimal), the gateway waits that long before the next attempt, capped at
   `max_wait_s`, instead of its own backoff. A missing or unparseable header falls back to the
   backoff.
8. **Cached prompt tokens are reported.** `LLMCallResult` gains `cached_prompt_tokens: int`, taken
   from the response's `usage.prompt_tokens_details.cached_tokens` (0 when absent). `prompt_tokens`
   still includes them. The cost was unchanged in this step; cached-input rates came in step 2
   (llm-provider-profiles).

The implementer adds the `RawCompletion` and `LLMCallResult` fields needed to carry this, and
updates `tests/scenariogen/llm/test_p1_mcp.py::test_tool_loop_respects_budget`, which asserts the
old behaviour of removing the tools.

Out of scope: config, profiles, the rate card and new providers (steps 2–4).

## Acceptance criteria

- **C1**: `openai_client_kwargs(api_key=k, base_url=b, timeout_s=t)` returns exactly
  `{"max_retries": 0, "timeout": t}`, plus `"api_key": k` when `k` is not `None` and
  `"base_url": b` when `b` is not `None`. It raises `ValueError` for a `t` that is ≤ 0, NaN or
  infinite. Oracle: Requirement 1.
- **C2**: In `run_tool_loop` over `InstrumentedChatClient` over `OpenAIChatBackend(client=<fake SDK
  client>)`, the assistant message in the request that follows a tool-call response equals the dict
  in Requirement 2, built by hand from the fake response. It contains `reasoning_content` exactly
  when the fake response message has a non-`None` one. Oracle: Requirement 2.
- **C3**: For any message list, the `messages` the fake SDK client receives from
  `OpenAIChatBackend.create` equal the input with every top-level key starting with `_` removed, and
  the input is unchanged after the call. Oracle: Requirement 3.
- **C4**: With `max_tool_calls=n` and a model that keeps asking for tools, the requests before the
  budget is used up carry the full tool list and no `tool_choice`. The request after it carries the
  same tool list and `tool_choice == "none"`. With no tools offered, no request has `tools` or
  `tool_choice`. Oracle: Requirement 4.
- **C5**: Through `InstrumentedChatClient` over a fake `ChatBackend`:
  - a `refusal` or `aborted` finish reason raises `FatalChatError` after exactly one backend call;
  - one `insufficient_system_resource` followed by `stop` returns a result with `attempts == 2` and
    `retries == 1`;
  - `insufficient_system_resource` on every attempt raises `TransientChatError` after `max_attempts`
    calls.

  Oracle: Requirement 5.
- **C6**: Through `OpenAIChatBackend` with a fake SDK client:
  - a response model equal to the requested model, or the requested model plus `-YYYY-MM-DD`,
    succeeds with `result.model` equal to the requested name and `cost_usd` equal to the requested
    model's rate applied by hand;
  - a different model (including another model that shares the prefix, such as
    `gpt-5-mini-2025-08-07` when `gpt-5` was requested) raises `FatalChatError` after one call;
  - an empty response model succeeds.

  Oracle: Requirement 6 and the `gpt-5` row of `MODEL_PRICING`.
- **C7**: The fake SDK client first raises an exception named `RateLimitError` with
  `status_code = 429` and `response.headers = {"retry-after": h}`, then returns a normal response.
  With `min_wait_s = 0.01`:
  - `h = "1"` and `max_wait_s = 5`: the call succeeds with `attempts == 2`, after at least 0.9 s;
  - `h = "30"` and `max_wait_s = 0.3`: it succeeds in under 2 s;
  - a missing or non-numeric `h`: it succeeds in under 0.5 s.

  Oracle: Requirement 7, measured with wall-clock bounds whose margins are stated in the test.
- **C8**: With `usage.prompt_tokens_details.cached_tokens = c` in the fake response,
  `result.cached_prompt_tokens == c`, `result.prompt_tokens` equals the fake's `prompt_tokens`, and
  `cost_usd` charges cached tokens at the `cached_input` rate. Without `prompt_tokens_details`, `cached_prompt_tokens == 0`.
  Oracle: Requirement 8 and the `gpt-5` row of `MODEL_PRICING`.

## Interface

New function in `src/scenariogen/llm/gateway.py`:

- `src.scenariogen.llm.gateway.openai_client_kwargs(*, api_key: str | None, base_url: str | None,
  timeout_s: float) -> dict[str, Any]`

Existing signatures are exercised unchanged:
- `OpenAIChatBackend(client: Any = None, *, api_key: str | None = None)` with
  `create(*, model, messages, max_completion_tokens=None, temperature=None, response_format=None,
  tools=None, tool_choice=None) -> RawCompletion`;
- `InstrumentedChatClient(backend, cost_tracker, *, max_attempts=4, min_wait_s=1.0, max_wait_s=30.0,
  cost_budget_usd=None)` with `complete(...) -> LLMCallResult`;
- `RawCompletion(content, prompt_tokens, completion_tokens, finish_reason, tool_calls=())`;
- `ToolCall(id, name, arguments)`, `FatalChatError`, `TransientChatError`, `CostTracker`,
  `MODEL_PRICING` (in `src.scenariogen.llm.cost`);
- `src.scenariogen.llm.mcp.loop.run_tool_loop(*, gateway, registry, system, user, model, stage,
  allow=None, max_tool_calls=24, max_completion_tokens=None, temperature=None) -> ToolLoopResult`;
- `src.scenariogen.llm.mcp.registry.McpToolRegistry(source, specs: dict[str, ServerSpec])`, where
  `source` has `list_tools(server) -> list[ToolListing]` and
  `call_tool(server, tool, arguments) -> str`;
- `src.scenariogen.llm.mcp.client.ServerSpec(name, command, args, cwd=None, env={},
  tool_prefix="")` and `ToolListing(name, description, input_schema)`.

`LLMCallResult.cached_prompt_tokens` is added by the implementer. Tests read it with
`getattr(result, "cached_prompt_tokens", None)`, so they fail with an assertion before it exists.

## Notes

- **Fake SDK client.** An object with `chat.completions.create(**kwargs)` that records each call's
  kwargs and returns scripted responses. Each response is a namespace with:
  - `model`;
  - `choices[0].finish_reason`;
  - `choices[0].message` with `content`, `tool_calls` (each with `id` and
    `function.name` / `function.arguments`) and, when scripted, `reasoning_content`;
  - `usage` with `prompt_tokens`, `completion_tokens` and, when scripted,
    `prompt_tokens_details.cached_tokens`.

  It can also be scripted to raise an exception. This fakes the OpenAI SDK at the I/O boundary; no
  project code is faked.
- **Fake `ChatBackend`** (C5): returns scripted `RawCompletion`s built with the existing fields only.
- **Fake tool source** (C2, C4): returns fixed `ToolListing`s and a fixed JSON string from
  `call_tool`.
- `MODEL_PRICING["gpt-5"]` is USD per 1,000 tokens: `input` 0.00125, `output` 0.01.
- **Live evidence for Requirement 6** (2026-10-20): `gpt-5-nano`, `gpt-5-mini` and `gpt-5` were
  reported as `gpt-5-nano-2025-08-07`, `gpt-5-mini-2025-08-07` and `gpt-5-2025-08-07`. The Anthropic
  and DeepSeek models were reported under their requested names.
- No network, no `data/`, no current date. Timing tests (C7) keep total sleep under about 3 s.
- Updated 2026-10-30 (retired spec; tests kept in step): llm-provider-profiles added cached-input rates, as
  Requirement 8 planned, so C8's cost now uses the `gpt-5` `cached_input` rate of 0.000125 USD per 1K tokens.
