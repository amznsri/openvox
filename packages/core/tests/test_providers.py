"""Provider unit tests — the wizard-to-provider contract per provider.

Every provider class in ``packages/core/openvox/providers/`` shares
the same constructor pattern:

    def __init__(self):
        self._api_key = get_settings().<provider>_api_key
    def is_available(self):
        return bool(self._api_key)

If hydration doesn't run before the provider's ``__init__`` (bug
#78), the cached ``_api_key`` is empty and ``is_available()`` returns
False forever. Every wizard-flow feature then breaks.

These tests assert two contracts per provider:

  1. ``is_available()`` returns False with no key configured.
  2. After a wizard save + hydration, a freshly-constructed provider
     reads the key correctly and ``is_available()`` returns True.

Parameterised across the 5 providers most likely to be the user's
default choice: BytePlus TTS (the one the v0.1.7 bug surfaced on),
OpenAI LLM, Anthropic LLM, ElevenLabs TTS, Cartesia TTS. Adding a
new provider? Add it to the ``PROVIDERS`` list below; no per-test
code changes needed.

What these tests DELIBERATELY skip:
  * Real HTTP requests (mocked via respx in higher-level tests)
  * Audio quality, voice selection, streaming semantics — those are
    integration concerns, not the contract that broke in v0.1.7
  * Provider-specific request shaping (Anthropic vs OpenAI prompt
    format etc.) — covered indirectly by the eval suite

The point of THIS file is just: does the wizard-saved key flow
into the provider's runtime state at all?
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest


# ── Provider table ──────────────────────────────────────────────────


@dataclass
class ProviderSpec:
    """Everything a provider test needs in one row.

    ``import_path`` is a string we import lazily — direct imports
    in this module would pull in every provider's transitive deps
    even for tests that touch none of them.
    """

    id: str  # short label for pytest -v output
    import_path: str  # 'openvox.providers.byteplus.tts:BytePlusTTS'
    secret_provider: str  # what the wizard calls it
    secret_key_name: str  # what the wizard calls the key field
    env_var: str  # the env var settings hydrates from
    sample_key: str  # any non-empty string the provider will accept as "set"


PROVIDERS = [
    ProviderSpec(
        id="byteplus-tts",
        import_path="openvox.providers.byteplus.tts:BytePlusTTS",
        secret_provider="byteplus",
        secret_key_name="voice_api_key",
        env_var="BYTEPLUS_VOICE_API_KEY",
        sample_key="bp-tts-test",
    ),
    ProviderSpec(
        id="byteplus-llm",
        import_path="openvox.providers.byteplus.llm:BytePlusLLM",
        secret_provider="byteplus",
        secret_key_name="llm_api_key",
        env_var="BYTEPLUS_LLM_API_KEY",
        sample_key="bp-llm-test",
    ),
    ProviderSpec(
        id="anthropic-llm",
        import_path="openvox.providers.openai_compat.anthropic:AnthropicLLM",
        secret_provider="anthropic",
        secret_key_name="api_key",
        env_var="ANTHROPIC_API_KEY",
        sample_key="sk-ant-test",
    ),
    ProviderSpec(
        id="elevenlabs-tts",
        import_path="openvox.providers.openai_compat.elevenlabs_tts:ElevenLabsTTS",
        secret_provider="elevenlabs",
        secret_key_name="api_key",
        env_var="ELEVENLABS_API_KEY",
        sample_key="el-test",
    ),
    ProviderSpec(
        id="cartesia-tts",
        import_path="openvox.providers.openai_compat.cartesia_tts:CartesiaTTS",
        secret_provider="cartesia",
        secret_key_name="api_key",
        env_var="CARTESIA_API_KEY",
        sample_key="ct-test",
    ),
]


def _instantiate(spec: ProviderSpec):
    """Lazy import + construct."""
    mod_path, cls_name = spec.import_path.rsplit(":", 1)
    import importlib

    module = importlib.import_module(mod_path)
    cls = getattr(module, cls_name)
    return cls()


# ── Per-provider contract tests ────────────────────────────────────


@pytest.mark.parametrize("spec", PROVIDERS, ids=[s.id for s in PROVIDERS])
def test_provider_unavailable_with_no_key(
    spec: ProviderSpec, tmp_openvox_home: Path
) -> None:
    """No env var + no stored key → ``is_available()`` is False.

    Without this guarantee, a provider could silently default to
    None / sentinel keys and hit real APIs with garbage credentials.
    """
    provider = _instantiate(spec)
    assert provider.is_available() is False, (
        f"{spec.id} reported available with NO key configured — "
        f"would attempt real API calls with no credentials"
    )


@pytest.mark.parametrize("spec", PROVIDERS, ids=[s.id for s in PROVIDERS])
def test_provider_available_when_env_set(
    spec: ProviderSpec,
    tmp_openvox_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting the env var directly → provider sees it on construction.

    This covers the Docker / .env path (no wizard involved). Most
    production users hit this; the wizard is for non-tech CLI users.
    """
    monkeypatch.setenv(spec.env_var, spec.sample_key)
    # Settings is @lru_cache'd; bust it so the new env value is seen.
    from openvox.config import get_settings

    get_settings.cache_clear()

    provider = _instantiate(spec)
    assert provider.is_available() is True
    # Direct attribute check too — the constructor pattern is to cache
    # the key in self._api_key, and bug #78 was about that cache.
    assert provider._api_key == spec.sample_key


@pytest.mark.parametrize("spec", PROVIDERS, ids=[s.id for s in PROVIDERS])
async def test_provider_available_after_wizard_save_and_hydrate(
    spec: ProviderSpec,
    isolated_db: Path,
) -> None:
    """The end-to-end wizard contract:

      1. Wizard writes key to encrypted store.
      2. App restarts → lifespan calls ``_hydrate_secrets_into_env``.
      3. ``register_builtins()`` instantiates the provider.
      4. Provider reads ``settings.<key>``, ``is_available()`` is True.

    This is the path that broke for ALL providers in v0.1.7. Tests
    the same flow at the provider level (the
    ``test_secrets_hydration.py`` tests cover the bridge layer).
    """
    from openvox import secrets
    from openvox.api.app import _hydrate_secrets_into_env

    # 1. Wizard save
    await secrets.set_provider_key(
        spec.secret_provider, spec.secret_key_name, spec.sample_key
    )

    # 2. App startup runs hydration (which busts settings cache).
    await _hydrate_secrets_into_env()

    # 3. register_builtins instantiates the provider (we simulate this
    #    by direct construction — same path internally).
    provider = _instantiate(spec)

    # 4. Provider must see the key.
    assert provider.is_available() is True, (
        f"{spec.id} did NOT see wizard-saved key after hydration. "
        f"This is the v0.1.7 bug — DO NOT regress."
    )
    assert provider._api_key == spec.sample_key
