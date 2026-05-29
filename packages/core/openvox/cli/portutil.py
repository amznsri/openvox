"""Port resolution + persistence for the CLI (`run` / `start`).

Why this exists
===============

Both `openvox run` and `openvox start` used to bind the configured
port (default 8000) with no free-port check. Port 8000 is one of the
most contended dev ports on the planet — a user with any other local
web app, a leftover uvicorn, or a conda-env service already on :8000
hit one of two failure modes:

  - `openvox run`  → uvicorn raises "address already in use" and the
    command crashes with a stack trace.
  - `openvox start` → the launchd/systemd daemon's uvicorn fails to
    bind and the process exits, but the service manager still reports
    the unit as "running". The user then loads localhost:8000 and
    hits WHATEVER ELSE owns the port — typically a bare
    `{"detail":"Not Found"}` from some other FastAPI app — and
    concludes OpenVox is broken. (Reported by a real first-time
    installer.)

The fix: resolve a FREE port before binding, persist the choice so a
restart reuses it, and surface the actual URL loudly.

Resolution precedence
=====================

1. Explicit `--port N` passed on the CLI (highest — honour intent).
2. The persisted port from a previous run (`~/.openvox/runtime.json`),
   so `openvox stop` → `openvox start` keeps the same URL.
3. `settings.core_port` (the env/config default, usually 8000).

Whichever wins, if it's already occupied we scan UPWARD for the next
free port and emit a visible warning. The resolved port is then
persisted so the daemon's restarts (and the next foreground `run`)
reuse it. "Maintain the port across restarts" + "never crash on a
busy port" are both satisfied: we keep the chosen port when it's
free, and only move when we have to.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

# Scan window when the preferred port is busy. 8000 → up to 8064.
# Wide enough to clear a cluster of dev servers, narrow enough that
# the eventual URL is still "near" the expected 8000.
_SCAN_LIMIT = 64


def _runtime_path() -> Path:
    """`~/.openvox/runtime.json` — small JSON blob holding the last
    resolved {port, host}. Co-located with the rest of OpenVox state
    (db, logs, secret.key) under the data dir."""
    from openvox.config import get_settings

    return Path(get_settings().data_dir) / "runtime.json"


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """True iff we can bind ``(host, port)`` right now.

    We bind the SAME host the server will use (default 0.0.0.0) so the
    check matches uvicorn's actual bind semantics — a port can look
    free on 127.0.0.1 while something holds 0.0.0.0 (or vice versa).
    SO_REUSEADDR mirrors uvicorn so we don't false-negative on a
    socket still in TIME_WAIT from a clean prior shutdown.

    Note: there's an inherent TOCTOU gap between this check and
    uvicorn's bind — something could grab the port in the
    microseconds between. That's acceptable: the common case this
    guards (a long-lived app already squatting :8000) is stable, and
    a genuine race just surfaces uvicorn's normal bind error.
    """
    # An empty host (uvicorn accepts "") means all interfaces — bind
    # to 0.0.0.0 for the probe in that case.
    probe_host = host or "0.0.0.0"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((probe_host, port))
            return True
        except OSError:
            return False


def find_free_port(preferred: int, host: str = "0.0.0.0", *, scan_limit: int = _SCAN_LIMIT) -> int:
    """Return ``preferred`` if free, else the next free port above it.

    Raises RuntimeError if nothing in ``[preferred, preferred+scan_limit]``
    is free — which in practice means something is very wrong (64
    consecutive occupied ports).
    """
    if is_port_available(preferred, host):
        return preferred
    for candidate in range(preferred + 1, preferred + 1 + scan_limit):
        if candidate > 65535:
            break
        if is_port_available(candidate, host):
            return candidate
    raise RuntimeError(
        f"no free port found in range {preferred}–{preferred + scan_limit}. "
        f"Free up a port or pass an explicit --port."
    )


def load_persisted_port() -> int | None:
    """Read the last resolved port from runtime.json, or None if absent
    / unreadable. Never raises — a corrupt file just means we fall
    back to the configured default."""
    path = _runtime_path()
    try:
        data = json.loads(path.read_text())
        port = int(data.get("port"))
        if 1 <= port <= 65535:
            return port
    except (FileNotFoundError, ValueError, TypeError, OSError, json.JSONDecodeError):
        pass
    return None


def save_runtime(port: int, host: str) -> None:
    """Persist the resolved {port, host} to runtime.json. Best-effort —
    a write failure (read-only home, etc.) is logged-and-ignored so it
    never blocks startup."""
    path = _runtime_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"port": port, "host": host}, indent=2))
    except OSError:
        pass


def resolve_port(
    explicit: int | None,
    *,
    host: str = "0.0.0.0",
    use_persisted: bool = True,
) -> tuple[int, int]:
    """Resolve the port to bind, applying the precedence rules.

    Returns ``(resolved_port, preferred_port)`` so the caller can tell
    whether a switch happened (resolved != preferred → warn the user).

    - ``explicit``: the value of ``--port`` (None if not passed).
    - ``use_persisted``: when True (the default), a previously-saved
      port is consulted before falling back to settings.core_port.
      `openvox start` passes True so daemon restarts keep their URL.
    """
    from openvox.config import get_settings

    if explicit is not None:
        preferred = explicit
    elif use_persisted and (persisted := load_persisted_port()) is not None:
        preferred = persisted
    else:
        preferred = get_settings().core_port

    resolved = find_free_port(preferred, host)
    return resolved, preferred
