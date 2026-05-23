"""Windows Service backend via nssm.exe.

Windows lacks a first-class "user service" abstraction analogous to
launchd LaunchAgents or systemd --user units. The pattern that works
reliably for Python CLI tools is to install a Windows Service via NSSM
(the Non-Sucking Service Manager) — a tiny BSD-licensed wrapper that
turns an arbitrary executable into a Windows service with restart-on-
crash, automatic log rotation, and graceful shutdown.

Bundling story (Phase 4 PR-5): the ~300 KB `nssm.exe` is shipped inside
the wheel at `openvox/cli/daemon/_bin/nssm.exe`. Falls back to
$PATH-installed `nssm.exe` (e.g. via `choco install nssm`) if the
bundled binary is missing — useful for editable installs that haven't
copied the data files in.

Cannot be tested from macOS / Linux dev machines; rely on the GitHub
Actions Windows matrix runner.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from openvox.cli.daemon.base import DaemonBackend, DaemonStatus

# Windows service names can't contain '.', so we override the
# cross-platform `com.openvox.daemon` label.
WIN_SERVICE_NAME = "OpenVoxDaemon"


class WindowsServiceBackend(DaemonBackend):
    SERVICE_NAME = WIN_SERVICE_NAME

    @property
    def _nssm_path(self) -> Path:
        """Resolve the nssm.exe binary.

        Order: bundled (wheel data file) → PATH-installed → error.
        Bundling means the user never needs to `choco install nssm`
        themselves for the default install path.
        """
        bundled = Path(__file__).parent / "_bin" / "nssm.exe"
        if bundled.exists():
            return bundled
        sys_nssm = shutil.which("nssm.exe") or shutil.which("nssm")
        if sys_nssm:
            return Path(sys_nssm)
        raise FileNotFoundError(
            "nssm.exe not found. Re-install OpenVox, or `choco install nssm`."
        )

    def _openvox_path(self) -> str:
        bin_path = shutil.which("openvox.exe") or shutil.which("openvox")
        if bin_path:
            return bin_path
        # Fallback: run via python -m for editable installs. Quote
        # python.exe path because it usually lives under "Program Files".
        return f'"{sys.executable}" -m openvox.cli'

    def install(self, *, port: int, host: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # `nssm install <name> <exe> <args>` registers but doesn't
        # start. We then `nssm set` various properties.
        self._nssm(
            "install",
            self.SERVICE_NAME,
            self._openvox_path(),
            "run",
            "--no-browser",
            "--port",
            str(port),
            "--host",
            host,
            check=True,
        )
        self._nssm("set", self.SERVICE_NAME, "AppStdout", str(self.log_path), check=True)
        self._nssm(
            "set",
            self.SERVICE_NAME,
            "AppStderr",
            str(self.error_log_path),
            check=True,
        )
        self._nssm(
            "set",
            self.SERVICE_NAME,
            "AppDirectory",
            str(Path.home() / ".openvox"),
            check=True,
        )
        # Start automatically at boot. SERVICE_AUTO_START is the Windows
        # service-control-manager constant for "start at boot".
        self._nssm("set", self.SERVICE_NAME, "Start", "SERVICE_AUTO_START", check=True)

    def uninstall(self) -> None:
        # `nssm remove <name> confirm` is the non-interactive form.
        # check=False — remove fails if the service isn't installed,
        # which we treat as already-uninstalled.
        self._nssm("remove", self.SERVICE_NAME, "confirm", check=False)

    def start(self) -> None:
        self._nssm("start", self.SERVICE_NAME, check=True)

    def stop(self) -> None:
        self._nssm("stop", self.SERVICE_NAME, check=False)

    def status(self) -> DaemonStatus:
        try:
            res = subprocess.run(
                [str(self._nssm_path), "status", self.SERVICE_NAME],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as e:
            return DaemonStatus(state="unknown", pid=None, detail=str(e))
        out = res.stdout.strip().upper()
        if "SERVICE_RUNNING" in out:
            # NSSM doesn't surface the PID via `status`. We could shell
            # out to `sc queryex` for it but it's not worth the extra
            # subprocess for what the user can read in Task Manager.
            return DaemonStatus(
                state="running",
                pid=None,
                detail="running (use Task Manager for PID)",
            )
        if "SERVICE_STOPPED" in out:
            return DaemonStatus(
                state="stopped",
                pid=None,
                detail="installed but stopped",
            )
        if res.returncode != 0:
            return DaemonStatus(
                state="stopped",
                pid=None,
                detail="not installed — run `openvox start` to install + start",
            )
        return DaemonStatus(
            state="unknown",
            pid=None,
            detail=f"nssm reports {out}",
        )

    # ── internals ─────────────────────────────────────────────────────

    def _nssm(self, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self._nssm_path), *args],
            capture_output=True,
            text=True,
            check=check,
        )
