"""MCP client + tool→skill bridge.

The official `mcp` Python SDK gives us a `ClientSession` that speaks
JSON-RPC to a server over stdio or SSE. We:

  1. Spawn / connect to each configured server.
  2. `await session.list_tools()` → list of `Tool` objects.
  3. Synthesise one OpenVox `BaseSkill` per tool that, on `run()`,
     calls `session.call_tool(name, args)` and returns the result.

Skill ids are namespaced so two servers exposing the same tool name
don't collide. Example: `mcp__github__get_issue`.

The `MCPSessionManager` owns the live MCP sessions for the lifetime of
a `VoiceSession`. It MUST be closed when the session ends, otherwise
stdio subprocesses leak.
"""

from __future__ import annotations

import logging
import os
from contextlib import AsyncExitStack
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext

logger = logging.getLogger(__name__)


# ── Lazy SDK import — keeps `import openvox` cheap when MCP isn't used.


def _import_mcp():
    try:
        from mcp import ClientSession, StdioServerParameters  # type: ignore
        from mcp.client.stdio import stdio_client  # type: ignore

        try:
            from mcp.client.sse import sse_client  # type: ignore
        except ImportError:
            sse_client = None  # SSE optional
        return ClientSession, StdioServerParameters, stdio_client, sse_client
    except ImportError as e:
        raise RuntimeError(
            "The `mcp` package is not installed. Add `mcp>=1.2.0` to "
            "packages/core/pyproject.toml and rebuild the core image."
        ) from e


# ── Session manager ──────────────────────────────────────────────


class MCPSessionManager:
    """Owns the live MCP sessions and the resources backing them.

    Use as an async context manager:

        async with MCPSessionManager(configs) as mgr:
            skills = mgr.skills
            ...
    """

    def __init__(self, configs: list[dict[str, Any]]) -> None:
        self._configs = configs or []
        self._exit_stack: AsyncExitStack | None = None
        self.sessions: dict[str, Any] = {}  # server-name → ClientSession
        self.skills: list[BaseSkill] = []
        # Per-server connect errors. Empty when everything succeeded.
        # Surfaced via list_mcp_tools() so the dashboard Probe can show
        # the real reason a server returned zero tools instead of a
        # silent green-check-zero-tools UX.
        self.errors: dict[str, str] = {}

    async def __aenter__(self) -> "MCPSessionManager":
        if not self._configs:
            return self
        self._exit_stack = AsyncExitStack()
        ClientSession, StdioServerParameters, stdio_client, sse_client = _import_mcp()

        for cfg in self._configs:
            name = (cfg.get("name") or "").strip() or f"mcp_{len(self.sessions)}"
            transport = (cfg.get("transport") or "stdio").lower()
            try:
                if transport == "stdio":
                    params = StdioServerParameters(
                        command=cfg.get("command") or "",
                        args=list(cfg.get("args") or []),
                        env={**os.environ, **(cfg.get("env") or {})},
                    )
                    read, write = await self._exit_stack.enter_async_context(stdio_client(params))
                elif transport == "sse":
                    if sse_client is None:
                        raise RuntimeError("MCP SSE transport not available in this SDK version")
                    url = cfg.get("url") or ""
                    if not url:
                        raise ValueError("sse transport requires `url`")
                    read, write = await self._exit_stack.enter_async_context(sse_client(url))
                else:
                    raise ValueError(f"unknown transport {transport!r}")

                session = await self._exit_stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.sessions[name] = session

                # Pull tool list and wrap each as a skill.
                tools_result = await session.list_tools()
                for tool in tools_result.tools:
                    self.skills.append(_make_skill(name, session, tool))
                logger.info("mcp: connected %s (%d tools)", name, len(tools_result.tools))
            except Exception as e:
                # Compose a user-friendly error message. Common cases the
                # dashboard's Probe UI should be able to show verbatim:
                #   - `[Errno 2] No such file or directory: 'npx'` →
                #     "command not found; install Node + npm, then
                #     `openvox restart`."
                #   - JSON-RPC initialize timeout from `ClientSession`
                #     when the subprocess crashed at startup.
                #   - Specific MCP-server errors written to stderr.
                msg = _humanise_mcp_error(name, cfg, e)
                self.errors[name] = msg
                logger.warning("mcp: failed to connect server %s: %s", name, msg)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self.sessions.clear()
        self.skills.clear()


# ── Tool → Skill bridge ──────────────────────────────────────────


def _safe_id(s: str) -> str:
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in s)


