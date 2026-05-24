"""Debug test for Phase 4.2 — just verify the hydration log appears.

If THIS fails, the hydration log isn't reaching stderr regardless
of buffering / timing concerns. Easier-to-isolate variant of the
log assertion in test_first_run.py.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.e2e


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_hydration_log_appears_in_stderr(tmp_path: Path) -> None:
    """Spawn daemon with a pre-stored key, wait for hydration to run,
    stop daemon, grep stderr for the hydration line."""
    home = tmp_path / "openvox"
    home.mkdir()

    # Pre-seed the secrets store before spawning the daemon. We
    # spawn a one-shot Python process to do this so the daemon's
    # own startup is the only thing writing to the file in question.
    seed_script = f"""
import asyncio, os
os.environ['DATA_DIR'] = '{home}'
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///{home}/openvox.db'
from openvox.db import init_db
from openvox import secrets

async def main():
    await init_db()
    await secrets.set_provider_key('byteplus', 'voice_api_key', 'test-key')

asyncio.run(main())
"""
    r = subprocess.run([sys.executable, "-c", seed_script], capture_output=True, text=True)
    assert r.returncode == 0, f"seed failed: {r.stderr}"

    # Now spawn the daemon. Hydration should run during lifespan,
    # find the seeded key, and log "hydrated 1 secrets...".
    port = _pick_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "DATA_DIR": str(home),
        "DATABASE_URL": f"sqlite+aiosqlite:///{home}/openvox.db",
        "OPENVOX_AUTH": "disabled",
        "LOG_LEVEL": "info",
    }
    for v in ("LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        if v in os.environ:
            env[v] = os.environ[v]

    stderr_path = home / "stderr.log"
    proc = subprocess.Popen(
        [sys.executable, "-m", "openvox.cli", "run",
         "--no-browser", "--port", str(port), "--host", "127.0.0.1"],
        env=env,
        stdout=open(home / "stdout.log", "w"),
        stderr=open(stderr_path, "w"),
    )

    try:
        # Wait for daemon to be healthy.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                if r.status_code == 200:
                    break
            except (httpx.HTTPError, OSError):
                pass
            time.sleep(0.2)
        else:
            stderr_content = stderr_path.read_text()
            raise RuntimeError(
                f"daemon never healthy.\n--- stderr ---\n{stderr_content[-2000:]}"
            )

        # Daemon is healthy → hydration ran during lifespan startup.
        # Stop it cleanly so buffers flush.
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    # Read stderr. By now the subprocess has exited and all buffers flushed.
    log = stderr_path.read_text()

    assert "hydrated" in log.lower(), (
        f"hydration INFO log not in stderr.\n"
        f"--- full stderr ({len(log)} chars) ---\n{log}"
    )
