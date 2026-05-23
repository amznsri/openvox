"""Typer app + command registration.

Keeps the per-command implementations in `commands/` submodules so each
command stays small and unit-testable. This module just wires them up.
"""
from __future__ import annotations

import typer

from openvox.cli.commands import info, run, version

app = typer.Typer(
    name="openvox",
    help=(
        "OpenVox — the open-source platform for building production voice agents. "
        "Run `openvox run` to start the server and open the dashboard in your "
        "browser. See `openvox <command> --help` for per-command options."
    ),
    no_args_is_help=True,
    add_completion=False,  # don't litter the user's shell with completion files
)

# Register subcommands. Each module declares a single function and we wire
# it here so the user-facing command name is consistent + greppable.
app.command("version", help="Print the installed OpenVox version.")(version.version_cmd)
app.command("info", help="Show resolved configuration + service health.")(info.info_cmd)
app.command("run", help="Start the FastAPI server in the foreground and open the dashboard.")(run.run_cmd)
