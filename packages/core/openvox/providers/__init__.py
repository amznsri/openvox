"""Provider abstractions: STT, TTS, LLM, RTC.

Every provider implements an `ABC` defined in `base.py`. The `registry`
module discovers and instantiates providers by ID. Built-in providers
are registered in `bootstrap.py` at startup; third-party packages can
register via the `openvox.providers` entry-point group.
"""

from openvox.providers.base import (
    AudioChunk,
    LLMMessage,
    LLMProvider,
    LLMResponseChunk,
    Provider,
    ProviderCapability,
    ProviderType,
    RTCProvider,
    STTConfig,
    STTProvider,
    STTResult,
    TTSConfig,
    TTSProvider,
)
from openvox.providers.registry import ProviderRegistry, get_registry

__all__ = [
    "AudioChunk",
    "LLMMessage",
    "LLMProvider",
    "LLMResponseChunk",
    "Provider",
    "ProviderCapability",
    "ProviderRegistry",
    "ProviderType",
    "RTCProvider",
    "STTConfig",
    "STTProvider",
    "STTResult",
    "TTSConfig",
    "TTSProvider",
    "get_registry",
]
