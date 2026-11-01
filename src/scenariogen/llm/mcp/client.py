"""Stdio MCP client (P1).

Connects to the ARag (`arag-analyst`) and MacroRAG (`macro-rag`) MCP servers as a *client*,
over stdio, using the official `mcp` SDK. The SDK is async; the rest of the scenariogen LLM
flow (gateway, pricing engine) is synchronous, so `McpClient` owns a background event-loop
thread and exposes a small synchronous facade (`list_tools` / `call_tool`). Sessions are opened
once on `connect()` and held for the life of the client (the servers are heavy to spawn).

The heavy `mcp` import is deferred into the connect coroutine (same pattern as the MacroRAG
`RequestsTransport`), so importing this module never requires the SDK or a running server.

Each tool call is bounded by its own timeout from a `CallTimeoutPolicy` (spec mcp-call-timeouts); a
tool that keeps timing out is skipped for the rest of the run, while the other tools on its server
keep working.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, cast

from .timeouts import CallTimeoutPolicy

logger = logging.getLogger("scenariogen.llm.mcp.client")

_NS_SEP = "__"  # namespaced tool names, as the registry builds them: <prefix>__<tool>
# asyncio can fire a timer up to two clock ticks early: the deadline is read from a clock that lags by up
# to a tick, and the loop runs timers due within one more tick (GetTickCount64: ~15.6ms on Windows). A bare
# wait_for could give up just before the timeout; this padding lets every call use its full timeout.
_TIMER_SLACK_S = 2 * time.get_clock_info("monotonic").resolution


@dataclass(frozen=True)
class ServerSpec:
    """A launch spec for one MCP server (from configs/scenariogen/llm/mcp.json)."""

    name: str
    command: str
    args: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    tool_prefix: str = ""

    def full_env(self) -> dict[str, str]:
        """The child's environment: the parent env with this spec's overrides applied.

        mcp's `StdioServerParameters` *replaces* the child env entirely when set, so we merge
        with `os.environ` to keep PATH / conda vars the server needs.
        """
        merged = dict(os.environ)
        merged.update(self.env)
        return merged

    @property
    def prefix(self) -> str:
        return self.tool_prefix or self.name

    @classmethod
    def from_dict(cls, name: str, spec: dict[str, Any]) -> ServerSpec:
        return cls(
            name=name,
            command=str(spec["command"]),
            args=[str(a) for a in spec.get("args", [])],
            cwd=str(spec["cwd"]) if spec.get("cwd") else None,
            env={str(k): str(v) for k, v in spec.get("env", {}).items()},
            tool_prefix=str(spec.get("tool_prefix", name)),
        )


@dataclass(frozen=True)
class ToolListing:
    """A tool a server advertises (the subset the registry needs)."""

    name: str
    description: str
    input_schema: dict[str, Any]


def parse_server_specs(mcp_settings: dict[str, Any]) -> dict[str, ServerSpec]:
    servers = mcp_settings.get("servers", {})
    return {name: ServerSpec.from_dict(name, spec) for name, spec in servers.items()}


def _content_to_text(result: Any) -> str:
    """Flatten an MCP `CallToolResult` to text (FastMCP servers return JSON as text content)."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
    return "\n".join(parts) if parts else "{}"


