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

    # Snapshot state BEFORE stopping so we can report accurately. The old
    # code echoed status() AFTER stop(), which always read back
    # "not registered — run `openvox start`" — because stopping unloads
    # the job from the service manager. That made a SUCCESSFUL stop look
    # like a failure ("did it even work? is it gone?"). Decide the
    # message up front instead.
    before = backend.status()
    if before.state == "unknown":
        # Service manager itself is unavailable (e.g. wrong OS) — can't act.
        typer.secho(f"openvox — {before.detail}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    if not backend.is_installed():
        typer.echo("openvox — not installed; nothing to stop.")
        typer.echo("  Install + start with: openvox start")
        return

    backend.stop()

    if before.state == "running":
        pid_note = f" (was PID {before.pid})" if before.pid else ""
        typer.secho(f"openvox — stopped{pid_note}.", fg=typer.colors.GREEN)
    else:
        typer.echo("openvox — already stopped.")
    typer.echo("  Start again with: openvox start")