def _humanise_mcp_error(name: str, cfg: dict[str, Any], exc: Exception) -> str:
    """Translate the raw exception text into something a dashboard user
    can act on. Falls back to the raw string if no pattern matches.

    Patterns we currently recognise:
      - "[Errno 2] No such file or directory" → missing executable
        (typical: npx not on PATH because daemon was launched without
        /opt/homebrew/bin)
      - "OAuth keys file not found" → MCP server wants a credentials
        JSON file at a specific path; common with @gongrzhe/...
      - "asyncio.exceptions.CancelledError" raised during initialize →
        subprocess crashed during startup; check the daemon log for
        the subprocess stderr (the official mcp SDK pipes it to the
        parent's stderr).
    """
    raw = str(exc).strip() or exc.__class__.__name__
    command = cfg.get("command") or ""
    transport = cfg.get("transport") or "stdio"

    if "no such file or directory" in raw.lower():
        return (
            f"command not found: {command!r}. "
            "Make sure the binary is on the daemon's PATH "
            "(see launchd plist EnvironmentVariables.PATH on macOS, "
            "systemd unit Environment=PATH= on Linux). "
            f"Original error: {raw}"
        )
    if "oauth" in raw.lower() and "not found" in raw.lower():
        return (
            f"{name}: the MCP server needs an OAuth credentials file "
            f"that isn't in place. {raw}"
        )
    if transport == "sse" and ("connection" in raw.lower() or "timed out" in raw.lower()):
        return f"{name}: SSE connect failed — is the URL reachable? {raw}"
    return raw


def _make_skill(server_name: str, session: Any, tool: Any) -> BaseSkill:
    skill_id = f"mcp__{_safe_id(server_name)}__{_safe_id(tool.name)}"
    # Capture under different names so the class body below can reference
    # them safely. Python class-body scope has a footgun: an assignment
    # like `description = description[:1000]` inside the class body tries
    # to look up `description` in the class's local scope (not yet
    # populated by THIS assignment) — class bodies don't see enclosing
    # function locals, so the lookup raises NameError. Was eating EVERY
    # tool the MCP server returned silently — Probe always showed 0
    # tools. Fixed: rename outer vars + use clean RHS in the class body.
    desc_str = (
        (tool.description or "").strip()
        or f"MCP tool {tool.name} on server {server_name}"
    )[:1000]
    params_dict = tool.inputSchema or {"type": "object", "properties": {}}

    class _MCPSkill(BaseSkill):
        id = skill_id  # type: ignore[misc]
        display_name = f"{server_name}: {tool.name}"
        description = desc_str  # type: ignore[misc]
        parameters = params_dict  # type: ignore[misc]

        async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
            try:
                result = await session.call_tool(tool.name, arguments=args)
            except Exception as e:
                logger.exception("mcp call %s.%s failed", server_name, tool.name)
                return {"error": str(e)}
            # MCP CallToolResult has `content: list[TextContent|...]` and
            # `isError: bool`. Flatten to plain JSON.
            content_blocks: list[dict[str, Any]] = []
            for block in getattr(result, "content", []) or []:
                t = getattr(block, "type", None)
                if t == "text":
                    content_blocks.append({"type": "text", "text": getattr(block, "text", "")})
                else:
                    # image / resource — return a structural hint; LLM
                    # can decide what to do with it.
                    content_blocks.append({"type": t or "unknown", "data": str(block)[:1000]})
            return {
                "is_error": bool(getattr(result, "isError", False)),
                "content": content_blocks,
            }

    return _MCPSkill()


# ── Convenience helpers ──────────────────────────────────────────


async def build_mcp_skills(configs: list[dict[str, Any]]) -> tuple[list[BaseSkill], MCPSessionManager]:
    """Connect to all servers in `configs`, return (skills, manager).

    The caller MUST keep the manager alive (or use it as an async ctx mgr)
    for as long as the skills are usable.
    """
    mgr = MCPSessionManager(configs)
    await mgr.__aenter__()
    return list(mgr.skills), mgr


async def list_mcp_tools(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Backward-compatible wrapper around probe_mcp_server that returns
    just the tools list (no error). Pre-existing callers that don't
    care about per-server connect errors use this. For the Probe API
    route (which needs the error to show in the dashboard) use
    `probe_mcp_server` instead.
    """
    result = await probe_mcp_server(config)
    return result["tools"]


async def probe_mcp_server(config: dict[str, Any]) -> dict[str, Any]:
    """Connect to a single MCP server, list its tools, return both tools
    AND any error encountered while connecting. Disconnect on exit.

    Returns a dict with:
      - `tools`: list of `{id, display_name, description}` per tool
      - `error`: human-friendly string when the connect failed, or
                 None when it worked
      - `count`: convenience, equals `len(tools)`

    Used by the dashboard "Probe" button on the MCP config form so
    users see WHY a server returned zero tools instead of staring at
    a silent "0 tools" badge.
    """
    name = (config.get("name") or "").strip() or "probe"
    async with MCPSessionManager([config]) as mgr:
        tools = [
            {
                "id": sk.id,
                "display_name": sk.display_name,
                "description": sk.description,
            }
            for sk in mgr.skills
        ]
        error = mgr.errors.get(name)
    return {"tools": tools, "count": len(tools), "error": error}
