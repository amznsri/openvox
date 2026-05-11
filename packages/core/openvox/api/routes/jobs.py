"""Scheduled-jobs CRUD + manual trigger + run history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select

from openvox.db import db_session
from openvox.db.models import JobRun, ScheduledJob
from openvox.scheduler.engine import register_or_update, trigger_now, unregister

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────


class JobIn(BaseModel):
    name: str
    description: str = ""
    kind: str = "agent_query"  # agent_query | skill_run | audio_batch
    payload: dict[str, Any] = Field(default_factory=dict)
    agent_id: str = ""
    trigger_type: str = "cron"  # cron | interval | once
    trigger_expr: str = "0 20 * * *"
    timezone: str = "UTC"
    enabled: bool = True


def _to_dict(j: ScheduledJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "name": j.name,
        "description": j.description,
        "kind": j.kind,
        "payload": j.payload or {},
        "agent_id": j.agent_id,
        "trigger_type": j.trigger_type,
        "trigger_expr": j.trigger_expr,
        "timezone": j.timezone,
        "enabled": j.enabled,
        "last_run_at": j.last_run_at.isoformat() if j.last_run_at else None,
        "next_run_at": j.next_run_at.isoformat() if j.next_run_at else None,
        "last_status": j.last_status,
        "last_error": j.last_error,
        "created_at": j.created_at.isoformat() if j.created_at else "",
        "updated_at": j.updated_at.isoformat() if j.updated_at else "",
    }


# ── Routes ───────────────────────────────────────────────────────


@router.get("")
async def list_jobs() -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (
            await s.execute(select(ScheduledJob).order_by(ScheduledJob.updated_at.desc()))
        ).scalars().all()
        return [_to_dict(j) for j in rows]


@router.post("", status_code=201)
async def create_job(body: JobIn) -> dict[str, Any]:
    async with db_session() as s:
        job = ScheduledJob(**body.model_dump())
        s.add(job)
        await s.flush()
        if job.enabled:
            try:
                register_or_update(job)
            except Exception as e:
                raise HTTPException(400, f"invalid trigger: {e}") from e
        return _to_dict(job)


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    async with db_session() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return _to_dict(job)


@router.put("/{job_id}")
async def update_job(job_id: str, body: JobIn) -> dict[str, Any]:
    async with db_session() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        for k, v in body.model_dump().items():
            setattr(job, k, v)
        await s.flush()
        # Re-register so the scheduler picks up trigger / enabled changes.
        unregister(job_id)
        if job.enabled:
            try:
                register_or_update(job)
            except Exception as e:
                raise HTTPException(400, f"invalid trigger: {e}") from e
        return _to_dict(job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(job_id: str) -> None:
    async with db_session() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        # Drop history first — the FK from job_runs blocks the parent delete.
        await s.execute(delete(JobRun).where(JobRun.job_id == job_id))
        await s.delete(job)
    unregister(job_id)


@router.post("/{job_id}/trigger")
async def trigger_job(job_id: str) -> dict[str, Any]:
    async with db_session() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
    trigger_now(job_id)
    return {"queued": True}


@router.get("/{job_id}/runs")
async def job_runs(job_id: str, limit: int = 20) -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (
            await s.execute(
                select(JobRun)
                .where(JobRun.job_id == job_id)
                .order_by(desc(JobRun.started_at))
                .limit(limit)
            )
        ).scalars().all()
        return [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else "",
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
                "status": r.status,
                "result": r.result or {},
                "error": r.error,
            }
            for r in rows
        ]
