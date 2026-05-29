"""`openvox run` — foreground server + auto-open browser.

This is the user-facing equivalent of `python main.py`. Two things it
does that `python main.py` doesn't:

  1. After uvicorn starts and `/health` responds 200, opens the user's
     default browser to the dashboard URL automatically. Same UX as
     `streamlit run` and `jupyter notebook`.
  2. Prints a friendly banner with the dashboard URL so the user
     doesn't have to guess the port — useful when CORE_PORT is set
     non-default.

Daemon mode (`openvox start`, runs in background, survives terminal
close, auto-starts on boot) lands in Phase 4 — different
implementation (launchd plist / systemd unit / Windows Service).
"""
from __future__ import annotations

import logging
import threading
import time
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

import typer
import uvicorn

logger = logging.getLogger("openvox.cli")


def _wait_for_health(port: int, timeout_s: float = 30.0) -> bool:
    """Poll http://127.0.0.1:<port>/health until it returns 200.

    Used by the browser-opener thread so the user doesn't see a
    "connection refused" page if they happen to hit localhost before
    uvicorn finishes booting. Returns True if /health came up within
    `timeout_s`; False if we timed out.
    """
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.2)
    return False


def _open_browser_when_ready(port: int, no_browser: bool) -> None:
    """Background thread: wait for /health, then open the dashboard.

    Skipped entirely if --no-browser was passed (useful for headless
    servers / SSH sessions where popping a browser would fail or be
    silently swallowed).
    """
    if no_browser:
        return
    if not _wait_for_health(port):
        # Timed out waiting for boot. Don't open the browser — the user
        # will see uvicorn's logs in the terminal and can act on the
        # error. Opening anyway would just hit a connection-refused page.
        return
    url = f"http://localhost:{port}/dashboard"
    typer.echo(f"  Opening {url} in your browser…")
    try:
        webbrowser.open(url)
    except Exception as e:
        logger.warning("could not open browser: %s", e)


def run_cmd(
    port: int = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to bind the server to. Defaults to CORE_PORT env / config (usually 8000).",
    ),
    host: str = typer.Option(
        "0.0.0.0",
        "--host",
        "-h",
        help="Host to bind to. 0.0.0.0 lets other machines on your LAN connect; 127.0.0.1 is local-only.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Skip auto-opening the dashboard in a browser (useful on headless servers).",
    ),
) -> None:
    """Start the OpenVox server foreground.

    Press Ctrl-C to stop. For persistent / always-on operation, see
    `openvox start` (Phase 4 — daemon mode).
    """
    # Resolve config + a FREE port. Precedence: --port → persisted →
    # settings.core_port. If the preferred port is busy, auto-switch
    # upward + warn loudly rather than crashing with uvicorn's
    # "address already in use".
    from openvox.api.app import create_app
    from openvox.cli.portutil import resolve_port, save_runtime
    from openvox.config import get_settings

    settings = get_settings()
    effective_port, preferred_port = resolve_port(port, host=host)
    if effective_port != preferred_port:
        typer.secho(
            f"  ⚠  port {preferred_port} is in use — using {effective_port} instead.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    # Persist so the next `openvox run` (and the daemon) reuse this port.
    save_runtime(effective_port, host)

    # Sync the port we ACTUALLY bound back into settings. Server-side
    # code that builds self-referential URLs reads settings.core_port —
    # the Google OAuth redirect_uri (oauth/google.py), the post-connect
    # dashboard redirect (routes/integrations/google.py), and `openvox
    # info`'s health probe. Without this they'd point at the stale
    # config default (8000) while the daemon is really on, say, 8001 —
    # silently breaking the Google OAuth callback on any auto-switched
    # port. get_settings() is an lru_cache singleton, so this single
    # assignment is visible to every consumer.
    settings.core_port = effective_port

    app = create_app()

    # Friendly banner. Mirrors what we'll show in the Phase 4
    # `openvox start` daemon-launch path too.
    typer.echo(f"openvox — starting on http://{host}:{effective_port}")
    typer.echo(f"  dashboard: http://localhost:{effective_port}/dashboard")
    typer.echo(f"  api:       http://localhost:{effective_port}/api/v1/")
    typer.echo(f"  health:    http://localhost:{effective_port}/health")
    typer.echo("  Ctrl-C to stop")
    typer.echo("")

    # Kick off the browser-opener BEFORE the blocking uvicorn.run().
    # Daemon thread so it dies if uvicorn exits early.
    if not no_browser:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(effective_port, no_browser),
            daemon=True,
        ).start()

    # Configure uvicorn's logging via an explicit dictConfig that
    # ALSO registers handlers for the `openvox.*` logger hierarchy.
    # Without this, our module-level loggers (created via
    # `logger = logging.getLogger(__name__)`) have no handler
    # attached after uvicorn's own dictConfig runs, so INFO messages
    # like "hydrated N secrets from encrypted store" silently
    # disappear — leaving operators with no way to confirm via
    # `openvox logs` that hydration actually ran. This was surfaced
    # by the v0.1.7 incident where we couldn't tell from the log
    # alone whether the secrets bridge had fired.
    #
    # We copy uvicorn's default LOGGING_CONFIG and add a single
    # "openvox" logger entry that routes INFO+ to the same default
    # stderr handler. `disable_existing_loggers=False` preserves
    # any third-party loggers that have already been configured.
    log_level_upper = settings.log_level.upper()
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(message)s",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            # uvicorn defaults — preserved verbatim from
            # uvicorn.config.LOGGING_CONFIG.
            "uvicorn": {
                "handlers": ["default"], "level": log_level_upper,
                "propagate": False,
            },
            "uvicorn.error": {"level": log_level_upper},
            "uvicorn.access": {
                "handlers": ["access"], "level": log_level_upper,
                "propagate": False,
            },
            # Our addition: every openvox.* module-level logger
            # routes to the same default handler at the same level.
            # This is the single line that makes "hydrated..."
            # appear in `openvox logs`.
            "openvox": {
                "handlers": ["default"], "level": log_level_upper,
                "propagate": False,
            },
        },
    }

    # Run uvicorn — blocks the main thread until Ctrl-C.
    # No reload flag: code-changes-during-runtime is a dev-mode
    # responsibility; production / personal use restarts via the
    # daemon manager (Phase 4) or just re-runs `openvox run`.
    uvicorn.run(
        app,
        host=host,
        port=effective_port,
        log_config=log_config,
        ws="websockets",
        reload=False,
    )
