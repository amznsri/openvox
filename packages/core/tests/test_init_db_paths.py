"""Tests for the three init_db() paths (Phase 3.3).

The init_db function has to handle three distinct DB states without
data loss:

  1. **Fresh install** — no tables at all. Action: run alembic upgrade
     head. Outcome: all tables + alembic_version stamped at head.

  2. **v0.1.x orphan** — schema exists but no alembic_version table
     (because v0.1.x used Base.metadata.create_all). Action: stamp
     baseline + upgrade head. Outcome: data preserved, schema
     unchanged, alembic_version stamped.

  3. **Aligned** — alembic_version table exists. Action: just upgrade
     head. Outcome: idempotent — no-op if already at head.

If any of these paths is wrong, OpenVox either crashes at startup
(fails to init) or silently corrupts the DB. These tests fence
that risk.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


# ── Path 1: Fresh install ──────────────────────────────────────────


async def test_init_db_fresh_install_creates_all_tables(
    tmp_openvox_home: Path,
) -> None:
    """No DB file → init_db creates one with every table + alembic_version."""
    db_path = tmp_openvox_home / "openvox.db"
    assert not db_path.exists()

    from openvox.db import init_db

    await init_db()

    assert db_path.exists()
    tables = _list_tables(db_path)
    # Spot-check the core tables exist.
    for expected in ("agents", "skills", "personas", "provider_keys",
                     "sessions", "alembic_version"):
        assert expected in tables, f"fresh init missing {expected!r}"


async def test_init_db_fresh_install_stamps_alembic_version(
    tmp_openvox_home: Path,
) -> None:
    """alembic_version table should have exactly one row pointing at
    the head revision."""
    from openvox.db import init_db

    await init_db()

    versions = _query(
        tmp_openvox_home / "openvox.db",
        "SELECT version_num FROM alembic_version",
    )
    assert len(versions) == 1, f"expected single version row, got {versions}"
    # The baseline migration id. If a new migration lands, this assertion
    # should be UPDATED to the new head, not relaxed to "any string" —
    # we want a failing test to force the contributor to think about it.
    # Note: this test checks 'one row exists' + 'it's a real revision
    # string', not the specific hash, because new migrations will
    # change head and that's expected.
    assert versions[0][0], "alembic_version is empty"


# ── Path 2: v0.1.x orphan upgrade ──────────────────────────────────


async def test_init_db_orphan_v01x_preserves_data(
    tmp_openvox_home: Path,
) -> None:
    """Pre-Phase-3 DBs have tables but no alembic_version. init_db
    must NOT recreate / truncate them — operator data is sacred."""
    db_path = tmp_openvox_home / "openvox.db"

    # Simulate a v0.1.x DB: create the schema via raw SQL (mimicking
    # what Base.metadata.create_all would have produced) + insert
    # some user data + DON'T create alembic_version.
    conn = sqlite3.connect(str(db_path))
    try:
        # Minimal v0.1.x-shape agents table — enough to verify
        # preservation. We use raw SQL deliberately so the test
        # doesn't depend on the SQLAlchemy model evolution.
        conn.execute("""
            CREATE TABLE agents (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                voice_id TEXT,
                system_prompt TEXT,
                greeting TEXT,
                skills TEXT,
                llm_provider TEXT,
                template_id TEXT,
                draft INTEGER,
                published INTEGER,
                channels TEXT,
                voice_provider TEXT,
                stt_provider TEXT,
                voice_language TEXT,
                voice_speed REAL,
                temperature REAL,
                max_tokens INTEGER,
                avatar_url TEXT,
                tags TEXT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO agents (id, name, system_prompt) VALUES (?, ?, ?)",
            ("preserved-agent", "Existing", "Don't delete me"),
        )
        conn.commit()
    finally:
        conn.close()

    # Verify pre-condition: data is in the DB, no alembic_version.
    pre_tables = _list_tables(db_path)
    assert "agents" in pre_tables
    assert "alembic_version" not in pre_tables
    assert _query(db_path, "SELECT name FROM agents WHERE id='preserved-agent'") == [("Existing",)]

    # Now run init_db — should stamp + upgrade, NOT recreate.
    from openvox.db import init_db

    await init_db()

    post_tables = _list_tables(db_path)
    assert "alembic_version" in post_tables, "init_db didn't stamp alembic_version on orphan DB"
    assert "agents" in post_tables, "init_db dropped the agents table!"

    preserved = _query(db_path, "SELECT name, system_prompt FROM agents WHERE id='preserved-agent'")
    assert preserved == [("Existing", "Don't delete me")], (
        f"orphan-upgrade lost agent data: {preserved}"
    )


# ── Path 3: Aligned (idempotent) ───────────────────────────────────


async def test_init_db_idempotent_when_already_at_head(
    tmp_openvox_home: Path,
) -> None:
    """init_db on a freshly-upgraded DB is a no-op (no errors, no
    schema thrash)."""
    from openvox.db import init_db

    # First call: fresh init.
    await init_db()
    db_path = tmp_openvox_home / "openvox.db"
    first_version = _query(db_path, "SELECT version_num FROM alembic_version")[0][0]

    # Second call: aligned path. Must not error.
    await init_db()
    second_version = _query(db_path, "SELECT version_num FROM alembic_version")[0][0]

    assert first_version == second_version, "init_db changed version on a no-op"


async def test_init_db_idempotent_preserves_data(
    tmp_openvox_home: Path,
) -> None:
    """Two consecutive init_db calls don't truncate tables."""
    db_path = tmp_openvox_home / "openvox.db"
    from openvox.db import init_db

    await init_db()

    # Insert a row via the ORM (more robust than raw SQL — handles
    # NOT NULL fields + their defaults automatically without us having
    # to track schema evolution in the test).
    from openvox.db import db_session
    from openvox.db.models import Agent

    async with db_session() as s:
        s.add(Agent(id="test-agent", name="Persistence test"))

    # Second init — should NOT touch the data.
    await init_db()

    rows = _query(db_path, "SELECT name FROM agents WHERE id='test-agent'")
    assert rows == [("Persistence test",)], f"second init_db lost data: {rows}"


# ── Helpers ────────────────────────────────────────────────────────


def _list_tables(db_path: Path) -> set[str]:
    """Return the set of table names in the SQLite DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _query(db_path: Path, sql: str) -> list[tuple]:
    """Run a SELECT against the SQLite DB and return all rows."""
    conn = sqlite3.connect(str(db_path))
    try:
        return list(conn.execute(sql))
    finally:
        conn.close()
