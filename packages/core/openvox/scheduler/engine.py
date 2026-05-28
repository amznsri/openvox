"""Scheduler lifecycle + job registration.

We hold a single `AsyncIOScheduler` for the process. CRUD on
`ScheduledJob` rows in the DB go through `register_or_update()` /
`unregister()` so the running scheduler stays in sync.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from openvox.db import db_session
from openvox.db.models import ScheduledJob
from openvox.scheduler.runner import execute_job

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


async def start_scheduler() -> None:
    """Start the scheduler and load all enabled jobs from the DB."""
    sched = get_scheduler()
    if sched.running:
        return
    sched.start()
    async with db_session() as s:
        rows = (
            await s.execute(select(ScheduledJob).where(ScheduledJob.enabled.is_(True)))
        ).scalars().all()
    for job in rows:
        try:
            register_or_update(job)
        except Exception as e:
            logger.warning("could not schedule job %s on startup: %s", job.id, e)
    logger.info("scheduler started with %d active jobs", len(rows))


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def _build_trigger(job: ScheduledJob):
    if job.trigger_type == "cron":
        # Standard 5-field cron: "min hour day-of-month month day-of-week"
        return CronTrigger.from_crontab(job.trigger_expr, timezone=job.timezone or "UTC")
    if job.trigger_type == "interval":
        # Expr is "<seconds>" or "<value><unit>" e.g. "30s", "5m", "1h", "1d".
        seconds = _parse_interval(job.trigger_expr)
        return IntervalTrigger(seconds=seconds, timezone=job.timezone or "UTC")
    if job.trigger_type == "once":
        # Expr is an ISO datetime e.g. "2026-05-12T20:00:00".
        return DateTrigger(run_date=datetime.fromisoformat(job.trigger_expr), timezone=job.timezone or "UTC")
    if job.trigger_type == "webhook":
        # Webhook jobs fire only on explicit POST to
        # /api/v1/jobs/webhook/{token}. Returning None signals
        # `register_or_update` to skip APScheduler entirely — there's
        # no time-based schedule to register.
        return None
    raise ValueError(f"unknown trigger_type: {job.trigger_type}")


def _parse_interval(expr: str) -> int:
    e = expr.strip().lower()
    if not e:
        raise ValueError("interval expr is empty")
    if e.isdigit():
        return int(e)
    unit = e[-1]
    value = int(e[:-1])
    return {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * value


def register_or_update(job: ScheduledJob) -> None:
    """Add or replace an APScheduler job. Safe to call repeatedly.

    Webhook-trigger jobs aren't registered with APScheduler at all —
    they only fire on an explicit `POST /api/v1/jobs/webhook/{token}`.
    We still clear any old APScheduler binding so converting an
    existing cron job into a webhook job doesn't leave the old
    schedule firing.
    """
    sched = get_scheduler()
    trigger = _build_trigger(job)
    if trigger is None:
        # Webhook (or any future externally-triggered) kind. Unregister
        # any stale time-based binding and bail out.
        try:
            sched.remove_job(job.id)
        except Exception:
            pass
        job.next_run_at = None
        return
    sched.add_job(
        execute_job,
        trigger=trigger,
        args=[job.id],
        id=job.id,
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    aps_job = sched.get_job(job.id)
    job.next_run_at = aps_job.next_run_time if aps_job else None


def unregister(job_id: str) -> None:
    sched = get_scheduler()
    try:
        sched.remove_job(job_id)
    except Exception:
        # Already gone or never scheduled.
        pass


def trigger_now(job_id: str) -> None:
    """Run a job immediately, regardless of its trigger schedule."""
    sched = get_scheduler()
    sched.add_job(
        execute_job,
        args=[job_id],
        id=f"{job_id}-manual-{int(datetime.utcnow().timestamp() * 1000)}",
        max_instances=1,
        misfire_grace_time=60,
    )
