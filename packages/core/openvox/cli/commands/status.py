"""`openvox status` — show daemon state.

Reports running/stopped/unknown + PID if available + log path. The
"unknown" state means the backend couldn't determine the answer (e.g.
launchctl missing — usually because someone ran the CLI on the wrong
OS). Distinguished from "stopped" so the user knows the difference
between "the daemon is off" and "we don't know".
"""
from __future__ import annotations

import typer

from openvox.cli.daemon import get_backend


def status_cmd() -> None:
    """Show daemon state."""
    backend = get_backend()
    status = backend.status()
    typer.echo(f"openvox daemon: {status.state}")
    typer.echo(f"  {status.detail}")
    typer.echo(f"  logs: {backend.log_path}")
