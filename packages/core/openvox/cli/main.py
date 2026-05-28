"""Typer app + command registration.

Keeps the per-command implementations in `commands/` submodules so each
command stays small and unit-testable. This module just wires them up.
"""
from __future__ import annotations

import typer

from openvox.cli.commands import (
    info,
    logs,
    migrate,
    restart,
    run,
    start,
    status,
    stop,
    version,
)

app = typer.Typer(
    name="openvox",
    help=(
        "OpenVox — the open-source platform for building production voice agents. "
        "Run `openvox run` for foreground operation or `openvox start` for an "
        "always-on background daemon. See `openvox <command> --help` for per-"
        "command options."
    ),
    no_args_is_help=True,
    add_completion=False,  # don't litter the user's shell with completion files
)

# Foreground + introspection commands (Phase 1 PR-2).
app.command("version", help="Print the installed OpenVox version.")(version.version_cmd)
app.command("info", help="Show resolved configuration + service health.")(info.info_cmd)
app.command(
    "run",
    help="Start the FastAPI server in the foreground and open the dashboard.",
)(run.run_cmd)

# Daemon lifecycle commands (Phase 4 PR-1). Delegate to the per-OS
# backend in openvox.cli.daemon — see that package for launchd /
# systemd / Windows Service implementations.
app.command(
    "start",
    help="Install + start OpenVox as a background daemon (survives terminal close).",
)(start.start_cmd)
app.command("stop", help="Stop the background daemon (leaves it installed).")(stop.stop_cmd)
app.command("status", help="Show daemon state.")(status.status_cmd)
app.command("restart", help="Restart the daemon.")(restart.restart_cmd)
app.command("logs", help="Tail the daemon log file (use -f to follow).")(logs.logs_cmd)

# Schema migrations (Session 17 Phase 3). Wraps Alembic so operators
# don't have to learn the alembic CLI separately. Daemon startup runs
# `upgrade head` automatically; this command is for inspection +
# manual control.
app.command(
    "migrate",
    help="Run a schema migration command (default: upgrade head).",
)(migrate.migrate_cmd)
