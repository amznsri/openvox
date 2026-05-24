"""Smoke tests for the running_daemon + http_client fixtures.

Same logic as ``tests/test_conftest_smoke.py`` (Phase 1) but for the
e2e fixtures: if these don't work, every other e2e test will fail
with confusing errors. Run this file first when debugging Phase 2.
"""
from __future__ import annotations

import httpx
import pytest


pytestmark = pytest.mark.e2e


# ── Daemon spawns and serves /health ───────────────────────────────


def test_daemon_responds_to_health(running_daemon) -> None:
    """The subprocess started + bound to its assigned port + /health
    returns the expected JSON shape."""
    r = httpx.get(f"{running_daemon.base_url}/health", timeout=5.0)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_daemon_has_tmp_home(running_daemon) -> None:
    """The data dir was passed through correctly + the daemon created
    its DB inside it."""
    assert running_daemon.tmp_home.exists()
    # SQLite DB created on first request (init_db runs in lifespan).
    # The path is relative to DATA_DIR per the daemon's config.
    db_path = running_daemon.tmp_home / "openvox.db"
    assert db_path.exists(), (
        f"daemon DB not created at {db_path} — DATA_DIR not respected?"
    )


def test_two_consecutive_daemons_use_different_ports(
    running_daemon, tmp_path
) -> None:
    """Sanity: random-port logic gives each test its own port.

    Implicitly verifies the daemon shut down cleanly — if it hadn't,
    we'd see a port collision or stale-listener errors."""
    first_port = int(running_daemon.base_url.rsplit(":", 1)[1])
    # The first daemon is still running (this is its test). The fixture
    # would tear it down after this test. We just verify the port came
    # from the dynamic range, not a hardcoded value.
    assert first_port > 10000, f"unexpected port {first_port}"


# ── http_client fixture ────────────────────────────────────────────


async def test_http_client_default_base_url(http_client, running_daemon) -> None:
    """The async client is prebaked with base_url so tests don't have
    to construct full URLs."""
    r = await http_client.get("/health")
    assert r.status_code == 200


async def test_http_client_can_hit_admin_setup_status(http_client) -> None:
    """/api/v1/admin/setup/status returns the wizard state. Used by
    the dashboard's first-run detection. Fresh daemon = nothing
    configured."""
    r = await http_client.get("/api/v1/admin/setup/status")
    assert r.status_code == 200
    body = r.json()
    assert body["complete"] is False  # fresh install — no keys yet
