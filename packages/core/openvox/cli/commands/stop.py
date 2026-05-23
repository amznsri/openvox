"""`openvox stop` — stop the background daemon.

Leaves the service registration in place so `openvox start` can re-
enable later without re-installing. To remove entirely, use the
uninstall flow (currently: hand-delete the plist / unit file; a CLI
verb for this lands when there's user demand — Phase 4 keeps surface
area small).
"""
from __future__ import annotations

import typer

from openvox.cli.daemon import get_backend


def stop_cmd() -> None:
    """Stop the running OpenVox daemon."""
    backend = get_backend()
    backend.stop()
    typer.echo(f"openvox — {backend.status().detail}")
