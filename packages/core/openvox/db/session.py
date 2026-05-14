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
    """Create all tables, then run lightweight additive migrations.

    `create_all` only handles new tables — not new columns on existing
    ones. For a local-first app that doesn't yet ship Alembic, the
    pragmatic pattern is to ADD COLUMN IF NOT EXISTS for any column we've
    added since the last release. List them in `_ADDITIVE_COLUMNS` below.
    """
    # Import models so they register with the metadata.
    from openvox.db import models  # noqa: F401
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Additive column migrations. (table, column, type-string-as-DDL)
    # Postgres + SQLite both support `ADD COLUMN IF NOT EXISTS` in recent
    # versions; for older SQLite we fall back to a try/except.
    additive: list[tuple[str, str, str]] = [
        ("agents", "mcp_servers", "JSON DEFAULT '[]'"),
        ("agents", "voice_map", "JSON DEFAULT '{}'"),
        # Session 8: Silero VAD per agent. `silero` enables server-side
        # voice activity detection for sub-100ms interrupt latency;
        # `none` falls back to client-driven interrupt.
        ("agents", "vad_provider", "VARCHAR(50) DEFAULT 'silero'"),
        # Session 8: pricing telemetry on per-call rows so the cost
        # calculator can show $/min breakdown. Best-effort — never
        # crash a session because we couldn't bump a counter.
        ("sessions", "llm_tokens_in", "INTEGER DEFAULT 0"),
        ("sessions", "llm_tokens_out", "INTEGER DEFAULT 0"),
        ("sessions", "tts_chars", "INTEGER DEFAULT 0"),
    ]
    async with engine.begin() as conn:
        for table, column, ddl in additive:
            try:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
                )
            except Exception:
                # Older SQLite without IF NOT EXISTS support.
                try:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                except Exception:
                    pass  # Already exists or DB doesn't support — safe to skip.


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
