"""Encrypted provider-key store — backend for the Phase 3 setup wizard.

What this is for
================
The Docker `.env` file works for operators who're comfortable editing
config files. For non-tech personal users (the OpenClaw-bar audience
described in `docs/PLANNING_SESSION15.md`), pasting keys into the
dashboard's first-run wizard is the friendlier path. This module is
the backing store for that wizard.

Resolution order at runtime
===========================
For any provider key:

  1. **Env var first**. If the user set BYTEPLUS_LLM_API_KEY in
     `.env`, that wins. Existing Docker deployments keep working
     unchanged.
  2. **Encrypted store fallback**. If the env var is empty/unset, we
     decrypt the corresponding row from `provider_keys` (if any) and
     return that.
  3. **None**. If neither has a value, the caller gets None — same as
     today's behaviour.

This preserves backward compatibility (env wins → existing deploys
unaffected) AND lets the wizard populate keys without writing to disk.

Encryption
==========
- Library: `cryptography` (Fernet — AES-128-CBC + HMAC-SHA256). Already
  pulled in transitively via `python-jose[cryptography]`.
- Per-machine symmetric key at ``~/.openvox/secret.key``, mode 0600.
- Key is auto-generated on first access and persists.
- Re-using the same key file across restarts means existing rows stay
  decryptable. Deleting the file invalidates all stored secrets —
  treat it like an SSH host key.

What this module does NOT do
============================
- Per-user encryption. There's exactly one operator in CLI / personal
  mode. Multi-tenant cloud deployment (the Phase-4+ open follow-up)
  will need per-tenant KMS — out of scope here.
- Key rotation. Rotating the machine key would invalidate every stored
  secret; not useful for this audience. If you need to rotate, wipe
  the file + the table together and have the user re-enter keys via
  the wizard.
- Migration of values FROM env TO the store. The wizard explicitly
  sets values; env vars are read live each time.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from openvox.config import get_settings

logger = logging.getLogger(__name__)


# ── Machine-key lifecycle ──────────────────────────────────────────


def _key_path() -> Path:
    """Filesystem location of the per-machine encryption key.

    Stored next to the SQLite DB / data directory so it's co-located
    with the data it protects. Users who back up their `~/.openvox/`
    folder pick up the key automatically; users who blow it away
    have to re-enter their provider keys via the wizard.
    """
    settings = get_settings()
    return Path(settings.data_dir) / "secret.key"


def _load_or_create_key() -> bytes:
    """Read the machine key from disk, generating it on first call.

    File permissions are explicitly set to 0600 (read/write for owner
    only) — a 0644 keyfile would defeat the purpose of encryption.
    """
    path = _key_path()
    if path.exists():
        return path.read_bytes()
    # First-run — generate a fresh key.
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # On Windows (or unusual filesystems) chmod may be a no-op or
        # raise — log and proceed. The file is still owned by the user
        # via filesystem ACLs.
        logger.warning("could not chmod %s — check permissions manually", path)
    logger.info("generated machine encryption key at %s", path)
    return key


_fernet_cached: Fernet | None = None


def _fernet() -> Fernet:
    """Process-local Fernet instance. Lazy + cached."""
    global _fernet_cached
    if _fernet_cached is None:
        _fernet_cached = Fernet(_load_or_create_key())
    return _fernet_cached


# ── Store API ──────────────────────────────────────────────────────


async def set_provider_key(provider: str, key_name: str, value: str) -> None:
    """Encrypt + store a value. UPSERTs on (provider, key_name).

    Empty `value` deletes the row entirely so the env-var fallback
    can resume. Treat "" as "I don't want this key configured".
    """
    from openvox.db import db_session
    from openvox.db.models import ProviderKey

    provider = provider.strip().lower()
    key_name = key_name.strip().lower()
    if not provider or not key_name:
        raise ValueError("provider and key_name must be non-empty")

    if not value:
        await delete_provider_key(provider, key_name)
        return

    encrypted = _fernet().encrypt(value.encode("utf-8")).decode("ascii")

    async with db_session() as s:
        # Try insert first; on duplicate-key, update. This is the
        # pattern that works on both SQLite and Postgres without
        # needing dialect-specific ON CONFLICT syntax.
        existing = await s.get(ProviderKey, (provider, key_name))
        if existing is None:
            try:
                s.add(ProviderKey(provider=provider, key_name=key_name, encrypted_value=encrypted))
            except IntegrityError:
                # Race condition — another writer just inserted. Re-fetch + update.
                existing = await s.get(ProviderKey, (provider, key_name))
                if existing is not None:
                    existing.encrypted_value = encrypted
        else:
            existing.encrypted_value = encrypted

    logger.info("provider_key set: %s.%s", provider, key_name)


async def get_provider_key(provider: str, key_name: str) -> str | None:
    """Look up a key in the encrypted store. Returns None if absent.

    Note: callers should usually go through `resolve_provider_key()`
    which checks env-var first. This raw lookup is for cases that
    NEED the stored value specifically (e.g., the admin endpoint
    that shows what's been configured).
    """
    from openvox.db import db_session
    from openvox.db.models import ProviderKey

    provider = provider.strip().lower()
    key_name = key_name.strip().lower()

    async with db_session() as s:
        row = await s.get(ProviderKey, (provider, key_name))
        if row is None:
            return None
        try:
            return _fernet().decrypt(row.encrypted_value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            # The machine key changed under us (file was deleted +
            # regenerated). All stored rows are unrecoverable.
            logger.error(
                "provider_key %s.%s failed to decrypt — machine key likely rotated. "
                "Re-enter the key via the dashboard wizard or set the env var.",
                provider, key_name,
            )
            return None


async def delete_provider_key(provider: str, key_name: str) -> None:
    """Remove a stored value. No-op if it doesn't exist."""
    from openvox.db import db_session
    from openvox.db.models import ProviderKey

    provider = provider.strip().lower()
    key_name = key_name.strip().lower()

    async with db_session() as s:
        row = await s.get(ProviderKey, (provider, key_name))
        if row is not None:
            await s.delete(row)
            logger.info("provider_key deleted: %s.%s", provider, key_name)


async def list_configured_providers() -> dict[str, list[str]]:
    """Return `{provider: [key_name, ...]}` for everything in the store.

    Used by the admin `/setup/status` endpoint to tell the dashboard
    which providers the user has already configured.
    """
    from openvox.db import db_session
    from openvox.db.models import ProviderKey

    out: dict[str, list[str]] = {}
    async with db_session() as s:
        result = await s.execute(select(ProviderKey))
        for row in result.scalars().all():
            out.setdefault(row.provider, []).append(row.key_name)
    return out


# ── Hybrid resolution (env first, store fallback) ──────────────────


async def resolve_provider_key(
    provider: str,
    key_name: str,
    env_var_name: str | None = None,
) -> str | None:
    """Return the effective value for a provider key.

    Resolution order:
      1. ``os.environ[env_var_name]`` if set + non-empty (matches the
         existing pydantic-settings behaviour, so Docker deployments
         pick up their `.env` keys unchanged).
      2. Encrypted store via ``get_provider_key()``.
      3. None.

    ``env_var_name`` defaults to the upper-snake-case form, e.g.
    ``resolve_provider_key("byteplus", "llm_api_key")`` looks for
    ``BYTEPLUS_LLM_API_KEY`` first.

    This helper is what provider modules should call going forward.
    They CAN still read ``get_settings().byteplus_llm_api_key``
    directly for legacy code-paths — the setting will be empty if
    the env var is unset, and the provider will fall through to its
    own "is_available() == False" path.
    """
    env_var = env_var_name or f"{provider.upper()}_{key_name.upper()}"
    env_val = os.environ.get(env_var, "").strip()
    if env_val:
        return env_val
    return await get_provider_key(provider, key_name)


# ── Setup-status helper ────────────────────────────────────────────


# The set of keys we consider "essential" for OpenVox to actually
# function. At least one LLM + one voice-pipeline key must be present
# (either via env or store) for `setup_complete` to be True.
_ESSENTIAL_LLM_KEYS = [
    ("byteplus", "llm_api_key"),
    ("openai", "api_key"),
    ("anthropic", "api_key"),
    ("gemini", "api_key"),
    ("deepseek", "api_key"),
]
_ESSENTIAL_VOICE_KEYS = [
    ("byteplus", "voice_api_key"),
    ("elevenlabs", "api_key"),
    ("openai", "api_key"),  # OpenAI key works for both LLM and TTS
]


async def setup_complete() -> dict[str, Any]:
    """Is OpenVox ready to run an agent end-to-end?

    Returns a dict the admin endpoint can serialize:

        {
            "complete": True,
            "have_llm":  True,
            "have_voice": True,
            "providers_configured": {"byteplus": ["llm_api_key", "voice_api_key"]},
        }

    A user with both an LLM key AND a voice key (anywhere) is set up.
    Missing either → the wizard should run.
    """
    have_llm = False
    for provider, key_name in _ESSENTIAL_LLM_KEYS:
        if await resolve_provider_key(provider, key_name):
            have_llm = True
            break

    have_voice = False
    for provider, key_name in _ESSENTIAL_VOICE_KEYS:
        if await resolve_provider_key(provider, key_name):
            have_voice = True
            break

    return {
        "complete": have_llm and have_voice,
        "have_llm": have_llm,
        "have_voice": have_voice,
        "providers_configured": await list_configured_providers(),
    }
