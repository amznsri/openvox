"""`openvox restart` — stop + start the daemon.

Backends with a native restart verb (systemd has) override
`DaemonBackend.restart()` so the transition is atomic; the launchd
and Windows backends fall back to the base-class stop+start.
"""
from __future__ import annotations

import typer

from openvox.cli.daemon import get_backend


def restart_cmd() -> None:
    """Restart the OpenVox daemon (preserves install state)."""
    backend = get_backend()
    backend.restart()
    typer.echo(f"openvox restarted: {backend.status().detail}")
