"""Linux systemd backend — user-mode units (no sudo needed).

`systemctl --user` runs services as the logged-in user. Symmetric with
the macOS LaunchAgent model: no privilege escalation, service shares
the user's env, starts at session.

Sub-cycle:
  1. install() writes the unit file to ~/.config/systemd/user/openvox.service
     and runs `daemon-reload` so systemd picks it up, then `enable` to
     register-on-boot.
  2. start() runs `systemctl --user start`.
  3. status() queries `systemctl --user show` (machine-readable) — the
     human-friendly `status` subcommand is too noisy to parse.

A note on `linger`: by default, `systemctl --user` services stop when
the user logs out (or when SSH sessions end). For always-on operation
the user has to run `loginctl enable-linger $USER` once. We print a
hint at the end of `install()` so the user knows.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from openvox.cli.daemon.base import DaemonBackend, DaemonStatus

USER_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT_NAME = "openvox.service"


class SystemdBackend(DaemonBackend):
    @property
    def unit_path(self) -> Path:
        return USER_UNIT_DIR / UNIT_NAME

    def _openvox_path(self) -> str:
        """Resolve the executable to invoke from ExecStart.

        systemd ExecStart is space-delimited so we return a single
        string. `shutil.which` for the installed shim; fall back to
        `<python> -m openvox.cli` for editable installs.
        """
        bin_path = shutil.which("openvox")
        if bin_path:
            return bin_path
        return f"{sys.executable} -m openvox.cli"

    def _unit_text(self, *, port: int, host: str) -> str:
        # `StandardOutput=append:<path>` writes to a file in addition to
        # the systemd journal. We do this so `openvox logs` can tail the
        # same file across all three OSes without needing a
        # journalctl-vs-tail switch in the logs command.
        return (
            "[Unit]\n"
            "Description=OpenVox voice agent daemon\n"
            "After=network.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={self._openvox_path()} run --no-browser --port {port} --host {host}\n"
            f"WorkingDirectory={Path.home()}/.openvox\n"
            f"StandardOutput=append:{self.log_path}\n"
            f"StandardError=append:{self.error_log_path}\n"
            "Restart=on-failure\n"
            "RestartSec=10\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )

    def install(self, *, port: int, host: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(self._unit_text(port=port, host=host))
        # daemon-reload makes systemd notice the new / changed unit.
        # Enable so the daemon comes back on next session boot.
        self._systemctl("daemon-reload", check=True)
        self._systemctl("enable", UNIT_NAME, check=True)

    def uninstall(self) -> None:
        if self.unit_path.exists():
            self._systemctl("disable", UNIT_NAME, check=False)
            self.unit_path.unlink()
            self._systemctl("daemon-reload", check=False)

    def start(self) -> None:
        self._systemctl("start", UNIT_NAME, check=True)

    def stop(self) -> None:
        # check=False — `stop` on an already-stopped unit returns
        # non-zero on some systemd versions; we treat that as success.
        self._systemctl("stop", UNIT_NAME, check=False)

    def restart(self) -> None:
        # systemd's native `restart` re-uses the cgroup and is cleaner
        # than the base-class stop+start (which spawns two transitions).
        self._systemctl("restart", UNIT_NAME, check=True)

    def status(self) -> DaemonStatus:
        try:
            res = subprocess.run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    UNIT_NAME,
                    "--property=ActiveState,MainPID",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return DaemonStatus(
                state="unknown",
                pid=None,
                detail="systemctl not found — is this really Linux?",
            )
        if res.returncode != 0:
            return DaemonStatus(
                state="stopped",
                pid=None,
                detail="not installed — run `openvox start` to install + start",
            )
        active_match = re.search(r"ActiveState=(\w+)", res.stdout)
        pid_match = re.search(r"MainPID=(\d+)", res.stdout)
        active_state = active_match.group(1) if active_match else "unknown"
        pid_val = (
            int(pid_match.group(1))
            if pid_match and pid_match.group(1) != "0"
            else None
        )
        if active_state == "active" and pid_val:
            return DaemonStatus(
                state="running",
                pid=pid_val,
                detail=f"running as PID {pid_val}",
            )
        if active_state in ("inactive", "failed"):
            return DaemonStatus(
                state="stopped",
                pid=None,
                detail=f"installed but {active_state}",
            )
        return DaemonStatus(
            state="unknown",
            pid=pid_val,
            detail=f"systemd reports ActiveState={active_state}",
        )

    # ── internals ─────────────────────────────────────────────────────

    def _systemctl(self, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            check=check,
        )
