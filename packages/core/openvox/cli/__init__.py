"""OpenVox CLI — entry point for `pip install openvox && openvox <command>`.

Foreground + introspection (Phase 1 PR-2):
    openvox version  — print version string
    openvox info     — show resolved config + service status
    openvox run      — start FastAPI server foreground + open browser

Daemon lifecycle (Phase 4 PR-1):
    openvox start    — install + start as a background daemon
                       (launchd on macOS, systemd --user on Linux,
                        Windows Service via nssm on Windows)
    openvox stop     — stop the daemon
    openvox status   — running / stopped / unknown
    openvox restart  — stop + start
    openvox logs     — tail ~/.openvox/logs/openvox.log

Still planned (per docs/PLANNING_SESSION15.md):
    openvox onboard  — terminal-mode equivalent of the dashboard wizard

Why typer:
    Built on Click, plays nicely with type hints, matches the Python idiom we
    use elsewhere in the codebase (FastAPI / Pydantic). 4.5M downloads/month
    so well-trodden ground.

Entry point:
    pyproject.toml [project.scripts]: `openvox = "openvox.cli:main"`
    so `pip install openvox-core && openvox version` works after install.
"""
from __future__ import annotations

from typing import Any


def main() -> None:
    """Console-script entry point. Delegates to typer's app() runner.

    Imports `app` lazily so that `import openvox.cli` (or anything
    deeper like `openvox.cli.daemon`) doesn't pull in FastAPI /
    uvicorn / SQLAlchemy at import time — keeps unit tests and the
    daemon-only code paths cheap.
    """
    from openvox.cli.main import app

    app()


def __getattr__(name: str) -> Any:
    """Lazy attribute access for `from openvox.cli import app` style usage.

    Without this, removing the top-level `from openvox.cli.main import app`
    would break any caller doing attribute access on the cli package.
    """
    if name == "app":
        from openvox.cli.main import app

        return app
    raise AttributeError(f"module 'openvox.cli' has no attribute {name!r}")


__all__ = ["main", "app"]
