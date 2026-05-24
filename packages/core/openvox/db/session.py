"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from openvox.config import get_settings

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


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
    from pathlib import Path

    from sqlalchemy import inspect

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
