"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from openvox.config import get_settings

logger = logging.getLogger(__name__)

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _ensure_data_dir() -> None:
    """Make sure ~/.openvox/ exists before anyone tries to open a DB
    file inside it. SQLite's `open()` fails with a confusing
    `unable to open database file` error if the parent dir is missing."""
    (Path.home() / ".openvox").mkdir(parents=True, exist_ok=True)


def _migrate_legacy_db_locations() -> None:
    """Detect + recover from the v0.2.5-and-earlier CWD-relative DB bug.

    The old default `database_url = sqlite+aiosqlite:///./.openvox/openvox.db`
    was CWD-relative, so:

      - Daemon (launchd WorkingDirectory=~/.openvox/) created
        `~/.openvox/.openvox/openvox.db` (nested!)
      - Foreground `openvox run` from `~/documents/` created
        `~/documents/.openvox/openvox.db`
      - Etc.

    Real users (rightly) thought their data had disappeared when they
    switched between daemon and foreground. v0.2.6+ uses an absolute
    path, but existing installs still have the nested file.

    This migration:
      1. Looks for `~/.openvox/.openvox/openvox.db` (the nested one).
      2. If present AND `~/.openvox/openvox.db` is missing or empty,
         moves the nested file to the canonical path.
      3. If both exist, picks the LARGER file (probably the one with
         real data) and renames the other with a `.legacy` suffix —
         never silently deletes user data.
      4. Removes the now-empty `~/.openvox/.openvox/` directory.

    Idempotent. Safe to run at every startup.
    """
    home_openvox = Path.home() / ".openvox"
    canonical = home_openvox / "openvox.db"
    nested = home_openvox / ".openvox" / "openvox.db"

    if not nested.exists():
        return  # nothing to migrate

    nested_size = nested.stat().st_size
    canonical_size = canonical.stat().st_size if canonical.exists() else 0

    if canonical_size == 0:
        # Canonical missing or empty placeholder — promote nested.
        try:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            if canonical.exists():
                canonical.unlink()  # remove 0-byte placeholder
            shutil.move(str(nested), str(canonical))
            logger.warning(
                "db: migrated legacy nested DB %s -> %s "
                "(see openvox v0.2.6 changelog for context)",
                nested,
                canonical,
            )
        except OSError as e:
            logger.warning(
                "db: legacy DB at %s found but auto-migration failed: %s. "
                "Move it manually to %s.",
                nested,
                e,
                canonical,
            )
            return
    elif nested_size > canonical_size:
        # Both exist — nested is larger. Preserve user data: rename
        # nested to canonical, move existing canonical aside.
        try:
            legacy_backup = canonical.with_suffix(".db.legacy")
            shutil.move(str(canonical), str(legacy_backup))
            shutil.move(str(nested), str(canonical))
            logger.warning(
                "db: found two DB files; promoted larger one. "
                "Backed up the other to %s.",
                legacy_backup,
            )
        except OSError as e:
            logger.warning(
                "db: two DBs found but auto-migration failed: %s", e
            )
            return
    else:
        # Canonical is the same or larger — assume it's the real one,
        # rename nested aside.
        try:
            shutil.move(str(nested), str(nested.with_suffix(".db.legacy")))
            logger.info(
                "db: nested legacy DB %s preserved as .legacy "
                "(canonical at %s already has more data)",
                nested,
                canonical,
            )
        except OSError as e:
            logger.warning(
                "db: nested legacy DB cleanup failed: %s", e
            )
            return

    # Try to remove the now-empty nested .openvox/ directory.
    try:
        nested.parent.rmdir()
    except OSError:
        # Directory not empty (other files might live there) — leave alone.
        pass


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            future=True,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


