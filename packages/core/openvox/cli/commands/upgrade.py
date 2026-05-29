"""`openvox upgrade` — update openvox-core using whatever installed it.

The recurring support question is "what's the upgrade command?" — and the
honest answer used to be "depends how you installed it." This command
removes that guesswork: it inspects how THIS process was installed and
runs the matching upgrade automatically.

  - pipx venv          → `pipx upgrade openvox-core`
  - ~/.openvox/venv    → that venv's own `pip install --upgrade`
                         (the curl installer's fallback backend)
  - Homebrew (Cellar)  → prints `brew upgrade` (we never pip into a
                         Cellar — Homebrew owns those files)
  - anything else      → `python -m pip install --upgrade` for this env

Re-running the curl installer is an equivalent, backend-agnostic upgrade
for the first three cases; this command is just the in-tool shortcut so
users don't have to remember which one they used.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer


def _detect_method() -> tuple[str, str | None, str]:
    """Classify this install. Returns (method, tool, human_hint).

    ``tool`` is the executable to drive the upgrade (pipx path, the
    venv's pip path, or the python interpreter) — or None for Homebrew,
    which we never mutate directly.
    """
    prefix = Path(sys.prefix).resolve()
    parts = set(prefix.parts)

    # pipx installs each app under <PIPX_HOME>/venvs/<app> — default
    # ~/.local/pipx/venvs/openvox-core — so "pipx" is in the path.
    if "pipx" in parts:
        return ("pipx", shutil.which("pipx") or "pipx", "pipx-managed install")

    # Homebrew (incl. Linuxbrew) — Cellar-backed; never pip into it.
    if "Cellar" in parts or str(prefix).startswith(
        ("/opt/homebrew", "/usr/local/Cellar", "/home/linuxbrew")
    ):
        return ("homebrew", None, "Homebrew install")

    # A venv (the curl installer's ~/.openvox/venv fallback, or any
    # other venv) — upgrade with that venv's own pip.
    bindir = "Scripts" if os.name == "nt" else "bin"
    pip = prefix / bindir / ("pip.exe" if os.name == "nt" else "pip")
    if pip.exists():
        default_venv = Path.home() / ".openvox" / "venv"
        hint = (
            "venv install (~/.openvox/venv)"
            if prefix == default_venv.resolve()
            else f"venv at {prefix}"
        )
        return ("venv", str(pip), hint)

    # Fallback: pip module against whatever interpreter is running us.
    return ("pip", sys.executable, "pip install")


def upgrade_cmd(
    target_version: str = typer.Argument(
        None,
        help="Pin a specific version, e.g. 0.2.40 (default: upgrade to latest).",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="Show the detected install method + the command, without running it.",
    ),
) -> None:
    """Upgrade openvox-core in place using the installer that owns it."""
    method, tool, hint = _detect_method()
    spec = "openvox-core" + (f"=={target_version}" if target_version else "")

    if method == "homebrew":
        typer.secho("OpenVox was installed via Homebrew.", fg=typer.colors.YELLOW)
        typer.echo("Upgrade with:")
        if target_version:
            typer.echo(f"  brew update && brew install amznsri/openvox/openvox@{target_version}")
            typer.echo("  (or omit the version for the latest)")
        else:
            typer.echo("  brew update && brew upgrade openvox")
        raise typer.Exit(0)

    if method == "pipx":
        # pipx upgrade has no version pin — use install --force to pin.
        argv = (
            [tool, "install", "--force", spec]
            if target_version
            else [tool, "upgrade", "openvox-core"]
        )
    elif method == "venv":
        argv = [tool, "install", "--upgrade", spec]
    else:  # pip
        argv = [tool, "-m", "pip", "install", "--upgrade", spec]

    typer.echo(f"Detected: {hint}")
    typer.echo(f"Command:  {' '.join(argv)}")
    if check:
        raise typer.Exit(0)

    try:
        subprocess.run(argv, check=True)
    except FileNotFoundError:
        typer.secho(
            f"Could not find '{argv[0]}'. Is it on your PATH?",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        typer.secho(f"Upgrade failed (exit {e.returncode}).", fg=typer.colors.RED, err=True)
        raise typer.Exit(e.returncode)

    typer.secho("\nUpgraded. Restart to load the new version:", fg=typer.colors.GREEN)
    typer.echo("  openvox stop && openvox start     (background daemon)")
    typer.echo("  # or just re-launch `openvox run` if you run it in the foreground")
