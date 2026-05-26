"""Add ON DELETE CASCADE to every hard FK (D9 — v0.2.12)

Pre-D9 the FK declarations in ``models.py`` were SQLite-decorative:
PRAGMA foreign_keys was never set on the engine, so even constraint
violations were silently ignored. The agent-delete route papered over
this with a manual cascade chain in pure Python.

D9 wires up the real thing:
  - ``db/session.py`` now issues ``PRAGMA foreign_keys = ON`` on every
    SQLite connection.
  - This migration adds ``ON DELETE CASCADE`` to the 5 existing hard
    FKs so a ``DELETE FROM agents WHERE id=X`` cleans up children
    automatically at the DB level.

Tables touched:

  sessions.agent_id           → CASCADE on agents.id
  transcripts.session_id      → CASCADE on sessions.id
  documents.agent_id          → CASCADE on agents.id
  document_chunks.document_id → CASCADE on documents.id
  job_runs.job_id             → CASCADE on scheduled_jobs.id

How:
    SQLite can't ``ALTER TABLE`` an FK constraint, so we do the
    classic "create new, copy rows, swap" dance. To keep the new
    schema faithful to whatever the live DB looks like (including
    any additive columns from ``_ADDITIVE_COLUMNS`` /
    ``Base.metadata.create_all`` quirks across the v0.1.x → v0.2.x
    history), the migration INTROSPECTS the existing table via
    ``PRAGMA table_info`` at runtime rather than hand-rolling
    CREATE TABLE statements. The only difference between old and
    new is the FK clause.

Soft FKs (eval_runs.agent_id, scheduled_jobs.agent_id,
recordings.source_agent_id, document_chunks.agent_id) stay as plain
String columns for now. Promoting them is a D9-v2 follow-up; the
manual cascade chain in agents.py:delete_agent still handles them
defensively.

Revision ID: d9_fk_cascade_v1
Revises: 888ff7ac624a
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "d9_fk_cascade_v1"
down_revision: Union[str, Sequence[str], None] = "888ff7ac624a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, fk_col, ref_table, ref_col) — applies ON DELETE CASCADE
_CASCADES: list[tuple[str, str, str, str]] = [
    ("sessions", "agent_id", "agents", "id"),
    ("transcripts", "session_id", "sessions", "id"),
    ("documents", "agent_id", "agents", "id"),
    ("document_chunks", "document_id", "documents", "id"),
    ("job_runs", "job_id", "scheduled_jobs", "id"),
]


def _quote(name: str) -> str:
    """Minimal SQLite identifier quoting — wrap in double quotes."""
    return '"' + name.replace('"', '""') + '"'


def _column_defs(bind, table: str) -> tuple[list[str], list[str]]:
    """Read the live schema for ``table`` via ``PRAGMA table_info``.

    Returns two parallel lists:
      - ``column_ddl``: the per-column DDL fragments
        (e.g. ``"id" VARCHAR(36) NOT NULL DEFAULT ''``)
      - ``column_names``: just the names, for the INSERT … SELECT …
        copy step.

    SQLite's PRAGMA table_info returns rows:
      (cid, name, type, notnull, dflt_value, pk)
    Composite PKs (rare in this schema) are handled via the cid order
    + the ``pk`` rank — for the 5 tables we care about, every PK is
    a single column.
    """
    info = bind.execute(text(f"PRAGMA table_info({table})")).all()
    if not info:
        raise RuntimeError(f"table {table!r} doesn't exist — migration corrupt?")

    column_ddl: list[str] = []
    pk_cols: list[str] = []
    column_names: list[str] = []
    for cid, name, ctype, notnull, dflt, pk in info:
        column_names.append(name)
        parts = [_quote(name), ctype or "TEXT"]
        if notnull:
            parts.append("NOT NULL")
        if dflt is not None:
            parts.append(f"DEFAULT {dflt}")
        column_ddl.append(" ".join(parts))
        if pk:
            pk_cols.append((pk, name))  # type: ignore[arg-type]

    # Sort by SQLite's pk ordinal (1, 2, ...) and emit a composite PK
    # only if there's actually one column flagged as PK.
    pk_cols_sorted = [n for _, n in sorted(pk_cols, key=lambda t: t[0])]
    if pk_cols_sorted:
        column_ddl.append(
            "PRIMARY KEY (" + ", ".join(_quote(c) for c in pk_cols_sorted) + ")"
        )

    return column_ddl, column_names


def _recreate_with_cascade_fk(
    bind, table: str, fk_col: str, ref_table: str, ref_col: str
) -> None:
    """Recreate ``table`` so the FK from ``fk_col`` to
    ``ref_table(ref_col)`` carries ``ON DELETE CASCADE``."""
    column_ddl, column_names = _column_defs(bind, table)

    # Append the FK constraint. We don't drop the old FK explicitly
    # because the recreate IS the drop — `<table>_new` starts from
    # zero and includes only the constraints we add here.
    column_ddl.append(
        f"FOREIGN KEY ({_quote(fk_col)}) REFERENCES "
        f"{_quote(ref_table)} ({_quote(ref_col)}) ON DELETE CASCADE"
    )

    new_table = f"{table}_new"
    col_list = ", ".join(_quote(c) for c in column_names)

    op.execute("PRAGMA foreign_keys = OFF")
    op.execute(f"DROP TABLE IF EXISTS {_quote(new_table)}")
    op.execute(f"CREATE TABLE {_quote(new_table)} ({', '.join(column_ddl)})")
    op.execute(
        f"INSERT INTO {_quote(new_table)} ({col_list}) "
        f"SELECT {col_list} FROM {_quote(table)}"
    )
    op.execute(f"DROP TABLE {_quote(table)}")
    op.execute(f"ALTER TABLE {_quote(new_table)} RENAME TO {_quote(table)}")
    op.execute("PRAGMA foreign_keys = ON")


def _recreate_without_cascade_fk(
    bind, table: str, fk_col: str, ref_table: str, ref_col: str
) -> None:
    """Inverse of ``_recreate_with_cascade_fk`` — recreate without
    the ``ON DELETE CASCADE`` clause. Used by ``downgrade``."""
    column_ddl, column_names = _column_defs(bind, table)
    column_ddl.append(
        f"FOREIGN KEY ({_quote(fk_col)}) REFERENCES "
        f"{_quote(ref_table)} ({_quote(ref_col)})"
    )
    new_table = f"{table}_new"
    col_list = ", ".join(_quote(c) for c in column_names)

    op.execute("PRAGMA foreign_keys = OFF")
    op.execute(f"DROP TABLE IF EXISTS {_quote(new_table)}")
    op.execute(f"CREATE TABLE {_quote(new_table)} ({', '.join(column_ddl)})")
    op.execute(
        f"INSERT INTO {_quote(new_table)} ({col_list}) "
        f"SELECT {col_list} FROM {_quote(table)}"
    )
    op.execute(f"DROP TABLE {_quote(table)}")
    op.execute(f"ALTER TABLE {_quote(new_table)} RENAME TO {_quote(table)}")
    op.execute("PRAGMA foreign_keys = ON")


def _table_exists(bind, table: str) -> bool:
    """Defensive check before recreate.

    The v0.1.x-orphan init path (``db/session.py``) stamps a partially-
    populated DB with the baseline revision and runs ``upgrade head``.
    If the orphan DB only has ``agents`` (the test fixture does
    exactly this — see ``test_init_db_orphan_v01x_preserves_data``),
    the dependent tables haven't been created yet — they'd be missing
    from the orphan and we'd hit "no such table" on the recreate.
    Skipping when absent lets the migration be a no-op for tables
    that don't exist; the baseline migration creates them with the
    correct (post-D9) schema directly from models.py anyway.
    """
    row = bind.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).first()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        # On Postgres / MySQL the right form is ALTER TABLE DROP
        # CONSTRAINT + ADD CONSTRAINT. OpenVox ships SQLite-first;
        # if/when a Postgres deployment surfaces this needs a
        # dialect branch.
        return
    for table, fk_col, ref_table, ref_col in _CASCADES:
        if not _table_exists(bind, table):
            # Orphan-v0.1.x path: table will be created later by the
            # baseline migration with the correct constraints (because
            # models.py declares them with ondelete="CASCADE" now).
            continue
        _recreate_with_cascade_fk(bind, table, fk_col, ref_table, ref_col)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for table, fk_col, ref_table, ref_col in _CASCADES:
        if not _table_exists(bind, table):
            continue
        _recreate_without_cascade_fk(bind, table, fk_col, ref_table, ref_col)
