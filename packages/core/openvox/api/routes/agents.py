"""Agent CRUD."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from openvox.config import get_settings
from openvox.db import db_session
from openvox.db.models import (
    Agent,
    AgentStatus,
    Document,
    DocumentChunk,
    EvalRun,
    JobRun,
    Recording,
    ScheduledJob,
    Session as DBSession,
    Transcript,
)

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
    """Delete an agent + every row that references it.

    The agent table has two hard FK dependents (Session, Document) and
    several soft string-keyed dependents added across later sessions
    (DocumentChunk, ScheduledJob, JobRun, Recording, EvalRun). The
    hard FKs cause a `ForeignKeyViolationError` if children are not
    deleted first; the soft ones don't block delete but leave orphan
    rows that clutter the eval framework, RAG store, and scheduler.

    Pattern: cascade in dependency order, then `s.delete(a)`. Plain
    `s.delete(a)` with `relationship(cascade="all, delete-orphan")`
    has historically been unreliable in async-mode SQLAlchemy when
    the relationship isn't pre-loaded — see bugs #29, #30 in
    CLAUDE.md §8. In-route cascades are slower but bulletproof.
    """
    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")

        # 1. Eval framework — runs reference the agent directly,
        #    recordings via source_agent_id.
        await s.execute(delete(EvalRun).where(EvalRun.agent_id == agent_id))
        await s.execute(delete(Recording).where(Recording.source_agent_id == agent_id))

        # 2. Scheduler — kill job_runs first (FK to scheduled_jobs),
        #    then the jobs themselves. Same pattern as the
        #    /api/v1/jobs/{id} route uses (CLAUDE.md §8 #29).
        job_ids = (
            await s.execute(select(ScheduledJob.id).where(ScheduledJob.agent_id == agent_id))
        ).scalars().all()
        if job_ids:
            await s.execute(delete(JobRun).where(JobRun.job_id.in_(job_ids)))
        await s.execute(delete(ScheduledJob).where(ScheduledJob.agent_id == agent_id))

        # 3. Voice / text sessions — transcripts FK to sessions, so
        #    clear those first. Hard FK constraint.
        session_ids = (
            await s.execute(select(DBSession.id).where(DBSession.agent_id == agent_id))
        ).scalars().all()
        if session_ids:
            await s.execute(delete(Transcript).where(Transcript.session_id.in_(session_ids)))
        await s.execute(delete(DBSession).where(DBSession.agent_id == agent_id))

        # 4. Documents + RAG chunks. Hard FK on Document, soft on chunks.
        await s.execute(delete(DocumentChunk).where(DocumentChunk.agent_id == agent_id))
        await s.execute(delete(Document).where(Document.agent_id == agent_id))

        # 5. Finally the agent itself.
        await s.delete(a)


# ── Session 10: text-mode turn for the Setup Assistant ───────────────
# The Setup Assistant ships as a voice agent but the user-facing
# component supports both voice AND typed input. Voice goes through the
# existing /ws/voice WS; text comes in here. Both invoke the same agent
# + skills, both write to the same Agent.channels.setup_state, so the
# "draft" state stays consistent when the user switches mid-flow.
#
# Out of scope for v1: streaming. The skill-loop nature of an LLM round
# (LLM → maybe-tool-call → result → re-invoke LLM) is awkward to stream
# over a single HTTP response; the SetupAssistant doesn't need it
# because typed input is naturally turn-based. Reply payload includes
# every event the orchestrator would have emitted, in order.


class TurnRequest(BaseModel):
    user_text: str
    # Optional — lets the caller carry a conversational history across
    # turns. Each item is `{"role": "user"|"assistant", "content": "..."}`.
    # If omitted we treat this as a fresh turn (still uses the agent's
    # configured system_prompt + greeting).
    history: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/{agent_id}/turn")
async def agent_text_turn(agent_id: str, body: TurnRequest) -> dict[str, Any]:
    """Run a single LLM turn (with skill calls) against `agent_id`.

    Returns the assistant text plus an array of every event the
    orchestrator emitted (skill_call / skill_result / errors) so the
    SetupAssistant UI can render a faithful transcript even when the
    LLM took a multi-step tool path.
    """
    import json

    from openvox.providers import ProviderType, get_registry
    from openvox.providers.base import LLMConfig, LLMMessage, LLMProvider
    from openvox.skills import SkillContext
    from openvox.skills.runner import SkillRunner

    user_text = (body.user_text or "").strip()
    if not user_text:
        raise HTTPException(400, "user_text is required")

    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        system_prompt = a.system_prompt
        skill_ids = list(a.skills or [])
        llm_id = a.llm_provider
        llm_model = a.llm_model
        temperature = a.temperature
        max_tokens = a.max_tokens

    llm = get_registry().get(ProviderType.LLM, llm_id)
    if not isinstance(llm, LLMProvider) or not llm.is_available():
        raise HTTPException(400, f"LLM provider '{llm_id}' unavailable")

    runner = SkillRunner(
        skill_ids=skill_ids,
        ctx=SkillContext(agent_id=agent_id, metadata={"source": "agent_text_turn"}),
    )

    # Build a fresh message list — system prompt + caller-supplied
    # history + the new user turn. We're not persisting history here;
    # the SetupAssistant client carries it. Keeps this endpoint stateless
    # at the HTTP layer while still letting the LLM see the full thread.
    messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]
    for h in body.history:
        role = h.get("role")
        content = h.get("content") or ""
        if role in {"user", "assistant"} and content:
            messages.append(LLMMessage(role=role, content=content))
    messages.append(LLMMessage(role="user", content=user_text))

    cfg = LLMConfig(
        model=llm_model,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
        tools=runner.tool_specs() or None,
    )

    # ── Skill loop ───────────────────────────────────────────────────
    # Mirrors `orchestrator._llm_turn`'s shape but in non-streaming mode
    # because text replies don't need sub-sentence chunking. Same cap
    # on tool iterations (default 6) for the same reason —
    # see CLAUDE.md §8 #46.
    events: list[dict[str, Any]] = []
    full_text = ""
    max_iters = 6

    for iteration in range(max_iters):
        # Non-streaming call returns one chunk with the full delta
        # plus possibly tool_calls.
        last_chunk = None
        async for chunk in llm.chat_stream(messages, cfg):
            last_chunk = chunk
        if last_chunk is None:
            break
        delta = last_chunk.delta or ""
        full_text += delta
        if delta:
            events.append({"type": "assistant_token", "text": delta})

        tool_calls = last_chunk.tool_calls or []
        if not tool_calls:
            # LLM is done.
            break

        # Echo the assistant message that issued the tool_calls so the
        # next LLM call's history is well-formed (OpenAI / Ark contract).
        messages.append(
            LLMMessage(role="assistant", content=delta, tool_calls=tool_calls)
        )
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                parsed_args = {"_raw": raw_args}
            if not isinstance(parsed_args, dict):
                parsed_args = {"_value": parsed_args}
            events.append({"type": "skill_call", "name": name, "args": parsed_args})
            result = await runner.invoke(name, parsed_args)
            events.append({"type": "skill_result", "name": name, "output": result})
            messages.append(
                LLMMessage(
                    role="tool",
                    tool_call_id=tc.get("id") or "",
                    name=name,
                    content=json.dumps(result, ensure_ascii=False),
                )
            )
    else:
        events.append({
            "type": "error",
            "text": f"tool-call loop exceeded {max_iters} iterations",
        })

    events.append({"type": "assistant_done", "text": full_text})
    return {"text": full_text, "events": events}
