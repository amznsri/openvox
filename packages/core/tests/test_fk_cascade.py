"""DB-level FK cascade behavior (D9 — v0.2.12).

Before D9, SQLite ran with foreign_keys=OFF (the default), so every
``ForeignKey()`` declaration in ``models.py`` was decorative — no
enforcement, no cascade. The agent-delete route did all the work
manually (CLAUDE.md §8 #29, #30, #53).

D9:
  1. Sets ``PRAGMA foreign_keys = ON`` on every new SQLite connection
     (``db/session.py:_wire_sqlite_foreign_keys``).
  2. Adds ``ondelete="CASCADE"`` to the 5 hard FKs in models.py.
  3. Alembic migration 0003 recreates tables with the new constraints.

These tests pin both pieces down:
  - The PRAGMA is actually set on every connection from the pool.
  - Deleting a parent row (agent, session, document, scheduled_job)
    cascades to children automatically.

The in-route manual cascade in ``api/routes/agents.py:delete_agent``
stays for now as defensive belt-and-braces — but if it ever gets
deleted, THESE tests are what catches the regression.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select


# ── PRAGMA ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sqlite_foreign_keys_pragma_is_on(isolated_db):
    """Every new connection from the pool must have FKs enabled."""
    from openvox.db import db_session
    from sqlalchemy import text

    # `db_session()` is the same context manager every route uses,
    # so we exercise the actual production pool — not a side-channel.
    async with db_session() as s:
        result = await s.execute(text("PRAGMA foreign_keys"))
        row = result.one()
    assert row[0] == 1, (
        f"foreign_keys PRAGMA was {row[0]!r}, expected 1 — D9's "
        "event listener didn't fire or didn't apply to this connection."
    )


# ── Cascade behaviour ────────────────────────────────────────────


async def _seed_agent(name: str, agent_id: str) -> None:
    """Insert one agent row with the minimum required columns."""
    from openvox.db import db_session
    from openvox.db.models import Agent

    async with db_session() as s:
        s.add(
            Agent(
                id=agent_id,
                name=name,
                description="",
                system_prompt="",
                greeting="",
                stt_provider="byteplus",
                tts_provider="byteplus",
                llm_provider="byteplus",
                llm_model="",
                voice_id="",
                voice_speed=1.0,
                voice_language="en-US",
                temperature=0.3,
                max_tokens=400,
                skills=[],
                channels={},
                mcp_servers=[],
                voice_map={},
                status="draft",
            )
        )


@pytest.mark.asyncio
async def test_deleting_agent_cascades_to_sessions(isolated_db):
    """DELETE FROM agents → sessions rows for that agent disappear."""
    from openvox.db import db_session
    from openvox.db.models import Agent, Session as DBSession

    await _seed_agent("Test agent", "agent-1")
    async with db_session() as s:
        s.add(DBSession(
            agent_id="agent-1",
            channel="web",
            caller_id="test",
            started_at=datetime.now(timezone.utc),
            status="active",
        ))

    # Confirm the session row exists.
    async with db_session() as s:
        rows = (await s.execute(select(DBSession).where(DBSession.agent_id == "agent-1"))).scalars().all()
        assert len(rows) == 1

    # Delete the agent via raw ORM (NOT the manual-cascade route).
    async with db_session() as s:
        a = await s.get(Agent, "agent-1")
        await s.delete(a)

    # Session row should be GONE — cascade fired.
    async with db_session() as s:
        rows = (await s.execute(select(DBSession).where(DBSession.agent_id == "agent-1"))).scalars().all()
        assert rows == [], (
            "DELETE FROM agents did not cascade to sessions — D9's "
            "ondelete=CASCADE isn't in effect (PRAGMA off? migration "
            "not applied? batch_alter_table failure?)."
        )


@pytest.mark.asyncio
async def test_deleting_session_cascades_to_transcripts(isolated_db):
    """DELETE FROM sessions → transcript rows disappear."""
    from openvox.db import db_session
    from openvox.db.models import Session as DBSession, Transcript

    await _seed_agent("Test agent", "agent-2")
    async with db_session() as s:
        sess = DBSession(
            agent_id="agent-2",
            channel="web",
            caller_id="test",
            started_at=datetime.now(timezone.utc),
            status="active",
        )
        s.add(sess)
        await s.flush()
        sess_id = sess.id
        s.add(Transcript(session_id=sess_id, role="user", text="hello"))
        s.add(Transcript(session_id=sess_id, role="assistant", text="hi"))

    async with db_session() as s:
        rows = (await s.execute(select(Transcript).where(Transcript.session_id == sess_id))).scalars().all()
        assert len(rows) == 2

    # Delete the session — transcripts must follow.
    async with db_session() as s:
        sess = await s.get(DBSession, sess_id)
        await s.delete(sess)

    async with db_session() as s:
        rows = (await s.execute(select(Transcript).where(Transcript.session_id == sess_id))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_deleting_agent_transitively_kills_transcripts(isolated_db):
    """Two-hop cascade: agent → sessions → transcripts."""
    from openvox.db import db_session
    from openvox.db.models import Agent, Session as DBSession, Transcript

    await _seed_agent("Test agent", "agent-3")
    async with db_session() as s:
        sess = DBSession(
            agent_id="agent-3",
            channel="web",
            caller_id="test",
            started_at=datetime.now(timezone.utc),
            status="active",
        )
        s.add(sess)
        await s.flush()
        s.add(Transcript(session_id=sess.id, role="user", text="abc"))

    async with db_session() as s:
        a = await s.get(Agent, "agent-3")
        await s.delete(a)

    # Both rows gone — agent → session → transcript chain cascaded.
    async with db_session() as s:
        sessions_count = len((await s.execute(
            select(DBSession).where(DBSession.agent_id == "agent-3")
        )).scalars().all())
        transcripts_count = len((await s.execute(
            select(Transcript)  # any transcript at all
        )).scalars().all())
    assert sessions_count == 0
    assert transcripts_count == 0


@pytest.mark.asyncio
async def test_deleting_document_cascades_to_chunks(isolated_db):
    """DELETE FROM documents → document_chunks rows disappear."""
    from openvox.db import db_session
    from openvox.db.models import Document, DocumentChunk

    await _seed_agent("Test agent", "agent-4")
    async with db_session() as s:
        doc = Document(
            agent_id="agent-4",
            name="test.pdf",
            mime_type="application/pdf",
            size_bytes=100,
            page_count=1,
        )
        s.add(doc)
        await s.flush()
        doc_id = doc.id
        # DocumentChunk's actual columns: id, document_id, agent_id, text,
        # page (= chunk index, not page number), embedding. Only document_id
        # + text are strictly required.
        s.add(DocumentChunk(
            document_id=doc_id,
            agent_id="agent-4",
            text="chunk text",
        ))

    async with db_session() as s:
        rows = (await s.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_id))).scalars().all()
        assert len(rows) == 1

    async with db_session() as s:
        doc = await s.get(Document, doc_id)
        await s.delete(doc)

    async with db_session() as s:
        rows = (await s.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_id))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_deleting_scheduled_job_cascades_to_job_runs(isolated_db):
    """DELETE FROM scheduled_jobs → job_runs rows disappear.

    The /api/v1/jobs/{id} delete route currently runs a manual
    cascade for this (CLAUDE.md §8 #29). After D9 the DB enforces it
    too — manual cascade becomes belt-and-braces.
    """
    from openvox.db import db_session
    from openvox.db.models import JobRun, ScheduledJob

    async with db_session() as s:
        # ScheduledJob's actual columns: kind + name + trigger_type +
        # trigger_expr (the cron string for trigger_type=cron, the
        # interval expression for type=interval, etc.). payload is
        # the kind-specific JSON.
        job = ScheduledJob(
            agent_id="",
            kind="agent_query",
            name="test",
            trigger_type="interval",
            trigger_expr="1h",
            payload={},
            enabled=False,
        )
        s.add(job)
        await s.flush()
        job_id = job.id
        s.add(JobRun(
            job_id=job_id,
            started_at=datetime.now(timezone.utc),
            status="success",
        ))

    async with db_session() as s:
        rows = (await s.execute(select(JobRun).where(JobRun.job_id == job_id))).scalars().all()
        assert len(rows) == 1

    async with db_session() as s:
        job = await s.get(ScheduledJob, job_id)
        await s.delete(job)

    async with db_session() as s:
        rows = (await s.execute(select(JobRun).where(JobRun.job_id == job_id))).scalars().all()
        assert rows == []
