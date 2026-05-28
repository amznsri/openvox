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


# ────────────────────────────────────────────────────────────────────
# S2S — Speech-to-Speech (Session 18 Phase 3)
# ────────────────────────────────────────────────────────────────────
#
# Replaces the STT → LLM → TTS pipeline with a single bidirectional
# WebSocket to a model that natively handles speech in/out. The
# headline win is first-audio latency: pipeline averages ~280 ms,
# OpenAI Realtime / Gemini Live / similar S2S models hit ~120 ms.
#
# Tradeoff: less provider portability — each S2S vendor has its own
# event protocol. The adapter layer normalises to ONE set of event
# kinds the orchestrator understands, so swapping providers from the
# dashboard doesn't require pipeline changes.
#
# Why a separate provider TYPE (not just an LLM + audio extension):
#   - S2S subsumes STT + LLM + TTS in one connection.
#   - Skills still work, but routed via the S2S session's own
#     tool-calling channel rather than the orchestrator's tool loop.
#   - Pricing is per-MINUTE, not per-token — completely different
#     cost-tracking shape.
#   - VAD is server-side — Realtime owns interrupt detection.
#
# A user picks S2S per-agent via `Agent.s2s_provider`. When that's
# set, the orchestrator's `_llm_turn` is bypassed entirely (Phase
# 3.3 in PLANNING_SESSION18.md).


@dataclass
class S2SConfig:
    """Per-session config the orchestrator passes to ``S2SProvider.connect``.

    Maps roughly to OpenAI Realtime's `session.update` event, but the
    adapter layer translates from this provider-agnostic shape into
    whatever the upstream API wants.
    """

    # Model id. Empty = use the adapter's own default. For OpenAI
    # Realtime this is e.g. `gpt-4o-realtime-preview` or
    # `gpt-4o-realtime-preview-2024-12-17`. The adapter resolves the
    # canonical default when empty.
    model: str = ""
    instructions: str = ""
    voice: str = "alloy"  # Realtime: alloy | echo | shimmer | etc.
    temperature: float = 0.8
    # PCM sample rates. Realtime expects 24 kHz input + output.
    # Adapter handles resampling from / to the orchestrator's
    # 16 kHz STT-side audio when needed.
    sample_rate_in: int = 24000
    sample_rate_out: int = 24000
    # Tool specs in OpenAI-compatible shape (`runner.tool_specs()`
    # returns this directly). Realtime's `session.update.tools`
    # accepts the same shape so we pass through.
    tools: list[dict[str, Any]] | None = None
    # Server-side VAD threshold (Realtime: 0.0-1.0; 0.5 is the
    # default). The orchestrator's existing barge-in pipeline is
    # bypassed in S2S mode — the server handles interrupt detection.
    vad_threshold: float = 0.5


@dataclass
class S2SEvent:
    """Normalised event emitted by the S2S session.

    The same five shapes regardless of which S2S provider is in play
    — the orchestrator only sees these. Adapters translate from
    their vendor's 30+ event types down to this canonical set.

    ``kind`` enumerates:
      - ``user_partial``    interim STT (server-side recogniser)
      - ``user_final``      finalised STT
      - ``assistant_text``  assistant transcript fragment
      - ``assistant_audio`` PCM bytes the orchestrator forwards
                            unchanged to the WS client
      - ``tool_call``       LLM wants to invoke a skill — payload is
                            ``{name, args, call_id}``; the
                            orchestrator runs the skill and replies
                            via ``S2SProvider.submit_tool_result``
      - ``speech_started``  user started speaking (server VAD) —
                            orchestrator uses this for barge-in
      - ``response_done``   complete turn finished, ready for next
      - ``error``           protocol error
    """

    kind: Literal[
        "user_partial",
        "user_final",
        "assistant_text",
        "assistant_audio",
        "tool_call",
        "speech_started",
        "response_done",
        "error",
    ]
    text: str = ""
    audio: bytes = b""
    sample_rate: int = 24000
    data: dict[str, Any] = field(default_factory=dict)


class S2SProvider(Provider):
    """Bidirectional speech-to-speech model — STT + LLM + TTS in one
    connection.

    Lifecycle:

        async with provider.connect(config) as session:
            # Push audio whenever you have it
            await session.push_audio(pcm_bytes)
            # Receive normalised events
            async for ev in session.events():
                ...
            # Reply to tool calls
            await session.submit_tool_result(call_id, output)
            # Optional explicit interrupt
            await session.interrupt()

    The session is itself an async context manager so the connection
    is torn down deterministically on session end (including on
    exceptions during ``events()`` iteration).
    """

    type = ProviderType.S2S

    @abstractmethod
    def connect(self, config: S2SConfig) -> "S2SSession":
        """Return a session handle. The session is an async-context
        manager — ``__aenter__`` actually opens the WebSocket."""


class S2SSession(ABC):
    """Per-call S2S handle. Subclassed by each adapter."""

    @abstractmethod
    async def __aenter__(self) -> "S2SSession": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...

    @abstractmethod
    async def push_audio(self, pcm_bytes: bytes) -> None:
        """Send a chunk of PCM 16-bit audio to the model.

        Sample rate is whatever the session's ``S2SConfig.sample_rate_in``
        promised — the adapter resamples upstream if the provider
        expects something else.
        """

    @abstractmethod
    async def commit_audio(self) -> None:
        """Tell the server the current input buffer is complete.

        Called when the user explicitly ends a turn (e.g., a "send
        now" button) or when the orchestrator's own VAD has fired.
        Many providers commit automatically via their server-side VAD;
        on those this is a no-op.
        """

    @abstractmethod
    async def submit_tool_result(self, call_id: str, output: Any) -> None:
        """Hand a skill's result back to the LLM so it can resume."""

    @abstractmethod
    async def interrupt(self) -> None:
        """Tell the provider to cancel its in-flight response.

        Used when the orchestrator's barge-in logic decides to cut
        off the assistant mid-sentence. Some providers (Realtime)
        require an explicit `response.cancel`; others auto-interrupt
        on next push_audio.
        """

    @abstractmethod
    async def events(self) -> AsyncIterator[S2SEvent]:
        """Stream of normalised events from the server.

        Single iterator per session — concurrent consumers will race.
        The orchestrator owns this loop and dispatches per event.kind.
        """
