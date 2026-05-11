"""Per-session skill runner — owns the subset of skills the agent has enabled."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext, SkillResult
from openvox.skills.registry import get_skill_registry

logger = logging.getLogger(__name__)


class SkillRunner:
    """Owns the subset of skills available for one VoiceSession.

    Skills come from two sources:
      - `skill_ids`     — names looked up in the global skill registry
                          (built-ins + local + entry-point installed).
      - `extra_skills`  — ad-hoc skill instances passed in directly. Used
                          by the MCP bridge so per-session tools don't
                          pollute the global registry.
    """

    def __init__(
        self,
        *,
        skill_ids: list[str],
        ctx: SkillContext | None = None,
        timeout_s: float = 30.0,
        extra_skills: list[BaseSkill] | None = None,
    ) -> None:
        self._ids = skill_ids
        self._ctx = ctx or SkillContext()
        self._timeout = timeout_s
        # Keyed by skill id for O(1) dispatch from LLM tool-calls.
        self._extras: dict[str, BaseSkill] = {sk.id: sk for sk in (extra_skills or [])}

    def tool_specs(self) -> list[dict[str, Any]]:
        reg = get_skill_registry()
        out: list[dict[str, Any]] = []
        for sid in self._ids:
            sk = reg.get(sid)
            if sk is not None:
                out.append(sk.to_tool_spec())
        for sk in self._extras.values():
            out.append(sk.to_tool_spec())
        return out

    def _lookup(self, name: str) -> BaseSkill | None:
        # Per-session MCP skills win over the global registry — useful if
        # an MCP server intentionally shadows a built-in (e.g. a custom
        # `get_quote`).
        if name in self._extras:
            return self._extras[name]
        return get_skill_registry().get(name)

    async def invoke(self, name: str, args: Any) -> dict[str, Any]:
        # LLM tools sometimes pass JSON string args; normalise to dict.
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {"_value": args}

        sk = self._lookup(name)
        if sk is None:
            return SkillResult(ok=False, error=f"unknown skill: {name}").to_dict()
        try:
            output = await asyncio.wait_for(sk.run(args, self._ctx), timeout=self._timeout)
            return SkillResult(ok=True, output=output).to_dict()
        except asyncio.TimeoutError:
            return SkillResult(ok=False, error="skill timed out").to_dict()
        except Exception as e:
            logger.exception("skill %s raised", name)
            return SkillResult(ok=False, error=str(e)).to_dict()
