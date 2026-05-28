"""E2E fixtures — spawn the daemon as a real subprocess + drive it
via HTTP and Playwright.

Loaded automatically by pytest (it's a `conftest_*.py` file in the
test root). Independent of `conftest.py`'s unit-test fixtures because
the e2e tests need a SEPARATE subprocess with a SEPARATE database;
they don't share the in-process fixtures.

Key fixtures:

  * ``running_daemon`` — spawns ``openvox run --no-browser
    --port <random>``, polls /health until 200, yields ``DaemonHandle``
    with ``base_url`` + ``tmp_home``. Tears the subprocess down on
    exit with SIGTERM, escalates to SIGKILL after 5 s.

  * ``http_client`` — pre-configured ``httpx.AsyncClient`` pointing at
    the daemon's base_url. Most e2e tests want this rather than the
    Playwright browser (faster + less brittle for API-only assertions).

Important design choices:

  - **Random port.** Avoids collisions with a real daemon the dev has
    running. We let the OS pick (bind to port 0) then pass the chosen
    port to the subprocess via --port.

  - **Per-test fresh DB.** Each test gets a tempdir DATA_DIR so the
    wizard's "first run" state always fires. Avoid scope="session"
    fixtures here — sharing state across tests defeats the purpose.

  - **No real network calls.** Tests set fake API keys via the wizard
    and verify HTTP wiring (status codes + error message changes),
    not actual provider responses. Real-API tests live under the
    ``@pytest.mark.network`` marker (Phase 5 install matrix).

  - **Slow on purpose.** Don't add e2e tests for things a unit test
    can cover. The e2e suite's job is catching cross-component
    regressions like bug #77 (wizard → provider gap), not exhaustive
    coverage.
"""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest


@dataclass
class DaemonHandle:
    """What every e2e test needs to talk to the running daemon."""

    base_url: str           # e.g. "http://127.0.0.1:51234"
    tmp_home: Path          # the daemon's DATA_DIR (also where its DB lives)
    proc: subprocess.Popen  # subprocess handle for manual control if needed


def _pick_free_port() -> int:
    """Bind to port 0 + read back what the OS gave us. Race-window
    is tiny (we release the socket before the daemon binds) but in
    a high-concurrency CI worker pool this can occasionally collide
    — accept the risk for now; if it bites, switch to a port-range
    sweep with retry."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout_s: float = 30.0) -> bool:
    """Poll ``GET /health`` until 200 or timeout."""
    deadline = time.monotonic() + timeout_s
    url = f"{base_url}/health"
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.2)
    return False


@pytest.fixture
def running_daemon(tmp_path: Path) -> Iterator[DaemonHandle]:
    """Spawn ``openvox run`` in a subprocess; tear down on exit.

    Each test gets a fresh tempdir as DATA_DIR so the daemon starts
    with no agents, no wizard state, no machine key — same shape as
    a brand-new pipx install.
    """
    port = _pick_free_port()
    home = tmp_path / "openvox-home"
    home.mkdir(parents=True, exist_ok=True)

    # The subprocess env: a minimal slice plus our test-specific
    # DATA_DIR + DATABASE_URL. We strip the host's BYTEPLUS_* /
    # OPENAI_* etc. keys for the same reason `conftest.py`'s
    # `tmp_openvox_home` does — otherwise the "is the wizard the
    # only key source?" tests can pass spuriously when the host
    # has a real key in their shell.
    env = {
        # Preserve PATH (so the openvox binary is findable) +
        # HOME (so non-data file lookups don't break) + basic
        # system vars.
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATA_DIR": str(home),
        "DATABASE_URL": f"sqlite+aiosqlite:///{home}/openvox.db",
        "OPENVOX_AUTH": "disabled",
        # INFO level so we can assert on lifecycle log lines (e.g. the
        # "hydrated N secrets from encrypted store" message). WARNING
        # would filter those out. Phase 4.2 added log-presence
        # assertions for operator-debugging signals.
        "LOG_LEVEL": "info",
    }
    # On macOS we need a few more env vars or python's locale init can fail.
    for passthrough_var in ("LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        if passthrough_var in os.environ:
            env[passthrough_var] = os.environ[passthrough_var]

    proc = subprocess.Popen(
        [sys.executable, "-m", "openvox.cli", "run",
         "--no-browser", "--port", str(port), "--host", "127.0.0.1"],
        env=env,
        # Buffer stdout/stderr so we can dump them on failure for debugging.
        # Using PIPE here would deadlock once the buffers fill (uvicorn is
        # chatty), so we redirect to a file in the tempdir instead.
        stdout=open(home / "daemon.stdout.log", "w"),
        stderr=open(home / "daemon.stderr.log", "w"),
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        if not _wait_for_health(base_url):
            # Health check failed — dump the daemon's logs so the
            # failure mode is debuggable rather than just "timeout".
            stderr_log = (home / "daemon.stderr.log").read_text()
            raise RuntimeError(
                f"daemon at {base_url} never became healthy.\n"
                f"--- stderr ---\n{stderr_log[-2000:]}"
            )
        yield DaemonHandle(base_url=base_url, tmp_home=home, proc=proc)
    finally:
        # Graceful shutdown first.
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)


@pytest.fixture
async def http_client(running_daemon: DaemonHandle) -> AsyncIterator[httpx.AsyncClient]:
    """Pre-configured AsyncClient pointing at the running daemon."""
    async with httpx.AsyncClient(
        base_url=running_daemon.base_url,
        timeout=httpx.Timeout(10.0, connect=2.0),
    ) as client:
        yield client


# ── Playwright fixtures (only loaded when pytest-playwright is installed) ──
# We're deliberately NOT making pytest-playwright a hard dep — adding
# 200MB of Chromium to the unit-test path is wasteful. The plugin
# auto-registers when present; if it isn't, browser tests just don't
# get collected.
