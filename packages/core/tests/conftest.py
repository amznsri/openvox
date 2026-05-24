"""Pytest fixtures for openvox-core's test suite.

Three core fixtures, used by ~all test modules:

  * ``tmp_openvox_home`` — gives each test its own isolated
    ``~/.openvox/``-shaped data directory. Bypasses the per-process
    settings cache so providers + the secrets store + the SQLite DB
    all see the test's directory, not the real user's. Generates a
    fresh machine encryption key per-test so secrets tests don't
    share Fernet state across cases.

  * ``isolated_db`` — builds on ``tmp_openvox_home``; sets
    ``DATABASE_URL`` to point at a SQLite file inside the tempdir,
    runs ``init_db()`` so all tables exist, then yields. Async
    fixture — use with ``async def`` tests.

  * ``mocked_http`` — wraps ``respx`` to intercept outbound ``httpx``
    calls so provider tests can't accidentally hit real BytePlus /
    OpenAI / etc. endpoints. The router is yielded so tests can
    add routes per-test.

Pattern when adding a new fixture: define it here, document it in
the module docstring above, never hard-code paths across modules
(use the fixture).

Notes for Phase 1 contributors:

  - All three fixtures bust the relevant per-process caches AT
    ENTRY AND EXIT. This matters because ``openvox.config.
    get_settings`` is ``@lru_cache``'d and ``openvox.secrets._fernet``
    caches the Fernet instance — if a previous test pinned them to
    its tempdir, this test would silently inherit that state.

  - The fixtures deliberately set env vars via ``monkeypatch.setenv``
    rather than mutating ``get_settings()`` directly. This mirrors how
    real users configure OpenVox (env vars / ``.env``) and avoids
    coupling tests to pydantic-settings internals.

  - If a test needs to start the FastAPI app, build it inside the
    test body (after the fixtures have set up the env) — never at
    module import time, which would lock in the wrong env vars.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest


# ── Core fixture #1: isolated data directory + caches busted ───────


@pytest.fixture
def tmp_openvox_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Isolated ``~/.openvox/`` for one test.

    Sets ``DATA_DIR`` and ``DATABASE_URL`` env vars so the settings
    layer + secrets store + DB all land inside a fresh tempdir.
    Busts ``openvox.config.get_settings``'s ``@lru_cache`` and
    ``openvox.secrets._fernet_cached`` on both entry and exit so no
    cross-test bleed.

    Yields the path to the isolated home so tests can assert on
    files written there (machine key, SQLite DB, etc.).
    """
    home = tmp_path / "openvox"
    home.mkdir(parents=True, exist_ok=True)
    db_path = home / "openvox.db"

    monkeypatch.setenv("DATA_DIR", str(home))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    # Some provider modules also gate on this — keep it predictable
    # in tests rather than inheriting whatever the host has set.
    monkeypatch.setenv("OPENVOX_AUTH", "disabled")

    _bust_caches()
    yield home
    _bust_caches()


def _bust_caches() -> None:
    """Clear settings + secrets process-level caches.

    Both ``get_settings()`` and the Fernet instance behind
    ``_fernet()`` are cached for performance. In tests that's a
    leak — a previous test's tempdir keeps being used for the
    "real" path. Always call this between tests.
    """
    # Settings: lazy import because openvox.config is itself
    # cached at module level — re-importing wouldn't help.
    from openvox.config import get_settings

    get_settings.cache_clear()

    # Fernet: lazy import because openvox.secrets pulls in the DB
    # layer which we don't want loaded for tests that only need
    # tmp_openvox_home.
    try:
        import openvox.secrets as secrets_mod

        secrets_mod._fernet_cached = None
    except ImportError:
        # secrets module may not even be importable in some narrow
        # test contexts; that's fine — there's nothing to clear.
        pass


# ── Core fixture #2: fresh DB with all tables ──────────────────────


@pytest.fixture
async def isolated_db(tmp_openvox_home: Path) -> AsyncIterator[Path]:
    """Fresh SQLite database with all tables created.

    Builds on ``tmp_openvox_home`` (so the DB file lives in the
    test's tempdir). Calls ``init_db()`` which runs
    ``Base.metadata.create_all()`` against every model in
    ``openvox.db.models``. After Phase 3 lands, this fixture will
    instead run ``alembic upgrade head``.

    Yields the path to the database file so tests can inspect it
    via raw sqlite3 if they want to verify schema directly.
    """
    from openvox.db import init_db

    await init_db()
    yield tmp_openvox_home / "openvox.db"
    # Teardown is implicit — tmp_path is cleaned up by pytest.


# ── Core fixture #3: outbound HTTP intercept ───────────────────────


@pytest.fixture
def mocked_http():
    """Intercept outbound httpx calls via respx.

    Yields the respx Router so tests can declare per-test routes:

        async def test_byteplus_tts_call(mocked_http):
            mocked_http.post("https://openspeech.bytedance.com/...").mock(
                return_value=httpx.Response(200, content=b"\\x00" * 1024)
            )
            ...

    ``assert_all_called=False`` because most provider tests only
    care that the ONE expected call happened, not that every
    registered mock was hit.

    Pattern for adding shared mocks: extend with a per-provider
    helper fixture in the test module (e.g.
    ``byteplus_tts_success(mocked_http)``).
    """
    import respx

    with respx.mock(assert_all_called=False) as router:
        yield router
