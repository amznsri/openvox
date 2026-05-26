"""Regression tests for the secrets-hydration bridge (`_hydrate_secrets_into_env`).

These tests exist specifically because of bugs #77 and #78 in
CLAUDE.md §8, both shipped in v0.1.7:

  #77 — The Phase 3 wizard saved API keys to the encrypted store at
        ~/.openvox/.openvox/openvox.db (provider_keys table) but
        providers read settings.<provider>_api_key from pydantic-
        settings env vars. The two layers were never bridged, so
        every wizard-entered key was invisible to providers. A user
        clicked through the wizard, saved keys, then every feature
        (Test voice, Build by voice, every agent's real conversation
        flow) errored with "API_KEY not set".

  #78 — Even after writing the bridge (`_hydrate_secrets_into_env`),
        providers STILL saw empty keys because `register_builtins()`
        ran BEFORE hydration. The TTS / LLM / STT classes cache
        settings.<key> in their __init__ — once cached with the
        empty value, they never re-read.

Both bugs cost ~3 hours to diagnose during Session 16. A single
hydration test (50 LoC) would have caught them in CI before the
v0.1.7 PyPI upload. This file is that test.

If any test here breaks during a refactor, STOP. Don't paper over
it with `expected_failure` — the fix is to preserve the actual
hydration semantics, not to relax the assertion.

Pattern when adding new tests here: target a specific user-visible
failure mode (not an internal implementation detail). The
implementation has changed twice already and these tests survived;
that's because they assert on behaviour, not code shape.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


# ── Bug #77 regression: keys in store → keys in os.environ ─────────


async def test_hydration_copies_stored_keys_to_env(
    isolated_db: Path,
) -> None:
    """The core invariant: a key the wizard saved must end up in
    os.environ where settings (and therefore providers) can see it.

    Reproduces the exact bug #77 scenario:
      1. Encrypted store has a BytePlus voice key (simulating wizard
         save).
      2. Settings's env (BYTEPLUS_VOICE_API_KEY) is unset (simulating
         a user who configured ONLY via the wizard, never via .env).
      3. Run hydration.
      4. Assert env is now populated with the stored value.
    """
    from openvox import secrets as secret_store
    from openvox.api.app import _hydrate_secrets_into_env

    # Pretend the wizard saved a key.
    await secret_store.set_provider_key(
        "byteplus", "voice_api_key", "test-key-xyz-123"
    )
    # Make sure env starts unset (the fixture should already
    # ensure this, but be defensive).
    assert "BYTEPLUS_VOICE_API_KEY" not in os.environ

    await _hydrate_secrets_into_env()

    # Bug #77 regression check: the stored value MUST be in env now.
    assert os.environ.get("BYTEPLUS_VOICE_API_KEY") == "test-key-xyz-123"


async def test_hydration_env_var_takes_priority_over_store(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If both env AND store have a value, env wins.

    This is what the docstring of `resolve_provider_key` promises:
    Docker / .env users who set BYTEPLUS_VOICE_API_KEY directly
    must not have their config silently overridden by a stale wizard
    value still in the store.
    """
    from openvox import secrets as secret_store
    from openvox.api.app import _hydrate_secrets_into_env

    # Both sources have a value, but they differ.
    monkeypatch.setenv("BYTEPLUS_VOICE_API_KEY", "from-env")
    await secret_store.set_provider_key(
        "byteplus", "voice_api_key", "from-store"
    )

    await _hydrate_secrets_into_env()

    # Env wins — the store value must NOT overwrite.
    assert os.environ["BYTEPLUS_VOICE_API_KEY"] == "from-env"


async def test_hydration_no_op_when_nothing_stored(
    isolated_db: Path,
) -> None:
    """Empty store → env unchanged → no crash.

    The Docker/.env-only user has an empty provider_keys table.
    Hydration must not blow up or set ghost values.
    """
    from openvox.api.app import _hydrate_secrets_into_env

    assert "BYTEPLUS_VOICE_API_KEY" not in os.environ
    await _hydrate_secrets_into_env()
    assert "BYTEPLUS_VOICE_API_KEY" not in os.environ


async def test_hydration_multiple_providers(
    isolated_db: Path,
) -> None:
    """Wizard-saved keys for multiple providers all hydrate together."""
    from openvox import secrets as secret_store
    from openvox.api.app import _hydrate_secrets_into_env

    await secret_store.set_provider_key("byteplus", "llm_api_key", "bp-llm")
    await secret_store.set_provider_key("byteplus", "voice_api_key", "bp-voice")
    await secret_store.set_provider_key("openai", "api_key", "oa")
    await secret_store.set_provider_key("anthropic", "api_key", "an")

    await _hydrate_secrets_into_env()

    assert os.environ["BYTEPLUS_LLM_API_KEY"] == "bp-llm"
    assert os.environ["BYTEPLUS_VOICE_API_KEY"] == "bp-voice"
    assert os.environ["OPENAI_API_KEY"] == "oa"
    assert os.environ["ANTHROPIC_API_KEY"] == "an"


