"""Session CRUD + transcript reads."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from openvox.db import db_session
from openvox.db.models import Session, Transcript

router = APIRouter()


def _session_to_dict(s: Session) -> dict[str, Any]:
    return {
        "id": s.id,
        "agent_id": s.agent_id,
        "channel": s.channel,
        "caller_id": s.caller_id,
        "duration_ms": s.duration_ms,
        "turn_count": s.turn_count,
        "cost_usd": s.cost_usd,
        "first_token_ms": s.first_token_ms,
        "avg_response_ms": s.avg_response_ms,
        # Per-session telemetry counters used by the pricing calculator.
        # Surfaced here so the dashboard list view can show them inline
        # without hitting /api/v1/pricing/sessions/{id} for every row.
        "llm_tokens_in": getattr(s, "llm_tokens_in", 0) or 0,
        "llm_tokens_out": getattr(s, "llm_tokens_out", 0) or 0,
        "tts_chars": getattr(s, "tts_chars", 0) or 0,
        "status": s.status,
        "audio_url": s.audio_url,
        "transcript_url": s.transcript_url,
        "started_at": s.started_at.isoformat() if s.started_at else "",
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
    }


@router.get("")
async def list_sessions(agent_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    async with db_session() as s:
        q = select(Session).order_by(Session.started_at.desc()).limit(limit)
        if agent_id:
            q = q.where(Session.agent_id == agent_id)
        rows = (await s.execute(q)).scalars().all()
        return [_session_to_dict(r) for r in rows]


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    async with db_session() as s:
        sess = await s.get(Session, session_id)
        if sess is None:
            raise HTTPException(404, "session not found")
        return _session_to_dict(sess)


@router.get("/{session_id}/transcripts")
async def get_transcripts(session_id: str) -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (
            await s.execute(
                select(Transcript)
                .where(Transcript.session_id == session_id)
                .order_by(Transcript.created_at.asc())
            )
        ).scalars().all()
        return [
            {
                "id": t.id,
                "role": t.role,
                "text": t.text,
                "audio_url": t.audio_url,
                "started_ms": t.started_ms,
                "ended_ms": t.ended_ms,
                "skill_id": t.skill_id,
                "skill_args": t.skill_args,
                "skill_result": t.skill_result,
                "sentiment": t.sentiment,
                "created_at": t.created_at.isoformat() if t.created_at else "",
            }
            for t in rows
        ]
