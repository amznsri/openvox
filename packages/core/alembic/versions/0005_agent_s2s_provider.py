"""Add Agent.s2s_provider for Phase 3 PR-B

Speech-to-Speech (S2S) provider per agent. Empty string = pipeline
mode (STT → LLM → TTS); populated = use this S2S adapter (e.g.
``openai_realtime``) for single-WS-hop voice.

Backwards-compatible: empty default means every existing agent
keeps pipeline behaviour until the operator explicitly opts in
via the Agent edit page.

Why a top-level Agent column rather than a JSON field inside
``channels``: Voice mode is a first-class agent decision, not a
channel toggle (the same agent uses the same voice mode whether
called via web / WhatsApp / Telegram / Twilio). Keeping it as a
plain column means SQLAlchemy can index it later if a "which
agents use which S2S provider" query ever becomes hot.

Revision ID: pr_b_s2s_provider
Revises: d9_v2_chunks_agent_fk
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "pr_b_s2s_provider"
down_revision: Union[str, Sequence[str], None] = "d9_v2_chunks_agent_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """SQLite-aware "does this column already exist?".

    Useful because the v0.1.x orphan-DB path bootstraps from
    models.py via ``Base.metadata.create_all`` — those tables
    arrive with every model column already, so this migration is
    a no-op there. Hitting an "ALTER TABLE ADD COLUMN" on an
    already-present column would otherwise crash on a freshly-
    initialised SQLite file.
    """
    row = bind.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).first()
    if row is None:
        return False
    rows = bind.execute(text(f"PRAGMA table_info({table})")).all()
    return any(r[1] == column for r in rows)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite handles ADD COLUMN cleanly without a table rebuild —
        # only FK changes need the create-new/copy/swap dance from 0003.
        if not _column_exists(bind, "agents", "s2s_provider"):
            op.execute(
                "ALTER TABLE agents ADD COLUMN s2s_provider VARCHAR(50) "
                "NOT NULL DEFAULT ''"
            )
    else:
        # Postgres path. `server_default=''` so existing rows get the
        # canonical "pipeline mode" sentinel.
        op.execute(
            "ALTER TABLE agents ADD COLUMN IF NOT EXISTS s2s_provider "
            "VARCHAR(50) NOT NULL DEFAULT ''"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite DROP COLUMN is supported from 3.35+ (Apr 2021). Anyone
        # downgrading is on a recent version. Use IF EXISTS to keep
        # this idempotent against the orphan-DB path.
        if _column_exists(bind, "agents", "s2s_provider"):
            op.execute("ALTER TABLE agents DROP COLUMN s2s_provider")
    else:
        op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS s2s_provider")
