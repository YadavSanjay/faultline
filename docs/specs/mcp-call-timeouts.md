# mcp-call-timeouts — per-tool MCP timeouts, and one timeout degrades one tool, not a server

## Requirement

`src/scenariogen/llm/mcp/client.py` (`McpClient`) wraps every MCP tool call in one
`call_timeout_s` from `configs/scenariogen/llm/mcp.json` (300s), whatever the tool. When any call
exceeds it, the client adds the **whole server** to `_degraded`, and every later call to that server
fails immediately with "previously timed out; skipped" for the rest of the run.

In a live run a single `macro_synthesize_shock_vector` call (MacroRAG
retrieval + LLM synthesis, which can take several minutes) exceeded 300s. That disabled all of
MacroRAG, including the cheap `macro_shock_schema` and `macro_get_citations`. In July runs the same
thing happened to ARag after one slow `narrative_search`, which also knocked out `arag__coverage`.
Since `llm-grounding-gate`, the flow calls synthesize from code for every scenario, so its timeout
budget matters even more.

When this change is done:

1. **Timeouts are per tool.** The call timeout of a tool is its entry in a new optional
   `tool_timeouts_s` map in `mcp.json`, keyed by the namespaced tool name (as in
   `phase_allowlists`, e.g. `macro__macro_synthesize_shock_vector`), and otherwise the default
   `call_timeout_s`.
2. **Degradation is per tool.** A timeout counts against the tool that timed out only. Once a tool
   has timed out `degrade_after_timeouts` times in a run (a new optional `mcp.json` key, default 1),
   later calls to **that tool** fail immediately with a `TimeoutError`, without waiting. Other tools
   on the same server keep working.
3. **Timeout settings are validated.** Every timeout (`timeout_s`, `call_timeout_s`, every
   `tool_timeouts_s` value) must be a finite number > 0, no call timeout may exceed `timeout_s` (the
   client's overall wait for a call, which would otherwise expire first and leave the call queued),
   and `degrade_after_timeouts` must be an integer ≥ 1. A bad value raises `ValueError` naming the
   key. Missing keys take the defaults `timeout_s` 600, `call_timeout_s` 90,
   `tool_timeouts_s` {}, `degrade_after_timeouts` 1 (the first two are today's runner defaults).
4. **`McpClient` applies the policy.** With its existing constructor
   (`McpClient(specs, *, timeout_s, call_timeout_s)`), the client behaves as the policy with
   `default_s=call_timeout_s`, no per-tool overrides and `degrade_after=1`: a call that exceeds its
   timeout raises `TimeoutError`, only that tool is degraded, and other tools on the same server
   still answer. The implementer wires the full policy from `mcp.json` into the runner (for example
   an optional `policy` argument on `McpClient`), sets a `tool_timeouts_s` entry for
   `macro__macro_synthesize_shock_vector` sized to its observed latency plus margin, and covers that
   wiring with unit tests in `tests/unit/`.

Out of scope: MacroRAG's own latency, retries of a timed-out call, and the grounding gate.

## Acceptance criteria

- **C1**: `CallTimeoutPolicy.from_settings(s).timeout_for(t)` equals `s["tool_timeouts_s"][t]` when
  `t` is in that map, otherwise `s["call_timeout_s"]`, exactly; with keys missing, the defaults in
  Requirement 3 apply. Oracle: Requirement 1 and 3 applied by hand to settings dicts built in the
  test.
- **C2**: `from_settings` raises `ValueError` for each invalid setting in Requirement 3: a timeout
  ≤ 0, NaN or infinite; a `call_timeout_s` or `tool_timeouts_s` value greater than `timeout_s`; a
  `degrade_after_timeouts` < 1 or not an integer (a `bool` is not an integer here). A call timeout
  equal to `timeout_s` is valid. Oracle: Requirement 3.
- **C3**: For a policy with `degrade_after = n`, `is_degraded(t)` is false until `record_timeout(t)`
  has been called `n` times, and true from then on; recording timeouts for one tool never degrades
  another. Oracle: Requirement 2.
- **C4**: Against a real stdio MCP server (a committed test fixture) exposing a `slow` tool and a
  `fast` tool, an `McpClient(specs, timeout_s=T, call_timeout_s=c)`:
  (a) raises `TimeoutError` on a `slow` call that outlasts `c`, after at least `c` and well before
  the tool would have finished;
  (b) then answers a `fast` call on the same server with its normal result;
  (c) then raises `TimeoutError` on a second `slow` call without waiting (elapsed well under `c`).
  Oracle: Requirements 2 and 4, measured with wall-clock bounds whose margins are stated in the test.

## Interface

New module `src/scenariogen/llm/mcp/timeouts.py`:

- `src.scenariogen.llm.mcp.timeouts.CallTimeoutPolicy.__init__(self, *, default_s: float,
  per_tool_s: dict[str, float], degrade_after: int) -> None`
- `src.scenariogen.llm.mcp.timeouts.CallTimeoutPolicy.from_settings(cls, mcp_settings:
  dict[str, Any]) -> CallTimeoutPolicy` (classmethod)
- `src.scenariogen.llm.mcp.timeouts.CallTimeoutPolicy.timeout_for(self, tool: str) -> float`
- `src.scenariogen.llm.mcp.timeouts.CallTimeoutPolicy.record_timeout(self, tool: str) -> None`
- `src.scenariogen.llm.mcp.timeouts.CallTimeoutPolicy.is_degraded(self, tool: str) -> bool`

`tool` is the namespaced tool name (`<server prefix>__<tool>`). Existing signatures are unchanged
and exercised as they are: `src.scenariogen.llm.mcp.client.McpClient(specs: dict[str, ServerSpec],
*, timeout_s: float = 600.0, call_timeout_s: float = 90.0)` with `connect()`/`close()` (or use as a
context manager), `list_tools(server)` and `call_tool(server, tool, arguments) -> str`;
`ServerSpec(name, command, args, cwd=None, env={}, tool_prefix="")`.

## Notes

- **C4 fixture server.** A small Python script committed under the spec's test directory, started
  by `McpClient` through a `ServerSpec` whose `command` is the running interpreter
  (`sys.executable`) and whose `args` are the script path. Build it with the pinned SDK
  (`mcp==1.12.4`, `from mcp.server.fastmcp import FastMCP`), stdio transport. `slow` must block in a
  **worker thread** (`await anyio.to_thread.run_sync(time.sleep, seconds)`), as MacroRAG's tools do,
  so the server's event loop stays free to answer `fast` while the abandoned `slow` call is still
  running and client-side cancellation can't stop it. `fast` returns a fixed JSON string.
- Suggested timing: `c` = 1.5s, `slow` sleeps 8s, `T` = 30s. Assert (a) elapsed ≥ `c` and < `c` + 4s;
  (c) elapsed < 1s. Keep total test time small; close the client in a `finally`/context manager so
  the subprocess is reaped.
- No network: stdio subprocess only. No `data/`, no current date.
- The fixture server is spawned with the parent environment (`ServerSpec.full_env`); it imports only
  `mcp`, `anyio` and the standard library.
