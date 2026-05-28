"""Per-OS daemon backends for `openvox start / stop / status / restart`.

Each backend wraps the OS-native service manager so the CLI commands
stay platform-agnostic:

  * macOS   → launchd (`launchctl`) + `~/Library/LaunchAgents/com.openvox.daemon.plist`
  * Linux   → systemd user units (`systemctl --user`) + `~/.config/systemd/user/openvox.service`
  * Windows → Windows Service (via bundled `nssm.exe`)

All three back the same `DaemonBackend` interface in `base.py` so the
five lifecycle commands (`start`/`stop`/`status`/`restart`/`logs`) read
a single switch from the factory.
"""
from __future__ import annotations

import platform

from openvox.cli.daemon.base import DaemonBackend, DaemonStatus


def get_backend() -> DaemonBackend:
    """Return the daemon backend for the current OS.

    Raises NotImplementedError on unsupported platforms (BSD, etc.) —
    we'd rather fail loudly than silently no-op and leave the user
    confused why `openvox status` reports nothing.
    """
    system = platform.system()
    if system == "Darwin":
        from openvox.cli.daemon.launchd import LaunchdBackend

        return LaunchdBackend()
    if system == "Linux":
        from openvox.cli.daemon.systemd import SystemdBackend

        return SystemdBackend()
    if system == "Windows":
        from openvox.cli.daemon.windows_service import WindowsServiceBackend

        return WindowsServiceBackend()
    raise NotImplementedError(
        f"`openvox start/stop/status` is not yet supported on {system}. "
        "Use `openvox run` for foreground operation."
    )


__all__ = ["DaemonBackend", "DaemonStatus", "get_backend"]
