"""Round-trip + edge-case tests for openvox.secrets.

The encrypted secrets store is the load-bearing infrastructure for
the first-run wizard (Phase 3) and the hydration bridge tested in
test_secrets_hydration.py. Bugs here are silent — keys can be
"saved" but unreadable, or "decrypted" but actually corrupt.

Coverage targets:
  * Public API: set / get / delete / resolve_provider_key,
    list_configured_providers, setup_complete
  * Machine-key lifecycle: generate-on-first-call, persistence,
    0600 permissions
  * Edge cases: empty value (delete), case normalisation,
    duplicate writes (upsert), corrupted ciphertext

Note on the test pattern: every test uses the `isolated_db` fixture
so each test gets its own SQLite + machine key. Bug isolation
across cases is essential because the store is process-global.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


# ── Machine-key lifecycle ──────────────────────────────────────────


async def test_machine_key_generated_on_first_use(isolated_db: Path) -> None:
    """First call to anything that needs Fernet generates secret.key."""
    from openvox import secrets

    key_path = secrets._key_path()
    assert not key_path.exists()  # fresh tempdir

    # Touch a key — triggers _fernet() which triggers _load_or_create_key()
    await secrets.set_provider_key("byteplus", "llm_api_key", "v")

    assert key_path.exists(), "secret.key was not created"
    assert len(key_path.read_bytes()) > 0, "secret.key is empty"


async def test_machine_key_persists_across_calls(isolated_db: Path) -> None:
    """Two operations in the same tempdir must use the same key.

    Otherwise the second op would re-generate, decryption of the first
    op's value would fail, and the user's keys would silently vanish
    on every process restart.
    """
    from openvox import secrets

    # First op generates the key.
    await secrets.set_provider_key("byteplus", "llm_api_key", "value-1")
    key_path = secrets._key_path()
    original_bytes = key_path.read_bytes()

    # Bust the Fernet cache so the next call has to re-load from disk.
    secrets._fernet_cached = None

    # Second op should READ the existing key, not regenerate.
    retrieved = await secrets.get_provider_key("byteplus", "llm_api_key")
    assert retrieved == "value-1", "key roundtrip broke after Fernet cache bust"
    assert key_path.read_bytes() == original_bytes, "secret.key was regenerated"


@pytest.mark.skipif(
    os.name == "nt", reason="chmod permissions don't apply meaningfully on Windows"
)
async def test_machine_key_permissions_0600(isolated_db: Path) -> None:
    """The machine key must be 0600 — readable only by owner.

    A 0644 keyfile defeats the purpose of encryption since any user
    on the box could read it and decrypt every stored credential.
    """
    from openvox import secrets

    await secrets.set_provider_key("byteplus", "llm_api_key", "v")
    key_path = secrets._key_path()

    perms = stat.S_IMODE(os.stat(key_path).st_mode)
    assert perms == 0o600, f"secret.key permissions are {oct(perms)}, expected 0o600"


# ── set / get round-trip ───────────────────────────────────────────


async def test_set_then_get_roundtrip(isolated_db: Path) -> None:
    from openvox import secrets

    await secrets.set_provider_key("byteplus", "voice_api_key", "secret-value-xyz")
    retrieved = await secrets.get_provider_key("byteplus", "voice_api_key")
    assert retrieved == "secret-value-xyz"


async def test_set_upsert_overwrites(isolated_db: Path) -> None:
    """Second set on same (provider, key_name) replaces, doesn't error."""
    from openvox import secrets

    await secrets.set_provider_key("openai", "api_key", "first-value")
    await secrets.set_provider_key("openai", "api_key", "second-value")

    assert await secrets.get_provider_key("openai", "api_key") == "second-value"


async def test_get_missing_returns_none(isolated_db: Path) -> None:
    from openvox import secrets

    assert await secrets.get_provider_key("byteplus", "never_set") is None


async def test_set_empty_value_deletes(isolated_db: Path) -> None:
    """Per the docstring: empty value = delete the row (so env-var fallback resumes)."""
    from openvox import secrets

    await secrets.set_provider_key("openai", "api_key", "stored-value")
    assert await secrets.get_provider_key("openai", "api_key") == "stored-value"

    await secrets.set_provider_key("openai", "api_key", "")  # delete

    assert await secrets.get_provider_key("openai", "api_key") is None


async def test_set_normalises_case(isolated_db: Path) -> None:
    """provider + key_name are lower-cased on both set + get."""
    from openvox import secrets

    await secrets.set_provider_key("BytePlus", "VOICE_API_KEY", "v")
    # Lookup with different casing must find the same row.
    assert await secrets.get_provider_key("byteplus", "voice_api_key") == "v"
    assert await secrets.get_provider_key("BYTEPLUS", "Voice_Api_Key") == "v"


async def test_set_rejects_empty_provider_or_key(isolated_db: Path) -> None:
    from openvox import secrets

    with pytest.raises(ValueError):
        await secrets.set_provider_key("", "key_name", "v")
    with pytest.raises(ValueError):
        await secrets.set_provider_key("provider", "", "v")


