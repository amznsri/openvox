"""Abstract base classes + dataclasses for all providers.

Design principles:
  - All streaming methods are async generators yielding small payloads.
  - All providers are constructed *lazily* by the registry — no network
    calls in `__init__`.
  - A provider may declare itself unavailable (no API key) via
    `is_available()`; the router skips unavailable providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class ProviderType(str, Enum):
    STT = "stt"
    TTS = "tts"
    LLM = "llm"
    RTC = "rtc"
    VAD = "vad"
    S2S = "s2s"  # speech-to-speech
    TRANSLATE = "translate"


class ProviderCapability(str, Enum):
    STREAMING = "streaming"
    INTERRUPTION = "interruption"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"
    BIDIRECTIONAL = "bidirectional"
    LANGUAGE_DETECT = "language_detect"


# ────────────────────────────────────────────────────────────────────
# Audio
# ────────────────────────────────────────────────────────────────────


@dataclass
class AudioChunk:
    """A frame of PCM audio (or codec-encoded bytes)."""

    data: bytes
    sample_rate: int = 16000
    channels: int = 1
    encoding: Literal["pcm16", "pcm_s16le", "opus", "mulaw", "alaw"] = "pcm16"
    is_final: bool = False
    timestamp_ms: int = 0


# ────────────────────────────────────────────────────────────────────
# STT
# ────────────────────────────────────────────────────────────────────


@dataclass
class STTConfig:
    sample_rate: int = 16000
    language: str = "en-US"
    interim_results: bool = True
    vad_enabled: bool = True
    diarization: bool = False


@dataclass
class STTResult:
    text: str
    is_final: bool
    confidence: float = 0.0
    language: str = ""
    started_ms: int = 0
    ended_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# TTS
# ────────────────────────────────────────────────────────────────────


@dataclass
class TTSConfig:
    voice_id: str = ""
    language: str = "en-US"
    speed: float = 1.0
    sample_rate: int = 24000
    encoding: Literal["pcm16", "mp3", "opus", "wav"] = "pcm16"


# ────────────────────────────────────────────────────────────────────
# LLM
# ────────────────────────────────────────────────────────────────────


@dataclass
class LLMMessage:
    role: Literal["system", "user", "assistant", "tool"]
    # `content` is usually a string, but for vision/multimodal turns it
    # may be a list of OpenAI-compatible content parts:
    #   [{"type": "text", "text": "..."},
    #    {"type": "image_url", "image_url": {"url": "..."}}]
    content: str | list[dict[str, Any]]
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class LLMResponseChunk:
    delta: str = ""
    role: str = "assistant"
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    # Provider-reported token usage. OpenAI-compatible APIs (incl. Ark)
    # emit `{"usage": {"prompt_tokens": N, "completion_tokens": N,
    # "total_tokens": N}}` on the FINAL chunk when the request includes
    # `stream_options.include_usage = true`. Older proxies sometimes
    # send it mid-stream — accept whatever we get, last-write-wins on
    # the orchestrator side.
    usage: dict[str, int] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMConfig:
    model: str
    temperature: float = 0.7
    max_tokens: int = 2048
    stream: bool = True
    tools: list[dict[str, Any]] | None = None
    response_format: dict[str, Any] | None = None


# ────────────────────────────────────────────────────────────────────
# Provider base class
# ────────────────────────────────────────────────────────────────────


class Provider(ABC):
    """Common shape for all providers."""

    id: str
    display_name: str
    type: ProviderType
    capabilities: set[ProviderCapability] = set()

    @abstractmethod
    def is_available(self) -> bool:
        """Return True iff credentials / runtime are configured."""

    async def warmup(self) -> None:
        """Optional pre-flight check / connection pool setup."""
        return None

    async def close(self) -> None:
        """Release any held resources (HTTP clients, sockets)."""
        return None


class STTProvider(Provider):
    type = ProviderType.STT

    @abstractmethod
    async def transcribe_stream(
        self, audio: AsyncIterator[AudioChunk], config: STTConfig
    ) -> AsyncIterator[STTResult]: ...

    async def transcribe_file(self, audio_bytes: bytes, config: STTConfig) -> STTResult:
        """Transcribe a complete audio file. Default: collapse the stream."""

        async def _one() -> AsyncIterator[AudioChunk]:
            yield AudioChunk(data=audio_bytes, sample_rate=config.sample_rate, is_final=True)

        text_parts: list[str] = []
        last: STTResult | None = None
        async for r in self.transcribe_stream(_one(), config):
            if r.is_final:
                text_parts.append(r.text)
                last = r
        return STTResult(
            text=" ".join(text_parts).strip(),
            is_final=True,
            confidence=last.confidence if last else 0.0,
            language=last.language if last else config.language,
        )


class TTSProvider(Provider):
    type = ProviderType.TTS

    @abstractmethod
    async def synthesize_stream(
        self, text: str | AsyncIterator[str], config: TTSConfig
    ) -> AsyncIterator[AudioChunk]: ...

    async def synthesize(self, text: str, config: TTSConfig) -> bytes:
        """Synthesize all of `text` and return concatenated audio bytes."""
        chunks: list[bytes] = []
        async for c in self.synthesize_stream(text, config):
            chunks.append(c.data)
        return b"".join(chunks)


class LLMProvider(Provider):
    type = ProviderType.LLM

    @abstractmethod
    async def chat_stream(
        self, messages: list[LLMMessage], config: LLMConfig
    ) -> AsyncIterator[LLMResponseChunk]: ...

    async def chat(self, messages: list[LLMMessage], config: LLMConfig) -> str:
        cfg = LLMConfig(**{**config.__dict__, "stream": False})
        out: list[str] = []
        async for chunk in self.chat_stream(messages, cfg):
            if chunk.delta:
                out.append(chunk.delta)
        return "".join(out)


class RTCProvider(Provider):
    """Real-time communication provider — issues join tokens, manages rooms."""

    type = ProviderType.RTC

    @abstractmethod
    async def issue_token(
        self, room_id: str, user_id: str, role: Literal["publisher", "subscriber", "host"] = "publisher"
    ) -> dict[str, Any]:
        """Return everything the client needs to join the room."""

    @abstractmethod
    async def create_room(self, room_id: str, **kwargs: Any) -> dict[str, Any]: ...
