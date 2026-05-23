"""macOS launchd backend.

`launchctl` is Apple's service manager. We install OpenVox as a user
LaunchAgent (not a system LaunchDaemon) so:

  * No `sudo` required to install / start / stop.
  * The service runs as the logged-in user, with that user's env.
  * Loads at login automatically, gets unloaded at logout — sensible
    semantics for a personal tool.

The plist is generated at install-time (not shipped as a static
template) so port / host / Python interpreter path are all
customised per-machine.

A note on the `load`/`unload` verbs: Apple deprecation-warned them in
macOS 12 in favour of `bootstrap`/`bootout`, but the classic verbs
still work on every macOS up to and including the current release
(Tahoe / Darwin 25). They're enough simpler that we use them until
something actually breaks.
"""
from __future__ import annotations

import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from openvox.cli.daemon.base import DaemonBackend, DaemonStatus

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


class LaunchdBackend(DaemonBackend):
    @property
    def plist_path(self) -> Path:
        return LAUNCH_AGENTS_DIR / f"{self.SERVICE_NAME}.plist"

    def _openvox_argv(self) -> list[str]:
        """Resolve how launchd should invoke `openvox`.

        Prefers the `openvox` shim that pip installed on $PATH
        (`shutil.which`) because that's what the user has at the
        terminal. Falls back to `<python> -m openvox.cli` if the
        shim isn't found — covers editable installs where the
        console-scripts entry hasn't been (re)generated.
        """
        bin_path = shutil.which("openvox")
        if bin_path:
            return [bin_path]
        return [sys.executable, "-m", "openvox.cli"]

    def install(self, *, port: int, host: str) -> None:
        # Make sure the log dir exists BEFORE launchd tries to redirect
        # stdout/stderr into it — launchd silently fails (the service
        # respawn-loops without logs) when StandardOutPath's parent
        # doesn't exist.
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

        program_argv = self._openvox_argv() + [
            "run",
            "--no-browser",  # daemon mode — don't pop a browser at boot
            "--port",
            str(port),
            "--host",
            host,
        ]

        plist: dict[str, Any] = {
            "Label": self.SERVICE_NAME,
            "ProgramArguments": program_argv,
            # RunAtLoad → start when `launchctl load` runs (so `openvox
            # start` actually starts the process) AND at every user
            # login.
            "RunAtLoad": True,
            # KeepAlive controls auto-respawn. {SuccessfulExit: False}
            # means "respawn if it exited non-zero (crash) or via
            # uncaught signal, but NOT if it exited cleanly". A clean
            # `openvox stop` triggers a SIGTERM that uvicorn handles
            # gracefully and exits 0, so we don't fight ourselves.
            "KeepAlive": {"SuccessfulExit": False},
            "WorkingDirectory": str(Path.home() / ".openvox"),
            "StandardOutPath": str(self.log_path),
            "StandardErrorPath": str(self.error_log_path),
            # Prevent crash-restart loops from spinning up CPU.
            "ThrottleInterval": 10,
        }

        # If a prior version is loaded, launchd keeps it in memory even
        # after we overwrite the plist file. Unload first so the new
        # ProgramArguments actually take effect.
        if self._is_loaded():
            self._launchctl("unload", str(self.plist_path), check=False)

        with open(self.plist_path, "wb") as f:
            plistlib.dump(plist, f)

    def uninstall(self) -> None:
        if self._is_loaded():
            self._launchctl("unload", "-w", str(self.plist_path), check=False)
        if self.plist_path.exists():
            self.plist_path.unlink()

    def start(self) -> None:
        # `load -w` enables (clears any "Disabled=true" override) and
        # loads, which triggers RunAtLoad and starts the process.
        # Idempotent — re-running on an already-loaded plist is a no-op
        # that launchd handles internally.
        self._launchctl("load", "-w", str(self.plist_path), check=True)

    def stop(self) -> None:
        # `unload -w` disables and unloads. Symmetric with `load -w` in
        # `start()`. After `openvox stop`, the daemon won't auto-restart
        # at next login until `openvox start` runs again.
        self._launchctl("unload", "-w", str(self.plist_path), check=False)

    def status(self) -> DaemonStatus:
        # `launchctl list <label>` returns 0 + a property-list block if
        # the label is loaded, non-zero if not. PID is "-" when loaded
        # but not currently running.
        try:
            res = subprocess.run(
                ["launchctl", "list", self.SERVICE_NAME],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return DaemonStatus(
                state="unknown",
                pid=None,
                detail="launchctl not found — is this really macOS?",
            )
        if res.returncode != 0:
            return DaemonStatus(
                state="stopped",
                pid=None,
                detail="not registered — run `openvox start` to install + start",
            )
        pid_match = re.search(r'"PID"\s*=\s*(\d+);', res.stdout)
        if pid_match:
            pid = int(pid_match.group(1))
            return DaemonStatus(
                state="running",
                pid=pid,
                detail=f"running as PID {pid}",
            )
        return DaemonStatus(
            state="stopped",
            pid=None,
            detail="registered but not running",
        )

    # ── internals ─────────────────────────────────────────────────────

    def _is_loaded(self) -> bool:
        res = subprocess.run(
            ["launchctl", "list", self.SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0

    def _launchctl(self, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["launchctl", *args],
            capture_output=True,
            text=True,
            check=check,
        )