# ── delete ─────────────────────────────────────────────────────────


async def test_delete_existing_key(isolated_db: Path) -> None:
    from openvox import secrets

    await secrets.set_provider_key("anthropic", "api_key", "v")
    await secrets.delete_provider_key("anthropic", "api_key")

    assert await secrets.get_provider_key("anthropic", "api_key") is None


async def test_delete_missing_is_noop(isolated_db: Path) -> None:
    """Deleting a key that doesn't exist must NOT raise."""
    from openvox import secrets

    # Should silently succeed.
    await secrets.delete_provider_key("nonexistent", "key")


# ── list_configured_providers ──────────────────────────────────────


async def test_list_configured_providers_empty(isolated_db: Path) -> None:
    from openvox import secrets

    assert await secrets.list_configured_providers() == {}


async def test_list_configured_providers_grouping(isolated_db: Path) -> None:
    """Multiple keys per provider get grouped under one provider entry."""
    from openvox import secrets

    await secrets.set_provider_key("byteplus", "llm_api_key", "v")
    await secrets.set_provider_key("byteplus", "voice_api_key", "v")
    await secrets.set_provider_key("openai", "api_key", "v")

    result = await secrets.list_configured_providers()
    assert set(result.keys()) == {"byteplus", "openai"}
    assert sorted(result["byteplus"]) == ["llm_api_key", "voice_api_key"]
    assert result["openai"] == ["api_key"]


# ── resolve_provider_key (env-first, store-fallback) ───────────────


async def test_resolve_env_wins_over_store(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Docker / .env workflow contract: explicit env always wins."""
    from openvox import secrets

    await secrets.set_provider_key("byteplus", "llm_api_key", "from-store")
    monkeypatch.setenv("BYTEPLUS_LLM_API_KEY", "from-env")

    assert (
        await secrets.resolve_provider_key("byteplus", "llm_api_key") == "from-env"
    )


async def test_resolve_store_fallback_when_env_unset(
    isolated_db: Path,
) -> None:
    from openvox import secrets

    await secrets.set_provider_key("byteplus", "llm_api_key", "from-store")
    # Env is cleaned by tmp_openvox_home fixture.
    assert "BYTEPLUS_LLM_API_KEY" not in os.environ

    assert (
        await secrets.resolve_provider_key("byteplus", "llm_api_key") == "from-store"
    )


async def test_resolve_neither_returns_none(isolated_db: Path) -> None:
    from openvox import secrets

    assert await secrets.resolve_provider_key("byteplus", "llm_api_key") is None


async def test_resolve_custom_env_var_name(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env_var_name override lets callers pick a non-default env name."""
    from openvox import secrets

    monkeypatch.setenv("CUSTOM_ENV", "from-custom")

    val = await secrets.resolve_provider_key(
        "byteplus", "llm_api_key", env_var_name="CUSTOM_ENV"
    )
    assert val == "from-custom"


# ── setup_complete ──────────────────────────────────────────────────


async def test_setup_complete_no_keys(isolated_db: Path) -> None:
    from openvox import secrets

    status = await secrets.setup_complete()
    assert status["complete"] is False
    assert status["have_llm"] is False
    assert status["have_voice"] is False
    assert status["providers_configured"] == {}


async def test_setup_complete_llm_only(isolated_db: Path) -> None:
    from openvox import secrets

    await secrets.set_provider_key("openai", "api_key", "v")
    # OpenAI key counts as BOTH llm and voice per _ESSENTIAL_VOICE_KEYS — verify.

    status = await secrets.setup_complete()
    assert status["have_llm"] is True
    assert status["have_voice"] is True  # openai serves both
    assert status["complete"] is True


async def test_setup_complete_byteplus_full(isolated_db: Path) -> None:
    """Full BytePlus setup — both LLM and voice keys."""
    from openvox import secrets

    await secrets.set_provider_key("byteplus", "llm_api_key", "v")
    await secrets.set_provider_key("byteplus", "voice_api_key", "v")

    status = await secrets.setup_complete()
    assert status["complete"] is True
    assert "byteplus" in status["providers_configured"]


# ── corruption / error paths ───────────────────────────────────────


async def test_get_returns_none_on_corrupted_ciphertext(
    isolated_db: Path,
) -> None:
    """If the machine key was rotated, decryption fails — get() returns
    None and logs the issue rather than crashing.

    Simulates: user deleted ~/.openvox/secret.key (machine key
    regenerated), pre-existing rows can't be decrypted, system should
    degrade to "no key found" not "500 internal error".
    """
    from openvox import secrets
    from openvox.db import db_session
    from openvox.db.models import ProviderKey

    # Plant a row with garbage ciphertext (simulating post-rotation state).
    async with db_session() as s:
        s.add(
            ProviderKey(
                provider="byteplus",
                key_name="llm_api_key",
                encrypted_value="this-is-not-valid-fernet-output",
            )
        )

    # Must return None (and log), not raise.
    assert await secrets.get_provider_key("byteplus", "llm_api_key") is None
