"""`openvox migrate` — manual Alembic wrapper for operators.

`openvox start` and `openvox run` invoke ``init_db()`` automatically,
which runs ``alembic upgrade head`` as part of lifespan startup. This
command exists for operator workflows that need to inspect / control
migrations explicitly:

  * After a release bump, run ``openvox migrate upgrade`` BEFORE
    restarting the daemon to surface migration failures cleanly
    (otherwise the daemon won't even reach lifespan-ready).

  * ``openvox migrate current`` reports which migration the DB is at —
    useful when investigating "did the upgrade actually run?".

  * ``openvox migrate history`` shows the full migration timeline so
    you can see what changed between two versions.

  * ``openvox migrate sql --from <rev> --to <rev>`` emits SQL for an
    air-gapped review before applying (e.g. by DBAs in regulated
    environments).

Implementation: this just shells out to alembic with the same
``alembic.ini`` and DB env vars the daemon uses. Keeps the alembic
behaviour single-sourced and means new alembic features become
available here for free.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer


def _find_alembic_ini() -> Path:
    """Locate alembic.ini in either layout (wheel-installed or source)."""
    here = Path(__file__).resolve()
    # __file__ is openvox/cli/commands/migrate.py.
    # In a source checkout: ../../../alembic.ini = packages/core/alembic.ini
    # In a wheel install:   ../../alembic.ini    = alongside the openvox/ package
    candidates = [
        here.parent.parent.parent.parent / "alembic.ini",
        here.parent.parent.parent / "alembic.ini",
        here.parent.parent / "alembic.ini",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        f"alembic.ini not found in any of: {[str(c) for c in candidates]}"
    )


def _run_alembic(args: list[str]) -> int:
    """Run `alembic <args>` as a subprocess; return its exit code.

    Subprocess inherits env (DATABASE_URL etc.) so it hits the same DB
    the daemon uses.
    """
    ini = _find_alembic_ini()
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
    )
    return proc.returncode


# typer expects each subcommand to be a top-level function. We register
# them via Typer's command pattern in main.py rather than nesting a
# sub-app — flatter command tree reads better at `--help` time.


def migrate_cmd(
    action: str = typer.Argument(
        "upgrade",
        help=(
            "What to do. Common values: upgrade, current, history, "
            "stamp. Anything alembic supports — passed straight through."
        ),
    ),
    target: str = typer.Argument(
        "head",
        help=(
            "Target revision for upgrade/stamp/downgrade. Defaults to "
            "'head' (the latest). Ignored by current/history."
        ),
    ),
) -> None:
    """Run an Alembic migration command against the configured DB.

    Examples:

        openvox migrate                       # alembic upgrade head
        openvox migrate current               # show current revision
        openvox migrate history               # full migration timeline
        openvox migrate upgrade 0001          # upgrade to a specific rev
        openvox migrate stamp head            # mark DB as at head, no SQL
    """
    # Some actions take a target argument (upgrade, downgrade, stamp);
    # others don't (current, history, heads, branches). Detect to
    # avoid passing 'head' to commands that don't accept it.
    no_target_actions = {"current", "history", "heads", "branches", "show"}
    if action in no_target_actions:
        argv = [action]
    else:
        argv = [action, target]

    rc = _run_alembic(argv)
    if rc != 0:
        typer.echo(
            f"openvox migrate: alembic {' '.join(argv)} failed "
            f"(exit code {rc})",
            err=True,
        )
        raise typer.Exit(rc)
