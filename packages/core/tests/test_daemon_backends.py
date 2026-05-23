"""Unit tests for the per-OS daemon backends (Phase 4 PR-1).

These tests cover the pieces that don't need a real launchctl / systemctl
/ nssm.exe — primarily plist & unit-file generation and the factory's
OS dispatch. Lifecycle commands (install/start/stop/status) need the
real OS tooling and are covered by manual smoke tests in
docs/install-cli.md.
"""
from __future__ import annotations

import plistlib
import sys
from unittest.mock import patch

import pytest


# ── factory dispatch ─────────────────────────────────────────────────


@patch("platform.system", return_value="Darwin")
def test_factory_returns_launchd_on_macos(_mock: object) -> None:
    from openvox.cli.daemon import get_backend
    from openvox.cli.daemon.launchd import LaunchdBackend

    assert isinstance(get_backend(), LaunchdBackend)


@patch("platform.system", return_value="Linux")
def test_factory_returns_systemd_on_linux(_mock: object) -> None:
    from openvox.cli.daemon import get_backend
    from openvox.cli.daemon.systemd import SystemdBackend

    assert isinstance(get_backend(), SystemdBackend)


@patch("platform.system", return_value="Windows")
def test_factory_returns_windows_service_on_windows(_mock: object) -> None:
    from openvox.cli.daemon import get_backend
    from openvox.cli.daemon.windows_service import WindowsServiceBackend

    assert isinstance(get_backend(), WindowsServiceBackend)


@patch("platform.system", return_value="FreeBSD")
def test_factory_raises_on_unsupported_os(_mock: object) -> None:
    from openvox.cli.daemon import get_backend

    with pytest.raises(NotImplementedError, match="FreeBSD"):
        get_backend()


# ── launchd plist generation ─────────────────────────────────────────


def test_launchd_install_writes_valid_plist(tmp_path, monkeypatch) -> None:
    """install() should write a launchd-parseable plist with our
    standard keys: Label, ProgramArguments, RunAtLoad, KeepAlive."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-derive HOME-based constants after the patch.
    import importlib

    import openvox.cli.daemon.launchd as launchd_mod

    importlib.reload(launchd_mod)

    # Stub the subprocess so we don't actually call launchctl
    # (would dirty the user's system and need macOS).
    with patch.object(launchd_mod, "subprocess") as mock_sp:
        mock_sp.run.return_value.returncode = 1  # "not loaded"
        backend = launchd_mod.LaunchdBackend()
        backend.install(port=8765, host="127.0.0.1")

    assert backend.plist_path.exists()
    with open(backend.plist_path, "rb") as f:
        plist = plistlib.load(f)

    assert plist["Label"] == "com.openvox.daemon"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    # Args should include our port + host + no-browser flags
    argv = plist["ProgramArguments"]
    assert "run" in argv
    assert "--no-browser" in argv
    assert "8765" in argv
    assert "127.0.0.1" in argv


# ── systemd unit-text generation ─────────────────────────────────────


def test_systemd_unit_text_contains_required_sections() -> None:
    """The generated unit file must include [Unit], [Service], [Install]
    sections + ExecStart with our flags."""
    from openvox.cli.daemon.systemd import SystemdBackend

    backend = SystemdBackend()
    text = backend._unit_text(port=9999, host="0.0.0.0")
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "[Install]" in text
    assert "ExecStart=" in text
    assert "--no-browser" in text
    assert "--port 9999" in text
    assert "--host 0.0.0.0" in text
    assert "Restart=on-failure" in text
    # We log both to journal AND to the file (so `openvox logs` works
    # cross-platform). Confirm the file-append redirect is in place.
    assert "StandardOutput=append:" in text
    assert "/.openvox/logs/openvox.log" in text


# ── Windows backend metadata ─────────────────────────────────────────


def test_windows_service_name_has_no_dots() -> None:
    """Windows Service names can't contain '.', so the Windows backend
    overrides the cross-platform `com.openvox.daemon` label."""
    from openvox.cli.daemon.windows_service import WindowsServiceBackend

    assert "." not in WindowsServiceBackend.SERVICE_NAME
    assert WindowsServiceBackend.SERVICE_NAME == "OpenVoxDaemon"


# ── status parsing — launchd ─────────────────────────────────────────


def test_launchd_status_parses_pid_from_list_output() -> None:
    """`launchctl list <label>` output uses property-list-style key=val
    lines; we extract PID with a regex. Verify on a real-shaped sample."""
    from openvox.cli.daemon.launchd import LaunchdBackend

    sample = """{
    "StandardOutPath" = "/Users/x/.openvox/logs/openvox.log";
    "Label" = "com.openvox.daemon";
    "OnDemand" = false;
    "LastExitStatus" = 0;
    "PID" = 12345;
    "Program" = "/usr/local/bin/openvox";
};"""
    with patch("openvox.cli.daemon.launchd.subprocess") as mock_sp:
        mock_sp.run.return_value.returncode = 0
        mock_sp.run.return_value.stdout = sample
        result = LaunchdBackend().status()

    assert result.state == "running"
    assert result.pid == 12345


def test_launchd_status_returns_stopped_when_not_registered() -> None:
    from openvox.cli.daemon.launchd import LaunchdBackend

    with patch("openvox.cli.daemon.launchd.subprocess") as mock_sp:
        mock_sp.run.return_value.returncode = 1
        mock_sp.run.return_value.stdout = ""
        result = LaunchdBackend().status()

    assert result.state == "stopped"
    assert result.pid is None
    assert "openvox start" in result.detail


# ── status parsing — systemd ─────────────────────────────────────────


def test_systemd_status_parses_active_state() -> None:
    from openvox.cli.daemon.systemd import SystemdBackend

    sample = "ActiveState=active\nMainPID=4242\n"
    with patch("openvox.cli.daemon.systemd.subprocess") as mock_sp:
        mock_sp.run.return_value.returncode = 0
        mock_sp.run.return_value.stdout = sample
        result = SystemdBackend().status()

    assert result.state == "running"
    assert result.pid == 4242


def test_systemd_status_treats_inactive_as_stopped() -> None:
    from openvox.cli.daemon.systemd import SystemdBackend

    sample = "ActiveState=inactive\nMainPID=0\n"
    with patch("openvox.cli.daemon.systemd.subprocess") as mock_sp:
        mock_sp.run.return_value.returncode = 0
        mock_sp.run.return_value.stdout = sample
        result = SystemdBackend().status()

    assert result.state == "stopped"
    assert result.pid is None


# Keep pytest happy if run standalone
if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
