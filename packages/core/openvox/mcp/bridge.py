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
                logger.warning("mcp: failed to connect server %s: %s", name, e)
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


def _make_skill(server_name: str, session: Any, tool: Any) -> BaseSkill:
    skill_id = f"mcp__{_safe_id(server_name)}__{_safe_id(tool.name)}"
    description = (tool.description or "").strip() or f"MCP tool {tool.name} on server {server_name}"
    parameters = tool.inputSchema or {"type": "object", "properties": {}}

    class _MCPSkill(BaseSkill):
        id = skill_id  # type: ignore[misc]
        display_name = f"{server_name}: {tool.name}"
        # Limit description to keep the LLM tool-spec compact.
        description = description[:1000]  # type: ignore[misc]
        parameters = parameters  # type: ignore[misc]

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
    """Quick one-shot: connect to a single server, return tool descriptors,
    disconnect. Used by the dashboard for the "Probe" button on the MCP
    config form."""
    async with MCPSessionManager([config]) as mgr:
        return [
            {"id": sk.id, "display_name": sk.display_name, "description": sk.description}
            for sk in mgr.skills
        ]
