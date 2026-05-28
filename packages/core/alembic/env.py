"""Alembic env — wires the migration runner to OpenVox's settings + models.

Key differences from the stock async template:

  * `sqlalchemy.url` is loaded from `openvox.config.get_settings()` at
    runtime, not from alembic.ini. This is so the SAME alembic config
    works whether you're running migrations against a CLI-mode SQLite,
    a Docker-mode Postgres, or a test tempdir DB — all selected via the
    DATABASE_URL env var.

  * `target_metadata` is bound to `openvox.db.session.Base.metadata` so
    `alembic revision --autogenerate` sees every model declared in
    `openvox/db/models.py`.

  * `compare_type=True` + `compare_server_default=True` make
    autogenerate notice column type changes + default-value changes
    that the default config silently ignores. Worth the extra noise
    because Phase 1's `_ADDITIVE_COLUMNS` shim hid bugs in exactly
    these dimensions.

Run manually:
    cd packages/core
    alembic current                  # which migration is the DB at?
    alembic history --verbose         # full migration timeline
    alembic upgrade head              # apply pending migrations
    alembic revision --autogenerate -m "describe change"
                                      # generate a migration from
                                      # current models vs current DB
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import this BEFORE referencing target_metadata so all models are
# registered with Base.metadata. Adding a new model file? Make sure
# it's imported (directly or via openvox.db.models) before this line.
from openvox.config import get_settings
from openvox.db import models  # noqa: F401 — side-effect: registers models
from openvox.db.session import Base

config = context.config

# Honour alembic.ini's [loggers] section if present. We deliberately
# don't override the root logger config because openvox.cli.commands.run
# already configures it; double-config can produce duplicate log lines.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is what `--autogenerate` reflects against. Every Column /
# UniqueConstraint / Index / ForeignKey declared on a model class
# inherits onto this metadata object via DeclarativeBase machinery.
target_metadata = Base.metadata


def _resolved_db_url() -> str:
    """Return the DB URL alembic should connect to.

    Env-var precedence (highest first):
      1. DATABASE_URL via openvox.config.get_settings — what the
         daemon also uses, so migrations land on the same DB.
      2. The literal `sqlalchemy.url` in alembic.ini — fallback if
         settings can't be loaded (e.g. running alembic outside the
         openvox process tree).
    """
    settings = get_settings()
    if settings.database_url:
        return settings.database_url
    return config.get_main_option("sqlalchemy.url") or ""


def run_migrations_offline() -> None:
    """Generate SQL scripts without connecting to a DB.

    Used for `alembic upgrade head --sql` to dump the migration as
    a SQL file an ops team can run separately. Not the primary path
    for OpenVox (we always upgrade online), but kept for parity with
    the stock template.
    """
    url = _resolved_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Sync callback that alembic invokes inside an async connection.

    `compare_type=True` makes autogenerate notice when a column's
    type changes (e.g. VARCHAR(50) → VARCHAR(100)). Without this,
    such changes silently slip past `alembic revision --autogenerate`.

    `compare_server_default=True` does the same for server-side
    DEFAULT clauses — the dimension Phase 1's `_ADDITIVE_COLUMNS`
    shim was working around.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """The online-mode entry point — creates an async engine,
    delegates to `do_run_migrations` via `run_sync`."""
    cfg = config.get_section(config.config_ini_section, {})
    # Override the alembic.ini URL with the live settings URL so
    # migrations always land where the daemon expects.
    cfg["sqlalchemy.url"] = _resolved_db_url()

    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
