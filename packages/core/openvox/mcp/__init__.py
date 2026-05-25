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
"""

from openvox.mcp.bridge import (
    MCPSessionManager,
    build_mcp_skills,
    list_mcp_tools,
    probe_mcp_server,
)

__all__ = [
    "MCPSessionManager",
    "build_mcp_skills",
    "list_mcp_tools",
    "probe_mcp_server",
]
