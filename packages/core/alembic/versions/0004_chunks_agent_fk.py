"""D9-v2: promote document_chunks.agent_id from soft string to hard FK

D9 (migration 0003, v0.2.12) wired up SQLite ``PRAGMA foreign_keys =
ON`` and added ``ON DELETE CASCADE`` to the five FKs that were
already declared in ``models.py``. Four columns stayed as plain
indexed strings (no FK constraint):

  - ``eval_runs.agent_id``
  - ``scheduled_jobs.agent_id``
  - ``recordings.source_agent_id``
  - ``document_chunks.agent_id``

This migration promotes ONLY ``document_chunks.agent_id`` —
the other three are blocked on a UX decision (cascade vs SET
NULL audit-trail semantics) that needs broader review across
dashboard + SDK + scheduler/runner code. ``document_chunks`` is
the simple case: every chunk already cascades on
``document_id`` → ``documents.id`` → ``agents.id`` (transitive
through migration 0003), so the new direct FK is belt-and-braces
that defends against any future code path that inserts a chunk
without a corresponding Document.

Same SQLite "create new, copy rows, swap" dance as 0003. The
helper functions are duplicated here (rather than imported from
0003) on purpose — Alembic best-practice is for each migration
to be self-contained so old revisions don't break if helper
modules move.

Revision ID: d9_v2_chunks_agent_fk
Revises: d9_fk_cascade_v1
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "d9_v2_chunks_agent_fk"
down_revision: Union[str, Sequence[str], None] = "d9_fk_cascade_v1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _column_defs(bind, table: str) -> tuple[list[str], list[str]]:
    """Read the live schema for ``table`` via ``PRAGMA table_info``.

    Returns ``(column_ddl, column_names)`` — see migration 0003 for
    the rationale (table-rebuild needs every column reproduced
    faithfully, including any additive columns picked up from the
    v0.1.x → v0.2.x history).
    """
    info = bind.execute(text(f"PRAGMA table_info({table})")).all()
    if not info:
        raise RuntimeError(f"table {table!r} doesn't exist — migration corrupt?")

    column_ddl: list[str] = []
    pk_cols: list[tuple[int, str]] = []
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
            pk_cols.append((pk, name))

    pk_cols_sorted = [n for _, n in sorted(pk_cols, key=lambda t: t[0])]
    if pk_cols_sorted:
        column_ddl.append(
            "PRIMARY KEY (" + ", ".join(_quote(c) for c in pk_cols_sorted) + ")"
        )

    return column_ddl, column_names


def _existing_fks(bind, table: str) -> list[str]:
    """Read existing FK constraints via ``PRAGMA foreign_key_list``
    and return them as DDL fragments so the table-rebuild preserves
    them. The 0003 migration installed ``document_id`` → ``documents``
    CASCADE on this table; that FK MUST survive the rebuild.

    PRAGMA columns: (id, seq, table, from, to, on_update, on_delete,
                     match)
    """
    rows = bind.execute(text(f"PRAGMA foreign_key_list({table})")).all()
    # Group by id (composite FKs share an id across rows). For our
    # tables every FK is single-column, but the loop handles N-col
    # cases too.
    by_id: dict[int, list[tuple]] = {}
    for r in rows:
        by_id.setdefault(r[0], []).append(r)

    out: list[str] = []
    for _, group in sorted(by_id.items()):
        cols = [g[3] for g in group]   # `from` column
        refs = [g[4] for g in group]   # `to` column
        ref_table = group[0][2]
        on_delete = group[0][6] or "NO ACTION"
        clause = (
            f"FOREIGN KEY ({', '.join(_quote(c) for c in cols)}) "
            f"REFERENCES {_quote(ref_table)} "
            f"({', '.join(_quote(c) for c in refs)}) "
            f"ON DELETE {on_delete}"
        )
        out.append(clause)
    return out


def _table_exists(bind, table: str) -> bool:
    row = bind.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": table},
    ).first()
    return row is not None


def _recreate(bind, table: str, extra_fks: list[str]) -> None:
    """Rebuild ``table`` preserving its current columns + existing
    FKs, plus appending ``extra_fks`` (the new FKs we're adding).
    """
    column_ddl, column_names = _column_defs(bind, table)
    existing = _existing_fks(bind, table)

    new_table = f"{table}_new"
    col_list = ", ".join(_quote(c) for c in column_names)
    all_constraints = column_ddl + existing + extra_fks

    op.execute("PRAGMA foreign_keys = OFF")
    op.execute(f"DROP TABLE IF EXISTS {_quote(new_table)}")
    op.execute(f"CREATE TABLE {_quote(new_table)} ({', '.join(all_constraints)})")
    op.execute(
        f"INSERT INTO {_quote(new_table)} ({col_list}) "
        f"SELECT {col_list} FROM {_quote(table)}"
    )
    op.execute(f"DROP TABLE {_quote(table)}")
    op.execute(f"ALTER TABLE {_quote(new_table)} RENAME TO {_quote(table)}")
    # Rebuild any non-PK indexes that the column-defs path doesn't
    # capture. document_chunks has an index on agent_id (declared
    # in models.py via `index=True`); recreate it on the new table.
    op.execute(
        f'CREATE INDEX IF NOT EXISTS "ix_{table}_agent_id" '
        f'ON {_quote(table)} ("agent_id")'
    )
    op.execute("PRAGMA foreign_keys = ON")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        # Postgres / MySQL: ALTER TABLE ADD CONSTRAINT. Not the path
        # OpenVox ships today.
        return
    if not _table_exists(bind, "document_chunks"):
        # Orphan-v0.1.x path: table created later by the baseline
        # migration with the post-D9-v2 schema directly from models.py.
        return
    _recreate(
        bind,
        "document_chunks",
        extra_fks=[
            'FOREIGN KEY ("agent_id") REFERENCES "agents" ("id") ON DELETE CASCADE'
        ],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    if not _table_exists(bind, "document_chunks"):
        return
    # Rebuild without the new FK — _existing_fks() picks up the
    # ones from 0003 (document_id CASCADE) AND the new one we just
    # added. To remove the new one, we have to manually filter it
    # out of the existing list before rebuild.
    bind = op.get_bind()
    column_ddl, column_names = _column_defs(bind, "document_chunks")
    existing = [
        fk for fk in _existing_fks(bind, "document_chunks")
        if "agents" not in fk        # drop the agent_id FK; keep document_id FK
    ]
    new_table = "document_chunks_new"
    col_list = ", ".join(_quote(c) for c in column_names)
    all_constraints = column_ddl + existing
    op.execute("PRAGMA foreign_keys = OFF")
    op.execute(f"DROP TABLE IF EXISTS {_quote(new_table)}")
    op.execute(f"CREATE TABLE {_quote(new_table)} ({', '.join(all_constraints)})")
    op.execute(
        f"INSERT INTO {_quote(new_table)} ({col_list}) "
        f"SELECT {col_list} FROM document_chunks"
    )
    op.execute("DROP TABLE document_chunks")
    op.execute(f"ALTER TABLE {_quote(new_table)} RENAME TO document_chunks")
    op.execute('CREATE INDEX IF NOT EXISTS "ix_document_chunks_agent_id" ON document_chunks ("agent_id")')
    op.execute("PRAGMA foreign_keys = ON")
