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

    Also snapshots ``os.environ`` at entry and restores it at exit.
    This catches env vars set DIRECTLY (not via monkeypatch) inside
    the test — most notably the provider-key env vars that the
    hydration helper writes. Without this snapshot, the first test
    that calls ``_hydrate_secrets_into_env()`` would leak a real
    BYTEPLUS_VOICE_API_KEY into every subsequent test.

    Yields the path to the isolated home so tests can assert on
    files written there (machine key, SQLite DB, etc.).
    """
    import os

    home = tmp_path / "openvox"
    home.mkdir(parents=True, exist_ok=True)
    db_path = home / "openvox.db"

    monkeypatch.setenv("DATA_DIR", str(home))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    # Some provider modules also gate on this — keep it predictable
    # in tests rather than inheriting whatever the host has set.
    monkeypatch.setenv("OPENVOX_AUTH", "disabled")

    # Explicitly UNSET provider-key env vars that the test contributor
    # likely has in their host shell (.env, exported in .zshrc, etc.).
    # Without this, a test that asserts "settings.byteplus_voice_api_key
    # == ''" will fail on a developer's machine but pass in CI — exactly
    # the kind of "works on my machine" trap we want to avoid.
    # Keep this list in sync with the mapping in
    # `openvox/api/app.py::_hydrate_secrets_into_env`.
    for env_var in (
        "BYTEPLUS_LLM_API_KEY",
        "BYTEPLUS_VOICE_API_KEY",
        "BYTEPLUS_RTC_APP_ID",
        "BYTEPLUS_RTC_APP_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ELEVENLABS_API_KEY",
        "CARTESIA_API_KEY",
        "DEEPGRAM_API_KEY",
        "ASSEMBLYAI_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
    ):
        monkeypatch.delenv(env_var, raising=False)

    # Snapshot the FULL env BEFORE the test runs. On teardown we
    # restore os.environ to exactly this state so direct os.environ
    # mutations (e.g. by _hydrate_secrets_into_env) don't leak.
    env_snapshot = dict(os.environ)

    _bust_caches()
    try:
        yield home
    finally:
        # Restore env exactly. We do this in three steps because
        # pytest's monkeypatch will ALSO run an undo at teardown,
        # and we want our snapshot to be authoritative.
        for key in list(os.environ.keys()):
            if key not in env_snapshot:
                del os.environ[key]
        for key, value in env_snapshot.items():
            os.environ[key] = value
        _bust_caches()


def _bust_caches() -> None:
    """Clear all process-level caches that would otherwise leak
    one test's config into the next.

    Three caches matter:

      1. ``openvox.config.get_settings`` — ``@lru_cache(1)`` on a
         function that reads env vars + ``.env`` once.
      2. ``openvox.secrets._fernet_cached`` — module-level Fernet
         instance keyed on the machine key file. Different tempdir
         = different machine key = needs rebuild.
      3. ``openvox.db.session._engine`` (and ``_sessionmaker``) —
         module-level SQLAlchemy async engine pointing at
         ``settings.database_url`` at first construction. A second
         test would inherit the first test's engine + connection
         pool pointing at the first test's tempdir SQLite.

    All three are cleared every fixture entry AND exit. If you
    add another module-level cache that depends on settings or the
    filesystem, add it here too — and write a smoke test in
    ``test_conftest_smoke.py`` proving the leak is fixed.
    """
    # Settings.
    from openvox.config import get_settings

    get_settings.cache_clear()

    # Fernet (secrets store).
    try:
        import openvox.secrets as secrets_mod

        secrets_mod._fernet_cached = None
    except ImportError:
        pass

    # DB engine + session-maker.
    try:
        import openvox.db.session as session_mod

        session_mod._engine = None
        session_mod._sessionmaker = None
    except ImportError:
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
