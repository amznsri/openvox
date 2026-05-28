"""Smoke tests for the conftest fixtures themselves.

If these fail, every test that relies on `tmp_openvox_home`,
`isolated_db`, or `mocked_http` will fail in confusing ways.
Run this file first when debugging a fixture issue.

Pattern: each test exercises ONE fixture in isolation and asserts
the invariant the fixture promises in conftest.py's docstring.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ── tmp_openvox_home ────────────────────────────────────────────────


def test_tmp_openvox_home_creates_isolated_dir(tmp_openvox_home: Path) -> None:
    """Fixture returns a directory that exists + is empty."""
    assert tmp_openvox_home.is_dir()
    # Should have nothing in it yet — the test hasn't done anything.
    # (Earlier _bust_caches call doesn't touch the filesystem.)
    assert list(tmp_openvox_home.iterdir()) == []


def test_tmp_openvox_home_sets_env_vars(tmp_openvox_home: Path) -> None:
    """DATA_DIR + DATABASE_URL env vars point at the tempdir."""
    assert os.environ["DATA_DIR"] == str(tmp_openvox_home)
    assert os.environ["DATABASE_URL"].endswith("/openvox.db")
    assert str(tmp_openvox_home) in os.environ["DATABASE_URL"]


def test_tmp_openvox_home_settings_pick_up_env(tmp_openvox_home: Path) -> None:
    """get_settings() returns the tempdir's data_dir, not the real one."""
    from openvox.config import get_settings

    s = get_settings()
    # Compare resolved paths because Settings may stringify differently.
    assert Path(s.data_dir).resolve() == tmp_openvox_home.resolve()


def test_tmp_openvox_home_isolation_between_tests_part_1(
    tmp_openvox_home: Path,
) -> None:
    """Touch a file; the next test must NOT see it."""
    (tmp_openvox_home / "leak-marker.txt").write_text("hello")
    assert (tmp_openvox_home / "leak-marker.txt").exists()


def test_tmp_openvox_home_isolation_between_tests_part_2(
    tmp_openvox_home: Path,
) -> None:
    """The marker from the previous test must not appear here.

    If this fails, the fixture isn't actually giving each test its
    own tempdir.
    """
    assert not (tmp_openvox_home / "leak-marker.txt").exists()


# ── isolated_db ─────────────────────────────────────────────────────


async def test_isolated_db_creates_tables(isolated_db: Path) -> None:
    """init_db() ran; the SQLite file exists with the expected tables."""
    assert isolated_db.exists()
    assert isolated_db.stat().st_size > 0  # not an empty file

    # Sanity-check via raw sqlite3 — doesn't depend on our ORM code.
    import sqlite3

    conn = sqlite3.connect(str(isolated_db))
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    # Sample of expected tables — full list lives in
    # openvox/db/models.py. If this set diverges, models changed
    # and the test needs updating.
    expected = {"agents", "skills", "personas", "provider_keys"}
    missing = expected - tables
    assert not missing, f"isolated_db missing tables: {missing}"


# ── mocked_http ─────────────────────────────────────────────────────


async def test_mocked_http_intercepts_outbound_call(mocked_http) -> None:
    """A registered respx route catches the outbound httpx call."""
    import httpx

    mocked_http.get("https://example.com/test").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    async with httpx.AsyncClient() as client:
        r = await client.get("https://example.com/test")

    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_mocked_http_blocks_unmocked_calls(mocked_http) -> None:
    """A call to an unmocked URL raises rather than hitting the real network.

    Defense against test pollution — if a provider test forgets
    to mock its endpoint, we'd rather hard-fail than make a real
    API call.
    """
    import httpx
    import respx

    async with httpx.AsyncClient() as client:
        # respx raises AllMockedAssertionError (or similar) for
        # unmatched routes when its mock context is active.
        with pytest.raises(Exception):  # noqa: B017
            await client.get("https://unmocked.example.com/")
