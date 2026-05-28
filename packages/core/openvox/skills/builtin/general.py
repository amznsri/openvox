"""Generic skills useful to most agents — time, search, knowledge-base."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext
from openvox.utils.http import make_async_client


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
        async with make_async_client(timeout=10.0) as client:
            r = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": q, "format": "json", "no_html": 1, "skip_disambig": 1},
            )
        # DDG returns 202 when no instant-answer exists for the query — that's
        # not an error from our side, just an empty result. Anything else is a
        # real upstream failure.
        if r.status_code not in (200, 202):
            return {"error": f"upstream {r.status_code}", "query": q}
        try:
            d = r.json()
        except Exception:
            return {"query": q, "abstract": "", "answer": "", "definition": "", "related": []}
        related = [t.get("Text") for t in (d.get("RelatedTopics") or [])[:3] if t.get("Text")]
        return {
            "query": q,
            "abstract": d.get("AbstractText", ""),
            "answer": d.get("Answer", ""),
            "definition": d.get("Definition", ""),
            "related": related,
        }


SKILLS = [GetTime, WebSearch]
