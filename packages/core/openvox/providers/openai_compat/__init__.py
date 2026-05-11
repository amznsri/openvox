"""OpenAI-compatible LLM clients — OpenAI, DeepSeek, plus the Anthropic /
Gemini adapters that translate to OpenAI's message shape internally.

Most providers we support speak the OpenAI Chat Completions wire format
or a near-identical variant, so this module factors out the transport.
"""

from openvox.providers.openai_compat.anthropic import AnthropicLLM
from openvox.providers.openai_compat.deepseek import DeepSeekLLM
from openvox.providers.openai_compat.elevenlabs_tts import ElevenLabsTTS
from openvox.providers.openai_compat.gemini import GeminiLLM
from openvox.providers.openai_compat.openai_llm import OpenAILLM
from openvox.providers.openai_compat.openai_tts import OpenAITTS
from openvox.providers.openai_compat.deepgram_stt import DeepgramSTT
from openvox.providers.openai_compat.assemblyai_stt import AssemblyAISTT
from openvox.providers.openai_compat.whisper_stt import WhisperSTT
from openvox.providers.openai_compat.cartesia_tts import CartesiaTTS

__all__ = [
    "AnthropicLLM",
    "AssemblyAISTT",
    "CartesiaTTS",
    "DeepSeekLLM",
    "DeepgramSTT",
    "ElevenLabsTTS",
    "GeminiLLM",
    "OpenAILLM",
    "OpenAITTS",
    "WhisperSTT",
]
