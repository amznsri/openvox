"""Base classes and the @skill decorator."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillContext:
    """Per-invocation context handed to a skill."""

    session_id: str = ""
    agent_id: str = ""
    user_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    ok: bool = True
    output: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "output": self.output, "error": self.error}


class BaseSkill(ABC):
    """Abstract base. Subclass and implement `run`."""

    id: str
    display_name: str = ""
    description: str = ""
    # JSON schema for the function-call arguments (OpenAI-tool format)
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    # Pydantic-shape config defaults (optional). Dashboard renders a form.
    config_schema: dict[str, Any] = {}

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any: ...

    def to_tool_spec(self) -> dict[str, Any]:
        """OpenAI-style tool spec (consumed by the LLM via function calling)."""
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description or self.display_name or self.id,
                "parameters": self.parameters,
            },
        }


# ──────────────────────────────────────────────────────────────────────────
# Convenience decorator for ad-hoc function-style skills:
#
#   @skill(id="get_weather", description="Look up current weather",
#          parameters={"type":"object", "properties":{"city":{"type":"string"}}})
#   async def weather(args, ctx):
#       return {"temp": 72}
# ──────────────────────────────────────────────────────────────────────────


def skill(
    *,
    id: str,
    description: str = "",
    display_name: str = "",
    parameters: dict[str, Any] | None = None,
    config_schema: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], type[BaseSkill]]:
    """Decorator that turns an async function into a `BaseSkill` subclass.

    The decorated function must be async and accept `(args: dict, ctx: SkillContext)`.
    """

    params = parameters or {"type": "object", "properties": {}}

    def wrap(fn: Callable[..., Any]) -> type[BaseSkill]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError("@skill expects an `async def` function")

        cls_name = f"Skill_{id.replace('-', '_').replace('.', '_')}"

        async def _run(self: BaseSkill, args: dict[str, Any], ctx: SkillContext) -> Any:
            return await fn(args, ctx)

        cls = type(
            cls_name,
            (BaseSkill,),
            {
                "id": id,
                "display_name": display_name or id,
                "description": description,
                "parameters": params,
                "config_schema": config_schema or {},
                "run": _run,
            },
        )
        return cls  # type: ignore[return-value]

    return wrap
