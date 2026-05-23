"""OpenVox CLI — entry point for `pip install openvox && openvox <command>`.

Today's scope (Phase 1 PR-2):
    openvox version  — print version string
    openvox info     — show resolved config + service status
    openvox run      — start FastAPI server foreground + open browser

Coming in Phase 4 (per docs/PLANNING_SESSION15.md):
    openvox start / stop / status / restart   — daemon lifecycle (launchd / systemd / Windows Service)
    openvox onboard                            — interactive first-run wizard
    openvox logs                               — tail daemon logs

Why typer:
    Built on Click, plays nicely with type hints, matches the Python idiom we
    use elsewhere in the codebase (FastAPI / Pydantic). 4.5M downloads/month
    so well-trodden ground.

Entry point:
    pyproject.toml [project.scripts]: `openvox = "openvox.cli:main"`
    so `pip install openvox-core && openvox version` works after install.
"""
from __future__ import annotations

from openvox.cli.main import app


def main() -> None:
    """Console-script entry point. Delegates to typer's app() runner."""
    app()


__all__ = ["main", "app"]
