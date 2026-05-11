"""Task scheduling — cron / interval / once triggers.

OpenClaw has cron jobs as a first-class tool; this is our equivalent.

Why APScheduler + our own DB tables (not the SQLAlchemy jobstore):
  APScheduler's persistent jobstore needs a sync SQLAlchemy session, but
  our codebase is async-first. We instead keep the source-of-truth in our
  own `ScheduledJob` table and rebuild APScheduler's in-memory state on
  startup from that table. Every CRUD on a job also mutates the running
  scheduler. Single owner, no drift.
"""

from openvox.scheduler.engine import get_scheduler, start_scheduler, stop_scheduler

__all__ = ["get_scheduler", "start_scheduler", "stop_scheduler"]
