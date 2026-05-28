"""Skill hot-reload — async watcher around the local skills folder.

What this gives you:
    Drop a `*.py` file under `~/.openvox/skills/` (or wherever
    `OPENVOX_SKILLS_DIR` points), edit it in place, and the next
    VoiceSession picks up the new code without a core restart.

Why it's safe:
    - We only ever *register* new classes — we never evict old ones,
      because in-flight `VoiceSession` objects may still hold their
      bound class instances via Python's GC.
    - We *do* clear the registry's instance cache so the next `get(sid)`
      constructs a fresh object. That means hot-reload doesn't affect
      already-running sessions but does take effect on the next turn
      that builds a new SkillRunner.

Cost:
    - One coroutine per process, parked on `awatch()` (the watchfiles
      async API). The coroutine itself is idle 99.9% of the time.
    - Reload triggers debounce naturally because `awatch` batches
      events on a configurable interval (we use 500 ms).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from openvox.skills.registry import get_skill_registry

logger = logging.getLogger(__name__)


_watcher_task: asyncio.Task | None = None


async def _watch_loop(path: Path) -> None:
    """Run forever, reloading the skill registry whenever the folder changes.

    Lazy-import `watchfiles` so a missing wheel only warns rather than
    breaking core startup. (Same pattern we use for silero-vad.)
    """
    try:
        from watchfiles import Change, awatch
    except Exception as e:
        logger.warning("watchfiles not installed — skill hot-reload disabled: %s", e)
        return

    logger.info("skill hot-reload watching %s", path)
    reg = get_skill_registry()

    # We only care about *.py here. Filter at the watchfiles layer so
    # a noisy IDE writing swap files doesn't trigger a reload storm.
    def _is_py(_change: Change, p: str) -> bool:
        return p.endswith(".py") and "__pycache__" not in p

    try:
        async for changes in awatch(str(path), watch_filter=_is_py):
            # Log the trigger so users see "hot-reload fired" feedback.
            shorthand = ", ".join(sorted({Path(p).name for _c, p in changes})) or "(no files)"
            logger.info("skill hot-reload: %s changed — re-scanning %s", shorthand, path)
            try:
                added = reg.reload_local()
            except Exception:
                logger.exception("skill hot-reload failed; keeping previous registry")
                continue
            if added:
                logger.info("skill hot-reload: registered new skill ids %s", added)
            else:
                logger.info("skill hot-reload: registry refreshed (no new ids)")
    except asyncio.CancelledError:
        logger.info("skill hot-reload watcher cancelled")
        raise


async def start_watcher() -> None:
    """Spin up the watcher task. Idempotent — safe on re-init."""
    global _watcher_task
    if _watcher_task is not None and not _watcher_task.done():
        return
    reg = get_skill_registry()
    path = reg.local_skills_dir()
    if path is None:
        logger.info("skill hot-reload disabled — no local skills dir configured")
        return
    # Create the directory on first run so the watcher has something to
    # attach to (otherwise `awatch` raises FileNotFoundError immediately).
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("could not create skills dir %s: %s", path, e)
        return
    _watcher_task = asyncio.create_task(_watch_loop(path))


async def stop_watcher() -> None:
    """Cancel the watcher task. Called from FastAPI's lifespan teardown."""
    global _watcher_task
    if _watcher_task is None:
        return
    _watcher_task.cancel()
    try:
        await _watcher_task
    except (asyncio.CancelledError, Exception):
        pass
    _watcher_task = None
