"""Eval framework HTTP routes.

API shape:
  Recordings:
    GET    /api/v1/evals/recordings
    GET    /api/v1/evals/recordings/{id}
    POST   /api/v1/evals/recordings/from-session    # promote a Session
    POST   /api/v1/evals/recordings                  # create from arbitrary transcript
    DELETE /api/v1/evals/recordings/{id}

  Personas:
    GET    /api/v1/evals/personas
    POST   /api/v1/evals/personas                    # create / upsert
    DELETE /api/v1/evals/personas/{id}

  Eval runs:
    POST   /api/v1/evals/run                          # kick off (replay | persona)
    GET    /api/v1/evals/runs                         # list (filter by agent_id)
    GET    /api/v1/evals/runs/{id}                    # single run with breakdown

The run endpoint executes synchronously for now (LLM round-trips are
~5–30 s — short enough that we don't need a job queue). If a future
user has multi-minute personas we'll move execution into the existing
scheduler kind.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from openvox.api.routes.agents import _to_out as _agent_to_out
from openvox.db import db_session
from openvox.db.models import (
    Agent,
    EvalRun,
    Persona,
    Recording,
    Session as DBSession,
    Transcript,
)
from openvox.eval.runner import run_persona_eval, run_replay_eval

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Serialisers ────────────────────────────────────────────────────


def _rec_out(r: Recording) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "source_session_id": r.source_session_id,
        "source_agent_id": r.source_agent_id,
        "transcript": r.transcript or [],
        "audio_url": r.audio_url,
        "tags": r.tags or [],
        "notes": r.notes,
        "turn_count": sum(1 for t in (r.transcript or []) if t.get("role") == "user"),
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }


def _persona_out(p: Persona) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "system_prompt": p.system_prompt,
        "tags": p.tags or [],
        "llm_provider": p.llm_provider,
        "llm_model": p.llm_model,
        "voice_id": p.voice_id,
        "builtin": p.builtin,
        "created_at": p.created_at.isoformat() if p.created_at else "",
    }


def _run_out(r: EvalRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "recording_id": r.recording_id,
        "persona_id": r.persona_id,
        "criteria": r.criteria or [],
        "transcript": r.transcript or [],
        "verdict": r.verdict,
        "score": r.score,
        "judge_breakdown": r.judge_breakdown or [],
        "error": r.error,
        "turn_count": r.turn_count,
        "duration_ms": r.duration_ms,
        "started_at": r.started_at.isoformat() if r.started_at else "",
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
    }


# ── Recordings ──────────────────────────────────────────────────────


class CreateRecordingFromSessionRequest(BaseModel):
    session_id: str
    name: str = ""
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


@router.post("/recordings/from-session", status_code=201)
async def create_from_session(body: CreateRecordingFromSessionRequest) -> dict[str, Any]:
    """Promote an existing Session's transcripts into a reusable Recording.

    The Session row stays untouched — we just copy its transcripts into
    a fresh Recording row that survives even after the source session
    is deleted.
    """
    async with db_session() as s:
        sess = await s.get(DBSession, body.session_id)
        if sess is None:
            raise HTTPException(404, "session not found")
        rows = (
            await s.execute(
                select(Transcript)
                .where(Transcript.session_id == body.session_id)
                .order_by(Transcript.created_at.asc())
            )
        ).scalars().all()
        transcript = [
            {"role": t.role, "text": t.text, "skill_id": t.skill_id}
            for t in rows
            if t.text
        ]
        rec = Recording(
            name=body.name or f"Replay of {body.session_id[:8]}",
            source_session_id=body.session_id,
            source_agent_id=sess.agent_id,
            transcript=transcript,
            audio_url=sess.audio_url,
            tags=body.tags,
            notes=body.notes,
        )
        s.add(rec)
        await s.flush()
        return _rec_out(rec)


class CreateRecordingRequest(BaseModel):
    name: str
    transcript: list[dict[str, Any]]
    tags: list[str] = Field(default_factory=list)
    notes: str = ""


@router.post("/recordings", status_code=201)
async def create_recording(body: CreateRecordingRequest) -> dict[str, Any]:
    """Create a Recording from an arbitrary transcript — useful when
    you want to script a deterministic test case rather than capturing
    a live session."""
    async with db_session() as s:
        rec = Recording(
            name=body.name, transcript=body.transcript,
            tags=body.tags, notes=body.notes,
        )
        s.add(rec)
        await s.flush()
        return _rec_out(rec)


@router.get("/recordings")
async def list_recordings() -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (
            await s.execute(select(Recording).order_by(Recording.created_at.desc()))
        ).scalars().all()
        return [_rec_out(r) for r in rows]


@router.get("/recordings/{rid}")
async def get_recording(rid: str) -> dict[str, Any]:
    async with db_session() as s:
        r = await s.get(Recording, rid)
        if r is None:
            raise HTTPException(404, "recording not found")
        return _rec_out(r)


@router.delete("/recordings/{rid}", status_code=204)
async def delete_recording(rid: str) -> None:
    async with db_session() as s:
        r = await s.get(Recording, rid)
        if r is None:
            raise HTTPException(404, "recording not found")
        await s.delete(r)


# ── Personas ────────────────────────────────────────────────────────


class PersonaIn(BaseModel):
    name: str
    description: str = ""
    system_prompt: str
    tags: list[str] = Field(default_factory=list)
    llm_provider: str = "byteplus"
    llm_model: str = ""
    voice_id: str = ""


@router.get("/personas")
async def list_personas() -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (
            await s.execute(select(Persona).order_by(Persona.created_at.desc()))
        ).scalars().all()
        return [_persona_out(p) for p in rows]


@router.post("/personas", status_code=201)
async def create_persona(body: PersonaIn) -> dict[str, Any]:
    async with db_session() as s:
        p = Persona(**body.model_dump())
        s.add(p)
        await s.flush()
        return _persona_out(p)


@router.delete("/personas/{pid}", status_code=204)
async def delete_persona(pid: str) -> None:
    async with db_session() as s:
        p = await s.get(Persona, pid)
        if p is None:
            raise HTTPException(404, "persona not found")
        if p.builtin:
            raise HTTPException(400, "built-in personas can't be deleted")
        await s.delete(p)


# ── Eval runs ──────────────────────────────────────────────────────


class RunEvalRequest(BaseModel):
    agent_id: str
    # Provide exactly one of these:
    recording_id: str = ""
    persona_id: str = ""
    criteria: list[str] = Field(default_factory=list)
    max_turns: int = 8


@router.post("/run", status_code=201)
async def run_eval(body: RunEvalRequest) -> dict[str, Any]:
    """Kick off an eval — replay (recording_id) or persona (persona_id).

    Runs synchronously and returns the full EvalRun row. CI hooks
    consume the verdict + score to decide pass/fail.
    """
    if bool(body.recording_id) == bool(body.persona_id):
        raise HTTPException(400, "exactly one of recording_id or persona_id is required")

    async with db_session() as s:
        a = await s.get(Agent, body.agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        agent_dict = _agent_to_out(a)

        if body.recording_id:
            rec = await s.get(Recording, body.recording_id)
            if rec is None:
                raise HTTPException(404, "recording not found")
            transcript = list(rec.transcript or [])
            persona_id = ""
            result = await run_replay_eval(
                agent=agent_dict,
                recording_transcript=transcript,
                criteria=body.criteria,
            )
        else:
            p = await s.get(Persona, body.persona_id)
            if p is None:
                raise HTTPException(404, "persona not found")
            persona_dict = _persona_out(p)
            persona_id = p.id
            result = await run_persona_eval(
                agent=agent_dict,
                persona=persona_dict,
                criteria=body.criteria,
                max_turns=body.max_turns,
            )

        run = EvalRun(
            agent_id=body.agent_id,
            recording_id=body.recording_id,
            persona_id=persona_id,
            criteria=body.criteria,
            transcript=result["transcript"],
            verdict=result["verdict"],
            score=result["score"],
            judge_breakdown=result["judge_breakdown"],
            error=result["error"],
            turn_count=result["turn_count"],
            duration_ms=result["duration_ms"],
            started_at=result["started_at"],
            ended_at=result["ended_at"],
        )
        s.add(run)
        await s.flush()
        return _run_out(run)


@router.get("/runs")
async def list_runs(agent_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    async with db_session() as s:
        q = select(EvalRun).order_by(EvalRun.started_at.desc()).limit(limit)
        if agent_id:
            q = q.where(EvalRun.agent_id == agent_id)
        rows = (await s.execute(q)).scalars().all()
        return [_run_out(r) for r in rows]


@router.get("/runs/{rid}")
async def get_run(rid: str) -> dict[str, Any]:
    async with db_session() as s:
        r = await s.get(EvalRun, rid)
        if r is None:
            raise HTTPException(404, "eval run not found")
        return _run_out(r)
