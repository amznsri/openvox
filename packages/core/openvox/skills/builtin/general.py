"""Generic skills useful to most agents — time, search, knowledge-base."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from openvox.skills.base import BaseSkill, SkillContext


class GetTime(BaseSkill):
    id = "get_time"
    display_name = "Get current time"
    description = "Return the current UTC timestamp."
    parameters = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        return {"utc": datetime.now(timezone.utc).isoformat()}


class WebSearch(BaseSkill):
    """Light-weight DuckDuckGo Instant-Answer probe — no API key needed.

    Real production agents would swap this with Tavily / Brave / Bing.
    """

    id = "web_search"
    display_name = "Web search"
    description = "Search the web for a query and return the top instant-answer."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for"},
        },
        "required": ["query"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        q = (args.get("query") or "").strip()
        if not q:
            return {"error": "empty query"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": q, "format": "json", "no_html": 1, "skip_disambig": 1},
            )
        if r.status_code != 200:
            return {"error": f"upstream {r.status_code}"}
        d = r.json()
        return {
            "abstract": d.get("AbstractText", ""),
            "answer": d.get("Answer", ""),
            "definition": d.get("Definition", ""),
            "related": [t.get("Text") for t in (d.get("RelatedTopics") or [])[:3]],
        }


SKILLS = [GetTime, WebSearch]
