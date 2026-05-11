"""MCP server inspection — dashboard uses this to validate a server config
before saving it on an agent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openvox.mcp import list_mcp_tools

router = APIRouter()


class ProbeRequest(BaseModel):
    name: str = "probe"
    transport: str = "stdio"  # stdio | sse
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""


@router.post("/probe")
async def probe(req: ProbeRequest) -> dict[str, Any]:
    """Connect to a single MCP server, list its tools, then disconnect.
    Returns `{tools: [...]}` on success or raises 400 with the error."""
    try:
        tools = await list_mcp_tools(req.model_dump())
    except Exception as e:
        raise HTTPException(400, f"probe failed: {e}") from e
    return {"tools": tools, "count": len(tools)}
