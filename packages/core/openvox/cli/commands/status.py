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
    # Surface the actual dashboard URL — the port may differ from the
    # default 8000 if it was occupied when the daemon started (see
    # portutil). Reading the persisted port means `openvox status`
    # always tells the user the right URL to open, even after a
    # restart that auto-switched ports.
    from openvox.cli.portutil import load_persisted_port

    port = load_persisted_port()
    if port is not None:
        typer.echo(f"  dashboard: http://localhost:{port}/dashboard")
    typer.echo(f"  logs: {backend.log_path}")