# ── v0.2.12: Google OAuth client_id + client_secret hydration ───────
# Phase 1 (Native Connect Gmail) added google_oauth_client_id +
# google_oauth_client_secret to the settings; v0.2.12 added them to
# the hydration mapping so users can paste them via the dashboard
# Integrations form instead of editing the launchd plist.


async def test_hydration_google_oauth_client_keys(isolated_db: Path) -> None:
    """Google OAuth Client ID + Secret flow from the encrypted store
    through to env vars + pydantic Settings."""
    from openvox import secrets as secret_store
    from openvox.api.app import _hydrate_secrets_into_env
    from openvox.config import get_settings

    await secret_store.set_provider_key(
        "google", "oauth_client_id", "test-client-123.apps.googleusercontent.com"
    )
    await secret_store.set_provider_key(
        "google", "oauth_client_secret", "GOCSPX-test-secret-value"
    )

    await _hydrate_secrets_into_env()

    # Env vars set (this is what every downstream code path reads).
    assert os.environ["GOOGLE_OAUTH_CLIENT_ID"].endswith(".googleusercontent.com")
    assert os.environ["GOOGLE_OAUTH_CLIENT_SECRET"].startswith("GOCSPX-")

    # Settings sees the new values after the cache bust — this is
    # what Phase 1's `start_auth_flow()` reads when building the
    # authorization URL.
    s = get_settings()
    assert s.google_oauth_client_id.endswith(".googleusercontent.com")
    assert s.google_oauth_client_secret.startswith("GOCSPX-")


# ── Bug #78 regression: settings cache busted after hydration ──────


async def test_hydration_busts_settings_cache(
    isolated_db: Path,
) -> None:
    """get_settings() must return the FRESH value after hydration.

    Bug #78 was that even after env got populated, providers saw
    empty values because they'd already called get_settings() once
    and cached the empty Settings instance (via @lru_cache).

    The fix in `_hydrate_secrets_into_env` is to call
    `get_settings.cache_clear()` if it hydrated anything. This test
    proves that contract: call get_settings BEFORE hydration (gets
    empty), call hydration, call get_settings AGAIN — must see the
    new value.
    """
    from openvox import secrets as secret_store
    from openvox.api.app import _hydrate_secrets_into_env
    from openvox.config import get_settings

    # First call: nothing stored, nothing in env.
    s_before = get_settings()
    assert s_before.byteplus_voice_api_key == ""

    # Wizard saves a key.
    await secret_store.set_provider_key(
        "byteplus", "voice_api_key", "newly-saved-key"
    )

    # Hydrate (this is where bug #78 lived — cache wasn't being busted).
    await _hydrate_secrets_into_env()

    # Second call: must see the new value, not the cached empty one.
    s_after = get_settings()
    assert s_after.byteplus_voice_api_key == "newly-saved-key", (
        "settings cache wasn't busted — see CLAUDE.md §8 bug #78"
    )


async def test_hydration_provider_simulation(
    isolated_db: Path,
) -> None:
    """End-to-end simulation of the user flow that triggered bug #78.

    The real flow was:
      1. Daemon starts → lifespan runs → register_builtins() instantiates
         BytePlusTTS() → it caches settings.byteplus_voice_api_key (empty).
      2. Hydration runs LATER → env now has the key.
      3. User hits /api/v1/playground/synthesize → provider says
         "no API key" because its __init__-cached _api_key is empty.

    The fix in app.py is to reorder lifespan so hydration runs BEFORE
    register_builtins. This test simulates a fresh provider
    instantiation AFTER hydration to assert the same fix path
    is functional: instantiating a provider after hydration must
    see the key.
    """
    from openvox import secrets as secret_store
    from openvox.api.app import _hydrate_secrets_into_env
    from openvox.config import get_settings

    await secret_store.set_provider_key(
        "byteplus", "voice_api_key", "real-key-value"
    )

    # The correct order: hydrate FIRST, then provider reads settings.
    await _hydrate_secrets_into_env()

    # Now any provider that reads settings.byteplus_voice_api_key
    # in its __init__ will see the hydrated value. We simulate that
    # by reading the setting through the same path the provider uses.
    s = get_settings()
    api_key_a_provider_would_cache = s.byteplus_voice_api_key
    assert api_key_a_provider_would_cache == "real-key-value"


# ── Edge cases ──────────────────────────────────────────────────────


async def test_hydration_empty_stored_value_is_ignored(
    isolated_db: Path,
) -> None:
    """A stored empty string should NOT overwrite env (and definitely
    shouldn't set an empty env var which would break .env fallbacks).

    Empty values in the store typically mean "the wizard deleted this"
    — should behave as if nothing was stored.
    """
    from openvox import secrets as secret_store
    from openvox.api.app import _hydrate_secrets_into_env

    # Note: set_provider_key with empty value DELETES the row per
    # admin.py contract. So this test is implicitly verifying that
    # delete behaviour is right, not that empty values flow through.
    await secret_store.set_provider_key("byteplus", "voice_api_key", "value")
    await secret_store.set_provider_key("byteplus", "voice_api_key", "")  # delete

    await _hydrate_secrets_into_env()

    # Deleted key should not appear in env.
    assert os.environ.get("BYTEPLUS_VOICE_API_KEY", "") == ""
