"""Register all built-in providers with the registry."""

from __future__ import annotations

import logging

from openvox.providers.byteplus import BytePlusLLM, BytePlusRTC, BytePlusSTT, BytePlusTTS
from openvox.providers.openai_compat import (
    AnthropicLLM,
    AssemblyAISTT,
    CartesiaTTS,
    DeepgramSTT,
    DeepSeekLLM,
    ElevenLabsTTS,
    GeminiLLM,
    OpenAILLM,
    OpenAITTS,
    WhisperSTT,
)
from openvox.providers.registry import get_registry
from openvox.providers.vad.silero import SileroVAD

logger = logging.getLogger(__name__)


def register_builtins() -> None:
    reg = get_registry()
    for cls in (
        # LLM
        BytePlusLLM,
        OpenAILLM,
        AnthropicLLM,
        GeminiLLM,
        DeepSeekLLM,
        # STT
        BytePlusSTT,
        DeepgramSTT,
        AssemblyAISTT,
        WhisperSTT,
        # TTS
        BytePlusTTS,
        ElevenLabsTTS,
        CartesiaTTS,
        OpenAITTS,
        # RTC
        BytePlusRTC,
        # VAD
        SileroVAD,
    ):
        reg.register(cls)
    reg.discover_entrypoints()
    available = sum(1 for p in reg.list() if p["available"])
    logger.info("registered %d built-in providers (%d available)", len(reg.list()), available)
