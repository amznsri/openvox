"""Skill catalogue + invoke endpoint (for testing from the dashboard)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openvox.skills import SkillContext, get_skill_registry
from openvox.skills.runner import SkillRunner

router = APIRouter()


@router.get("")
async def list_skills() -> list[dict[str, Any]]:
    return get_skill_registry().list()


class InvokeRequest(BaseModel):
    skill_id: str
    args: dict[str, Any] = {}


@router.post("/invoke")
async def invoke_skill(req: InvokeRequest) -> dict[str, Any]:
    runner = SkillRunner(skill_ids=[req.skill_id], ctx=SkillContext(metadata={"source": "dashboard"}))
    if get_skill_registry().get(req.skill_id) is None:
        raise HTTPException(404, "skill not found")
    return await runner.invoke(req.skill_id, req.args)
