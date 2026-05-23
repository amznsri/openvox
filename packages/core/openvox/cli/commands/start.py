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
    from openvox.config import get_settings

    settings = get_settings()
    effective_port = port if port is not None else settings.core_port

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