async def init_db() -> None:
    """Initialise the database to the current schema via Alembic.

    Three init paths, distinguished by what's already in the DB:

      1. **Fresh install** (no tables at all) —
         Run ``alembic upgrade head``. Creates every table from the
         latest migration. Same outcome as the old ``create_all`` path.

      2. **v0.1.x orphan** (tables exist, no ``alembic_version``) —
         Pre-Phase-3 DBs created via ``Base.metadata.create_all`` never
         had an alembic version stamped. Stamp them with the baseline
         revision (the schema they actually have), THEN ``upgrade head``
         to apply any subsequent migrations. No data loss; no manual
         intervention by the operator.

      3. **Aligned** (``alembic_version`` table present) —
         Just ``upgrade head``. No-op if already at head.

    The Phase 1 ``_ADDITIVE_COLUMNS`` shim is GONE — every column it
    added is in the baseline migration. Adding a new column from here
    on is ``alembic revision --autogenerate`` against the changed model,
    not editing a hand-maintained list in this function.

    Implementation note on the third path: we DON'T call
    ``alembic.command.upgrade`` from within an existing async context
    because alembic's runner spawns its own asyncio.run via env.py.
    Instead we invoke alembic as a subprocess. The cost is one extra
    process spawn at startup (~100ms); the benefit is zero risk of
    nested-event-loop bugs that would only show up on certain
    Python versions / certain SQLAlchemy backends.
    """
    import asyncio
    import sys

    from sqlalchemy import inspect

    # FIRST: make sure ~/.openvox/ exists, then recover any data
    # parked at the legacy nested path from the v0.2.5-and-earlier
    # CWD-relative-DB bug. Both must run BEFORE `get_engine()` is
    # called because the engine opens a file handle on whichever
    # DB exists at the canonical path.
    _ensure_data_dir()
    _migrate_legacy_db_locations()

    # Make sure models are registered before any schema introspection —
    # even though we no longer use Base.metadata for table creation,
    # callers that follow init_db() with .add()/.query() expect the
    # mapper to be configured.
    from openvox.db import models  # noqa: F401

    engine = get_engine()

    # Detect which of the three init paths to take. Use SQLAlchemy's
    # inspector instead of raw `SHOW TABLES` so the same code works on
    # SQLite + Postgres.
    async with engine.begin() as conn:
        existing_tables = await conn.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )

    if not existing_tables:
        # Path 1: fresh install
        path_label = "fresh"
    elif "alembic_version" not in existing_tables:
        # Path 2: v0.1.x orphan — has tables but no migration tracking
        path_label = "orphan-v0.1.x"
    else:
        # Path 3: already alembic-managed
        path_label = "aligned"

    # Find alembic.ini. In a wheel-installed openvox-core, it lives at
    # the same level as the openvox/ package directory. In a source
    # checkout, it lives at packages/core/alembic.ini. Search both
    # candidate locations.
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent / "alembic.ini",  # source checkout
        here.parent.parent / "alembic.ini",         # alongside pkg
    ]
    alembic_ini = next((p for p in candidates if p.exists()), None)
    if alembic_ini is None:
        raise FileNotFoundError(
            f"alembic.ini not found in any of: {[str(c) for c in candidates]}. "
            "OpenVox needs alembic.ini at startup to run schema migrations."
        )

    # Build the alembic subprocess command(s). For the orphan path,
    # `stamp` first then `upgrade head`. For others, just `upgrade head`.
    def _run_alembic(*args: str) -> None:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(alembic_ini), *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"alembic {' '.join(args)} failed (exit {result.returncode}):\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
            )

    # alembic subprocess inherits our env — including DATABASE_URL — so
    # it connects to the same DB the daemon will use.
    if path_label == "orphan-v0.1.x":
        # Stamp with the baseline so subsequent `upgrade head` doesn't
        # try to re-run baseline migrations against existing tables.
        # The baseline revision id is stable; if it ever changes, this
        # path needs the new id.
        await asyncio.to_thread(_run_alembic, "stamp", "f1911d0fafa9")

    await asyncio.to_thread(_run_alembic, "upgrade", "head")


@asynccontextmanager
async def db_session() -> AsyncIterator[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
