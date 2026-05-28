"""`openvox logs` — tail the daemon log file.

The daemon writes stdout to ~/.openvox/logs/openvox.log on all three
OSes (launchd StandardOutPath, systemd StandardOutput=append:, nssm
AppStdout). This command is a friendlier alias for `tail -n 50` /
`tail -f` so users don't have to remember the path.

Windows note: `tail` doesn't ship with Windows. Fall back to a
Python loop that reads the last N lines + (in -f mode) appends new
ones every 200 ms.
"""
from __future__ import annotations

import platform
import subprocess
import time

import typer

from openvox.cli.daemon import get_backend


def _python_tail(path: str, n: int, follow: bool) -> None:
    """Cross-platform fallback for systems without `tail`.

    Reads the last `n` lines, prints them, then (in --follow mode)
    polls the file every 200 ms and prints any new content. Not as
    efficient as native tail for huge files but the daemon log
    shouldn't be huge — uvicorn's request lines are ~100 bytes each.
    """
    # Print the trailing N lines first.
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()[-n:]
        for line in lines:
            typer.echo(line.rstrip())
        if not follow:
            return
        # In follow mode, seek to end and poll for new bytes.
        f.seek(0, 2)
        try:
            while True:
                chunk = f.read()
                if chunk:
                    typer.echo(chunk, nl=False)
                else:
                    time.sleep(0.2)
        except KeyboardInterrupt:
            return


def logs_cmd(
    follow: bool = typer.Option(
        False,
        "-f",
        "--follow",
        help="Follow the log file (tail -f). Ctrl-C to exit.",
    ),
    n: int = typer.Option(
        50,
        "-n",
        "--lines",
        help="Number of trailing lines to print initially.",
    ),
) -> None:
    """Tail the OpenVox daemon log."""
    backend = get_backend()
    log_path = backend.log_path
    if not log_path.exists():
        typer.echo(f"no log yet at {log_path}")
        typer.echo("(run `openvox start` first, then come back)")
        raise typer.Exit(1)

    # Prefer system tail on Unix — handles huge files efficiently and
    # passes through Ctrl-C cleanly. Use the Python fallback on Windows
    # (no tail) and as a last resort if tail isn't on PATH.
    if platform.system() != "Windows":
        try:
            cmd = ["tail", f"-n{n}"]
            if follow:
                cmd.append("-f")
            cmd.append(str(log_path))
            # No capture — inherit parent stdout/stderr so Ctrl-C works
            # naturally and the user sees logs streamed in real time.
            subprocess.run(cmd, check=False)
            return
        except FileNotFoundError:
            pass

    _python_tail(str(log_path), n, follow)
