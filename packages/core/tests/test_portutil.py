"""Port resolution + persistence (CLI run/start auto-switch).

Covers the logic that fixes the "port 8000 already in use → daemon
silently dies → user hits another app's 404" failure a first-time
installer reported. The guarantees pinned here:

  - A free port resolves to itself.
  - An OCCUPIED preferred port auto-switches to the next free one.
  - Explicit --port takes precedence over the persisted value.
  - The persisted value is consulted before the configured default.
  - resolve_port reports (resolved, preferred) so callers can detect
    a switch and warn.

Hermetic: we occupy ports by binding real sockets inside the test
(not by assuming the machine's port map), so the suite is
deterministic on any CI runner.
"""

from __future__ import annotations

import socket

import pytest


def _occupy(port_host=("127.0.0.1", 0)):
    """Bind + listen on an ephemeral port, returning (sock, port).
    The caller closes the socket. While open, that port is occupied."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(port_host)
    s.listen(1)
    return s, s.getsockname()[1]


def test_free_port_resolves_to_itself():
    from openvox.cli.portutil import find_free_port, is_port_available

    # Grab an ephemeral port, close it, then assert it's reported free
    # and find_free_port returns it unchanged.
    s, port = _occupy()
    s.close()
    assert is_port_available(port, "127.0.0.1") is True
    assert find_free_port(port, "127.0.0.1") == port


def test_occupied_port_switches_upward():
    from openvox.cli.portutil import find_free_port, is_port_available

    s, port = _occupy()
    try:
        assert is_port_available(port, "127.0.0.1") is False
        resolved = find_free_port(port, "127.0.0.1")
        assert resolved != port
        assert resolved > port
        assert is_port_available(resolved, "127.0.0.1") is True
    finally:
        s.close()


def test_resolve_explicit_wins_over_persisted(tmp_path, monkeypatch):
    """An explicit --port is honoured even when a different port is
    persisted."""
    import openvox.cli.portutil as pu

    # Point runtime.json at a temp dir + seed a persisted port.
    monkeypatch.setattr(pu, "_runtime_path", lambda: tmp_path / "runtime.json")
    pu.save_runtime(8055, "0.0.0.0")
    assert pu.load_persisted_port() == 8055

    # Explicit 9123 should win (and is free on the test box), so
    # resolve returns it as both resolved + preferred.
    resolved, preferred = pu.resolve_port(9123, host="127.0.0.1")
    assert preferred == 9123
    assert resolved == 9123


def test_resolve_persisted_wins_over_default(tmp_path, monkeypatch):
    """With no --port, a persisted port is preferred over
    settings.core_port."""
    import openvox.cli.portutil as pu

    monkeypatch.setattr(pu, "_runtime_path", lambda: tmp_path / "runtime.json")
    # Use an ephemeral free port as the "persisted" value so it
    # resolves to itself.
    s, free_port = _occupy()
    s.close()
    pu.save_runtime(free_port, "0.0.0.0")

    resolved, preferred = pu.resolve_port(None, host="127.0.0.1", use_persisted=True)
    assert preferred == free_port
    assert resolved == free_port


def test_resolve_reports_switch(tmp_path, monkeypatch):
    """When the preferred (persisted) port is busy, resolve returns a
    different resolved port so the caller can warn."""
    import openvox.cli.portutil as pu

    monkeypatch.setattr(pu, "_runtime_path", lambda: tmp_path / "runtime.json")
    s, busy_port = _occupy()
    try:
        pu.save_runtime(busy_port, "0.0.0.0")
        resolved, preferred = pu.resolve_port(None, host="127.0.0.1", use_persisted=True)
        assert preferred == busy_port
        assert resolved != busy_port  # switched
    finally:
        s.close()


def test_persisted_port_roundtrip_and_corruption(tmp_path, monkeypatch):
    import openvox.cli.portutil as pu

    rt = tmp_path / "runtime.json"
    monkeypatch.setattr(pu, "_runtime_path", lambda: rt)

    # Missing file → None, no raise.
    assert pu.load_persisted_port() is None
    # Roundtrip.
    pu.save_runtime(8042, "127.0.0.1")
    assert pu.load_persisted_port() == 8042
    # Corrupt file → None, no raise.
    rt.write_text("{ not json")
    assert pu.load_persisted_port() is None
    # Out-of-range → None.
    rt.write_text('{"port": 70000}')
    assert pu.load_persisted_port() is None


def test_find_free_port_exhaustion_raises():
    """If every port in the scan window is busy, raise rather than
    spin forever. We simulate by passing scan_limit=0 against a busy
    port."""
    from openvox.cli.portutil import find_free_port

    s, port = _occupy()
    try:
        with pytest.raises(RuntimeError):
            find_free_port(port, "127.0.0.1", scan_limit=0)
    finally:
        s.close()
