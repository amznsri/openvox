"""`openvox start` — install + start the background daemon.

First run: registers OpenVox with the OS-native service manager
(launchd / systemd --user / Windows Service via nssm), then starts it.
Subsequent runs: re-install (idempotent — picks up any flag changes
like a different --port), then start.

Daemon mode complements `openvox run` (foreground). Use `run` for
ad-hoc tinkering with logs in the terminal; use `start` for always-on
operation across terminal closes and reboots.
"""
from __future__ import annotations

import typer

from openvox.cli.daemon import get_backend


def start_cmd(
    port: int = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to bind. Defaults to CORE_PORT env / 8000.",
    ),
    host: str = typer.Option(
        "0.0.0.0",
        "--host",
        "-h",
        help="Host to bind. 127.0.0.1 = local only; 0.0.0.0 = LAN-accessible.",
    ),
) -> None:
    """Install + start OpenVox as a background daemon."""
    from openvox.cli.portutil import resolve_port, save_runtime

    # Resolve a FREE port before baking it into the service definition.
    # Precedence: --port → persisted (so a stop/start cycle keeps the
    # same URL) → settings.core_port. Auto-switch + warn if the
    # preferred port is occupied — otherwise the daemon's uvicorn
    # would fail to bind, the service manager would still report
    # "running", and the user would hit whatever else owns :8000.
    effective_port, preferred_port = resolve_port(port, host=host, use_persisted=True)
    if effective_port != preferred_port:
        typer.secho(
            f"  ⚠  port {preferred_port} is in use — using {effective_port} instead.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    # Persist BEFORE install so the baked-in --port and the runtime
    # record agree, and a later `openvox run` lands on the same port.
    save_runtime(effective_port, host)

    backend = get_backend()
    backend_name = type(backend).__name__
    typer.echo(f"openvox — installing daemon via {backend_name}…")
    try:
        backend.install(port=effective_port, host=host)
        backend.start()
    except FileNotFoundError as e:
        # Most commonly: nssm.exe missing on Windows, or the OS-native
        # service manager (launchctl/systemctl) isn't on PATH. Both are
        # actionable so we re-raise with context.
        typer.echo(f"  error: {e}", err=True)
        raise typer.Exit(1) from e
    except Exception as e:
        typer.echo(f"  error: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(1) from e

    status = backend.status()
    typer.echo(f"  status:    {status.detail}")
    typer.echo(f"  dashboard: http://localhost:{effective_port}/dashboard")
    typer.echo(f"  logs:      {backend.log_path}")
    typer.echo("")
    typer.echo("  Tail logs:  openvox logs -f")
    typer.echo("  Stop:       openvox stop")
