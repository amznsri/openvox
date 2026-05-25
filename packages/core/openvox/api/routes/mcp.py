"""MCP server inspection + curated catalogue.

Two things live here:
  - `/api/v1/mcp/probe`     — connect to a user-supplied server config,
                              list its tools, return them so the dashboard
                              can validate before saving.
  - `/api/v1/mcp/catalogue` — curated list of "use this MCP server"
                              one-click entries (Slack / GitHub / Notion /
                              HubSpot / Salesforce / Stripe). The dashboard
                              MCP tab shows these as cards; clicking one
                              pre-fills the config form with the right
                              command + env field placeholders.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openvox.mcp import probe_mcp_server

logger = logging.getLogger(__name__)
router = APIRouter()

# Catalogue file lives alongside the mcp package so the data ships with
# every install. Edit `openvox/mcp/catalogue.json` to add / remove entries.
_CATALOGUE_PATH = Path(__file__).resolve().parents[2] / "mcp" / "catalogue.json"


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

    Returns ``{tools: [...], count: N, error: str|None}``.

    Successful probe → ``error == None``, ``count > 0``, ``tools``
    populated. Connect-failed → ``error`` carries a human-friendly
    explanation (e.g. "command not found: 'npx'. Make sure the
    binary is on the daemon's PATH..."), ``count == 0``. The
    dashboard's Probe button reads ``error`` to surface a real
    message in the badge area instead of a silent green-check-zero.

    Only raises 400 on unexpected exceptions (the actual code path
    inside `probe_mcp_server` catches connect failures and returns
    them in the response).
    """
    try:
        result = await probe_mcp_server(req.model_dump())
    except Exception as e:
        raise HTTPException(400, f"probe failed: {e}") from e
    return result


@router.get("/catalogue")
async def get_catalogue() -> list[dict[str, Any]]:
    """Return the curated MCP server catalogue.

    Each entry has the fields the dashboard needs to render a card and
    pre-fill the per-agent MCP config form:

        id              short slug, also the suggested config `name`
        name            display label
        tagline         one-line "what this MCP server does"
        transport       "stdio" | "sse"
        command, args   exec invocation for stdio servers
        env_required    env vars the user must fill in before it works
        env_optional    env vars that can be omitted
        docs_url        deep link to the upstream README
        icon            emoji for the card
        category        loose grouping ("crm", "devtools", ...)
    """
    try:
        with _CATALOGUE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("mcp catalogue file not found at %s", _CATALOGUE_PATH)
        return []
    except Exception as e:
        logger.exception("failed to load mcp catalogue")
        raise HTTPException(500, f"catalogue load failed: {e}") from e
