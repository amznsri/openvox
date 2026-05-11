"""Skills for the education / tutoring template."""

from __future__ import annotations

import math
from typing import Any

from openvox.skills.base import BaseSkill, SkillContext


class Calculator(BaseSkill):
    id = "calculator"
    display_name = "Calculator"
    description = "Evaluate a basic arithmetic expression. Supports +,-,*,/,**, sqrt, sin, cos, tan, log."
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. 'sqrt(2) * 3 + 5'"},
        },
        "required": ["expression"],
    }
    _ALLOWED = {
        "abs": abs, "round": round, "min": min, "max": max,
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log, "log10": math.log10, "exp": math.exp,
        "pi": math.pi, "e": math.e,
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        expr = (args.get("expression") or "").strip()
        if not expr:
            return {"error": "empty expression"}
        try:
            # Restrict eval scope to mathematical names only.
            value = eval(expr, {"__builtins__": {}}, self._ALLOWED)
            return {"expression": expr, "result": value}
        except Exception as e:
            return {"error": str(e), "expression": expr}


class ExplainConcept(BaseSkill):
    id = "explain_concept"
    display_name = "Explain a concept"
    description = (
        "Return a structured outline (definition, key points, example) for a science or "
        "mathematics topic. The LLM will turn this into a tutorial."
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "level": {"type": "string", "enum": ["primary", "secondary", "undergraduate"], "default": "secondary"},
        },
        "required": ["topic"],
    }

    async def run(self, args: dict[str, Any], ctx: SkillContext) -> Any:
        topic = (args.get("topic") or "").strip()
        level = args.get("level") or "secondary"
        # Stub: in production, fetch from a curated kb / RAG.
        return {
            "topic": topic,
            "level": level,
            "outline": [
                "definition",
                "core principles (3 bullet-points)",
                "worked example",
                "common misconceptions",
                "further reading",
            ],
        }


SKILLS = [Calculator, ExplainConcept]
