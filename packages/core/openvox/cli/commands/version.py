"""`openvox version` — print the installed version.

Reads from the package metadata so the printed value tracks
`pyproject.toml`'s `version` field automatically — no risk of the
displayed version drifting from the published wheel.
"""
from __future__ import annotations

import typer


def version_cmd() -> None:
    """Print the openvox-core version."""
    # importlib.metadata is the canonical Python way to read installed
    # package metadata without parsing pyproject.toml at runtime.
    try:
        from importlib.metadata import version as _pkg_version

        v = _pkg_version("openvox-core")
    except Exception:
        # Fallback for editable installs / source checkouts where the
        # package isn't fully resolved yet.
        v = "0.0.0-dev"

    typer.echo(f"openvox {v}")