class McpClient:
    """Synchronous facade over async MCP stdio sessions, backed by a background event loop.

    The `mcp` SDK's stdio sessions use anyio cancel scopes that must be entered **and** exited in
    the same task. So a single long-lived ``_serve`` coroutine opens the sessions, services calls
    submitted via a queue, and closes them on shutdown — all in one task. The sync methods enqueue
    work and block on a `concurrent.futures.Future`.
    """

    def __init__(
        self,
        specs: dict[str, ServerSpec],
        *,
        timeout_s: float = 600.0,
        call_timeout_s: float = 90.0,
        policy: CallTimeoutPolicy | None = None,
    ):
        """Builds an unconnected client.

        Args:
            specs: Launch specs per server name.
            timeout_s: Overall wait for connect, close and each queued call.
            call_timeout_s: Call timeout for every tool when no ``policy`` is given.
            policy: Per-tool timeouts and degradation (``CallTimeoutPolicy.from_settings``);
                default: ``call_timeout_s`` for every tool, degraded after one timeout.
        """
        self._specs = specs
        self._timeout = timeout_s
        self._policy = policy or CallTimeoutPolicy(default_s=call_timeout_s, per_tool_s={}, degrade_after=1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queue: Any = None  # asyncio.Queue, created on the loop
        self._ready: Future[None] | None = None
        self._serve_future: Any = None
        self._closed = False
        self._sessions: dict[str, Any] = {}

    # -- lifecycle ----------------------------------------------------------- #
    def __enter__(self) -> McpClient:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-loop", daemon=True)
        self._thread.start()
        self._ready = Future()
        self._serve_future = asyncio.run_coroutine_threadsafe(self._serve(), self._loop)
        try:
            self._ready.result(self._timeout)  # block until sessions are open (or raise)
        except BaseException:
            self._shutdown_loop()
            raise

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            if not self._closed and self._queue is not None:
                self._closed = True
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)  # sentinel → _serve closes in-task
                if self._serve_future is not None:
                    self._serve_future.result(self._timeout)
        except Exception as exc:  # closing best-effort — a dead server shouldn't mask the run
            logger.warning("error closing MCP sessions: %s", exc)
        finally:
            self._shutdown_loop()

    def _shutdown_loop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=self._timeout)
        loop.close()
        self._loop = None
        self._thread = None
        self._queue = None
        self._serve_future = None
        self._sessions = {}

    # -- public sync API ----------------------------------------------------- #
    def server_names(self) -> list[str]:
        return list(self._specs)

    def list_tools(self, server: str) -> list[ToolListing]:
        async def _work() -> list[ToolListing]:
            resp = await self._session(server).list_tools()
            return [
                ToolListing(
                    name=str(tool.name),
                    description=str(tool.description or ""),
                    input_schema=dict(tool.inputSchema or {}),
                )
                for tool in resp.tools
            ]

        return cast("list[ToolListing]", self._submit(server, None, _work))

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        async def _work() -> str:
            result = await self._session(server).call_tool(tool, arguments)
            if getattr(result, "isError", False):
                logger.warning("MCP %s.%s returned an error result", server, tool)
            return _content_to_text(result)

        return cast("str", self._submit(server, self._tool_key(server, tool), _work))

    # -- internals ----------------------------------------------------------- #
    def _tool_key(self, server: str, tool: str) -> str:
        spec = self._specs.get(server)
        return f"{spec.prefix if spec else server}{_NS_SEP}{tool}"

    def _submit(self, server: str, tool_key: str | None, work: Any) -> Any:
        if self._loop is None or self._queue is None:
            raise RuntimeError("McpClient is not connected; call connect() first")
        fut: Future[Any] = Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (server, tool_key, work, fut))
        return fut.result(self._timeout)

    async def _serve(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        assert self._loop is not None and self._ready is not None
        self._queue = asyncio.Queue()
        stack = AsyncExitStack()
        try:
            for name, spec in self._specs.items():
                params = StdioServerParameters(
                    command=spec.command, args=spec.args, env=spec.full_env(), cwd=spec.cwd
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._sessions[name] = session
                logger.info("connected MCP server %r (cmd=%s)", name, spec.command)
        except BaseException as exc:
            await stack.aclose()
            self._loop.call_soon_threadsafe(self._ready.set_exception, exc)
            return
        self._loop.call_soon_threadsafe(self._ready.set_result, None)
        try:
            while True:
                item = await self._queue.get()
                if item is None:  # shutdown sentinel
                    break
                server, tool_key, work, fut = item
                if tool_key is not None and self._policy.is_degraded(tool_key):  # it kept hanging — don't wait
                    fut.set_exception(TimeoutError(f"MCP tool {tool_key!r} previously timed out; skipped"))
                    continue
                call_timeout = self._policy.timeout_for(tool_key) if tool_key else self._policy.default_s
                try:
                    fut.set_result(await asyncio.wait_for(work(), call_timeout + _TIMER_SLACK_S))
                except TimeoutError:
                    # wait_for cancelled the hung call. Count it against this tool only, so the
                    # server's other tools keep working (the tool loop feeds the error to the model).
                    if tool_key is not None:
                        self._policy.record_timeout(tool_key)
                    logger.warning("MCP %s call exceeded %.0fs%s", tool_key or server, call_timeout,
                                   "; tool degraded" if tool_key and self._policy.is_degraded(tool_key) else "")
                    fut.set_exception(TimeoutError(f"MCP {tool_key or server!r} call exceeded {call_timeout:.0f}s"))
                except BaseException as exc:  # surface tool/list failures to the caller
                    fut.set_exception(exc)
        finally:
            await stack.aclose()  # same task that entered the contexts

    def _session(self, server: str) -> Any:
        if server not in self._sessions:
            raise KeyError(f"unknown MCP server {server!r} (have {list(self._sessions)})")
        return self._sessions[server]
