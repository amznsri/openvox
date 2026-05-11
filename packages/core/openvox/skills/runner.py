"""Per-session skill runner — owns the subset of skills the agent has enabled."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openvox.skills.base import SkillContext, SkillResult
from openvox.skills.registry import get_skill_registry

logger = logging.getLogger(__name__)


class SkillRunner:
    def __init__(
        self,
        *,
        skill_ids: list[str],
        ctx: SkillContext | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._ids = skill_ids
        self._ctx = ctx or SkillContext()
        self._timeout = timeout_s

    def tool_specs(self) -> list[dict[str, Any]]:
        reg = get_skill_registry()
        out = []
        for sid in self._ids:
            sk = reg.get(sid)
            if sk is not None:
                out.append(sk.to_tool_spec())
        return out

    async def invoke(self, name: str, args: Any) -> dict[str, Any]:
        # LLM tools sometimes pass JSON string args; normalise to dict.
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {"_value": args}

        sk = get_skill_registry().get(name)
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
