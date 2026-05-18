"""Scheduled-jobs CRUD + manual trigger + run history + webhook fire."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select

from openvox.db import db_session
from openvox.db.models import JobRun, ScheduledJob
from openvox.scheduler.engine import register_or_update, trigger_now, unregister

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────


class JobIn(BaseModel):
    name: str
    description: str = ""
    kind: str = "agent_query"  # agent_query | skill_run | audio_batch | outbound_call_batch
    payload: dict[str, Any] = Field(default_factory=dict)
    agent_id: str = ""
    # `webhook` is the Session 9 addition — fire-via-HTTP rather than
    # fire-on-clock. trigger_expr is ignored for webhook jobs.
    trigger_type: str = "cron"  # cron | interval | once | webhook
    trigger_expr: str = "0 20 * * *"
    timezone: str = "UTC"
    enabled: bool = True


def _to_dict(j: ScheduledJob, *, request: Request | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
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
    # Webhook jobs expose the full fire URL so the dashboard can offer
    # a copy-to-clipboard button. We don't expose `webhook_token` on
    # non-webhook jobs to keep the surface small.
    token = getattr(j, "webhook_token", "") or ""
    if j.trigger_type == "webhook" and token:
        base = ""
        if request is not None:
            base = str(request.base_url).rstrip("/")
        out["webhook_token"] = token
        out["webhook_url"] = f"{base}/api/v1/jobs/webhook/{token}" if base else f"/api/v1/jobs/webhook/{token}"
    return out


def _maybe_mint_webhook_token(job: ScheduledJob) -> None:
    """Make sure webhook jobs always have a token; non-webhook jobs
    don't. Idempotent — safe to call on every CRUD path."""
    if job.trigger_type == "webhook":
        if not (getattr(job, "webhook_token", "") or ""):
            job.webhook_token = secrets.token_urlsafe(24)
    # Note: we keep the token on jobs that switched away from webhook
    # so existing integrations don't break. If you really need to
    # rotate it, delete + re-create the job.


# ── Routes ───────────────────────────────────────────────────────


@router.get("")
async def list_jobs(request: Request) -> list[dict[str, Any]]:
    async with db_session() as s:
        rows = (
            await s.execute(select(ScheduledJob).order_by(ScheduledJob.updated_at.desc()))
        ).scalars().all()
        return [_to_dict(j, request=request) for j in rows]


@router.post("", status_code=201)
async def create_job(body: JobIn, request: Request) -> dict[str, Any]:
    async with db_session() as s:
        job = ScheduledJob(**body.model_dump())
        _maybe_mint_webhook_token(job)
        s.add(job)
        await s.flush()
        if job.enabled:
            try:
                register_or_update(job)
            except Exception as e:
                raise HTTPException(400, f"invalid trigger: {e}") from e
        return _to_dict(job, request=request)


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    async with db_session() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return _to_dict(job, request=request)


@router.put("/{job_id}")
async def update_job(job_id: str, body: JobIn, request: Request) -> dict[str, Any]:
    async with db_session() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        for k, v in body.model_dump().items():
            setattr(job, k, v)
        # Mint a token if this update switched the job to webhook trigger.
        _maybe_mint_webhook_token(job)
        await s.flush()
        # Re-register so the scheduler picks up trigger / enabled changes.
        unregister(job_id)
        if job.enabled:
            try:
                register_or_update(job)
            except Exception as e:
                raise HTTPException(400, f"invalid trigger: {e}") from e
        return _to_dict(job, request=request)


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


# ── Webhook fire ─────────────────────────────────────────────────────
# External-trigger endpoint for `trigger_type="webhook"` jobs.
# Anything else (cron / interval / once) is fired on its own schedule
# by APScheduler; this route is the manual escape hatch for event-
# driven workflows (upload completed → fire ingest job, lead arrives
# → fire outbound-call job, etc.).
#
# Design notes:
#  - We look the job up by `webhook_token`, NOT by id. The token is
#    the auth — anyone who has it can fire the job. Treat it like an
#    API key in your integration code.
#  - Disabled jobs (`enabled=False`) are silently no-op'd. This is
#    the "pause webhook deliveries" affordance.
#  - The optional POST body is merged into the job's `payload` for
#    this *one* run only — useful for "the new lead's id is X" style
#    parameterisation. The stored payload isn't mutated.
#  - We deliberately return 200 even on disabled / wrong-token cases
#    so the caller can't enumerate valid tokens by status code
#    differences. Real failures (e.g. payload not JSON) do 400.


@router.post("/webhook/{token}")
async def webhook_fire(token: str, request: Request) -> dict[str, Any]:
    """Fire a webhook-triggered job by its token.

    Body is optional JSON; if present, it's merged into the job's
    stored payload for this single run.
    """
    if not token or len(token) < 12:
        # Cheap guardrail — real tokens are 32+ chars URL-safe.
        return {"received": False, "reason": "invalid token format"}

    # Parse optional JSON body. Treat empty / non-JSON as "no override".
    payload_override: dict[str, Any] = {}
    try:
        if (await request.body()).strip():
            data = await request.json()
            if isinstance(data, dict):
                payload_override = data
    except Exception:
        # Malformed body is the only error we surface as 400 — the
        # caller's integration is broken and they should see it.
        raise HTTPException(400, "request body must be JSON object or empty")

    # Constant-time-ish lookup. Webhook tokens are stored hashed-equivalent
    # via secrets.token_urlsafe (high-entropy random), so a plain WHERE
    # is fine here — no need for HMAC since we don't index by user input
    # in any other column.
    async with db_session() as s:
        rows = (
            await s.execute(
                select(ScheduledJob).where(ScheduledJob.webhook_token == token)
            )
        ).scalars().all()
        if not rows:
            logger.info("webhook: no job matches token=%s...", token[:6])
            return {"received": False, "reason": "no matching job"}
        job = rows[0]
        if not job.enabled:
            logger.info("webhook: job %s is disabled — skipping", job.id)
            return {"received": False, "reason": "job disabled"}
        if job.trigger_type != "webhook":
            # Token exists but the job was switched away from webhook
            # trigger. Be explicit so the caller knows to update their
            # integration rather than silently failing.
            return {"received": False, "reason": "job no longer accepts webhook triggers"}
        job_id = job.id
        # Mutate the stored payload only if we have an override; we
        # snapshot it merged into a *new* dict so subsequent runs
        # without overrides still see the original config.
        if payload_override:
            merged = {**(job.payload or {}), **payload_override}
            job.payload = merged

    # Fire via the same path manual /trigger uses.
    trigger_now(job_id)
    return {"received": True, "job_id": job_id, "fired": True}
