"""Abstract backend interface for the platform-native service manager.

Every per-OS backend (`LaunchdBackend`, `SystemdBackend`,
`WindowsServiceBackend`) implements this interface so the CLI
lifecycle commands can stay platform-agnostic.

The lifecycle assumes an install-then-start model: `install()` is
idempotent registration (writes the plist / unit / service entry),
and `start()` actually flips the service on. `openvox start`
calls both, in that order, so the user never has to think about
the distinction.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DaemonState = Literal["running", "stopped", "unknown"]


@dataclass
class DaemonStatus:
    """Snapshot of daemon state, returned by `status()`."""

    state: DaemonState
    pid: int | None  # set when state == "running"; None otherwise
    detail: str  # human-readable line for `openvox status` output


class DaemonBackend(ABC):
    """Interface every per-OS backend implements."""

    # Label / unit name used by the OS service manager. Same across
    # platforms (the Windows backend overrides because Windows service
    # names can't contain dots).
    SERVICE_NAME = "com.openvox.daemon"

    @property
    def log_path(self) -> Path:
        """Where the daemon writes stdout. Cross-platform per
        PLANNING_SESSION15.md §1.6 — `~/.openvox/logs/openvox.log`."""
        return Path.home() / ".openvox" / "logs" / "openvox.log"

    @property
    def error_log_path(self) -> Path:
        """Where the daemon writes stderr."""
        return Path.home() / ".openvox" / "logs" / "openvox.err.log"

    @abstractmethod
    def install(self, *, port: int, host: str) -> None:
        """Register the service with the OS.

        Idempotent — re-installing replaces any existing registration
        with the supplied port/host so `openvox start --port 9000`
        DTRT after a previous `openvox start --port 8000`.
        """

    @abstractmethod
    def uninstall(self) -> None:
        """Remove the service registration. No-op if not installed."""

    @abstractmethod
    def start(self) -> None:
        """Start the service. Assumes install() has been called."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the service. Leaves it installed."""

    @abstractmethod
    def status(self) -> DaemonStatus:
        """Return current state of the service."""

    def is_installed(self) -> bool:
        """Whether the service is REGISTERED with the OS — independent of
        whether it's currently running.

        This distinction matters: after `openvox stop`, the daemon is
        installed-but-stopped (the plist/unit still exists on disk). A
        check based purely on "is it running?" would wrongly report the
        service as gone and tell the user to re-install.

        Default implementation infers from ``status()`` — anything other
        than the explicit "not installed" detail counts as installed.
        Backends that can check the on-disk plist/unit directly override
        this for accuracy (and to avoid a second service-manager call).
        """
        st = self.status()
        if st.state == "unknown":
            return False
        return "not installed" not in st.detail and "not registered" not in st.detail

    def restart(self) -> None:
        """Default stop + start. Backends with a native restart verb
        (systemd does) override for cleaner semantics."""
        self.stop()
        self.start()
