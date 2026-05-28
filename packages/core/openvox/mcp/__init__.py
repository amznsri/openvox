"""Model Context Protocol client + tool bridge.

Each agent can declare zero or more MCP server configs (stdio command or
SSE URL). At session start the runner calls `build_mcp_skills(configs)`
which connects to each server, lists its tools, and returns one
synthesised `BaseSkill` per tool. Those skills are added to the agent's
tool set before the LLM turn — to the LLM they look identical to local
built-in skills.

Public API:
    build_mcp_skills(configs)  → (skills, manager)
    list_mcp_tools(config)     → tools-only inspection helper
    probe_mcp_server(config)   → tools + error inspection helper
                                  (used by the dashboard Probe button)
    open_agent_mcp(configs)    → ``async with``-friendly turn-scoped
                                  bootstrap shared by every text-mode
                                  transport (Telegram, /turn endpoint,
                                  future WhatsApp / WeChat).
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from openvox.mcp.bridge import (
    MCPSessionManager,
    build_mcp_skills,
    list_mcp_tools,
    probe_mcp_server,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_agent_mcp(mcp_servers: list[dict[str, Any]] | None):
    """Async-context helper that yields a SkillRunner-compatible extras list.

    Why this exists:
        The voice WS, the text-mode ``/turn`` endpoint, the Telegram
        inbound handler, and (soon) the WhatsApp / WeChat handlers all
        need the SAME plumbing — given an agent's ``mcp_servers`` JSON,
        spawn the subprocesses / open the SSE connections, list each
        server's tools, expose them as ``BaseSkill`` instances, and tear
        everything down again when the turn / call ends.

        Without a shared helper, only the voice WS bothered to do this
        in v0.1.8, so text-mode transports (``/turn``, Telegram) saw
        an empty MCP toolset and the LLM dutifully told the user to
        "configure your Google OAuth on the MCP tab" — the system
        prompt's fallback for missing tools. That asymmetry is the bug
        this helper fixes.

    Usage::

        async with open_agent_mcp(agent.mcp_servers) as extras:
            runner = SkillRunner(skill_ids=skills, extra_skills=extras)
            ...  # LLM turn
        # On exit the subprocesses are reaped + SSE sockets closed.

    Failure-tolerant by design: if the MCP setup raises (subprocess
    binary missing, server crashes during ``initialize``, network blip),
    we log and yield an empty list rather than re-raising. A transient
    MCP failure shouldn't kill a turn the LLM could still complete with
    built-in tools.
    """
    if not mcp_servers:
        yield []
        return
    mgr = MCPSessionManager(mcp_servers)
    setup_ok = False
    extras: list = []
    try:
        try:
            await mgr.__aenter__()
            extras = list(mgr.skills)
            setup_ok = True
            logger.info("mcp: bridged %d tools for this turn", len(extras))
        except Exception as e:
            logger.warning("mcp: setup failed, continuing without bridged tools: %s", e)
            extras = []
        yield extras
    finally:
        if setup_ok:
            try:
                await mgr.__aexit__(None, None, None)
            except Exception:
                logger.exception("mcp: teardown error (non-fatal)")


__all__ = [
    "MCPSessionManager",
    "build_mcp_skills",
    "list_mcp_tools",
    "probe_mcp_server",
    "open_agent_mcp",
]
