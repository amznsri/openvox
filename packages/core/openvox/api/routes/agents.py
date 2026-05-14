"""Agent CRUD."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from openvox.config import get_settings
from openvox.db import db_session
from openvox.db.models import Agent, AgentStatus, Document, DocumentChunk

router = APIRouter()


class AgentIn(BaseModel):
    name: str
    description: str = ""
    template_id: str | None = None
    stt_provider: str = "byteplus"
    tts_provider: str = "byteplus"
    llm_provider: str = "byteplus"
    # Empty → server fills from settings.byteplus_llm_model on create.
    llm_model: str = ""
    voice_id: str = ""
    voice_speed: float = 1.0
    voice_language: str = "en-US"
    system_prompt: str = "You are a helpful voice assistant."
    greeting: str = "Hi, how can I help you?"
    temperature: float = 0.7
    max_tokens: int = 2048
    skills: list[str] = Field(default_factory=list)
    channels: dict[str, Any] = Field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    voice_map: dict[str, str] = Field(default_factory=dict)
    # "silero" (default) | "none" — controls server-side VAD interrupt path.
    vad_provider: str = "silero"


class AgentOut(AgentIn):
    id: str
    status: str
    created_at: str
    updated_at: str


def _to_out(a: Agent) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "template_id": a.template_id,
        "stt_provider": a.stt_provider,
        "tts_provider": a.tts_provider,
        "llm_provider": a.llm_provider,
        "llm_model": a.llm_model,
        "voice_id": a.voice_id,
        "voice_speed": a.voice_speed,
        "voice_language": a.voice_language,
        "system_prompt": a.system_prompt,
        "greeting": a.greeting,
        "temperature": a.temperature,
        "max_tokens": a.max_tokens,
        "skills": a.skills or [],
        "channels": a.channels or {},
        "mcp_servers": a.mcp_servers or [],
        "voice_map": a.voice_map or {},
        "vad_provider": getattr(a, "vad_provider", "silero") or "silero",
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else "",
        "updated_at": a.updated_at.isoformat() if a.updated_at else "",
    }


@router.get("")
async def list_agents() -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (await s.execute(select(Agent).order_by(Agent.updated_at.desc()))).scalars().all()
        return [_to_out(a) for a in rows]


@router.post("", status_code=201)
async def create_agent(body: AgentIn) -> dict[str, Any]:
    settings = get_settings()
    fields = body.model_dump()
    # Fill in env-driven defaults for fields the caller left blank.
    if not fields.get("llm_model"):
        fields["llm_model"] = settings.byteplus_llm_model
    if not fields.get("voice_id"):
        fields["voice_id"] = settings.byteplus_tts_default_voice
    async with db_session() as s:
        a = Agent(**fields)
        s.add(a)
        await s.flush()
        return _to_out(a)


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        return _to_out(a)


@router.put("/{agent_id}")
async def update_agent(agent_id: str, body: AgentIn) -> dict[str, Any]:
    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        for k, v in body.model_dump().items():
            setattr(a, k, v)
        await s.flush()
        return _to_out(a)


@router.post("/{agent_id}/publish")
async def publish_agent(agent_id: str) -> dict[str, Any]:
    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        a.status = AgentStatus.PUBLISHED.value
        await s.flush()
        return _to_out(a)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        # Same in-route cascade pattern as bug #29 (job_runs): clean up FK
        # dependents first because the schema doesn't declare ON DELETE
        # CASCADE. Document chunks reference the agent via a plain string
        # column (no FK), but we drop them too to keep the RAG store tidy.
        await s.execute(delete(DocumentChunk).where(DocumentChunk.agent_id == agent_id))
        await s.execute(delete(Document).where(Document.agent_id == agent_id))
        await s.delete(a)
