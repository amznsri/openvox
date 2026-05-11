"""Async client for the OpenVox voice WebSocket pipeline."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import websockets


@dataclass
class VoiceSessionOptions:
    agent_id: str | None = None
    system_prompt: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    stt_provider: str | None = None
    tts_provider: str | None = None
    voice_id: str | None = None
    voice_language: str | None = None
    sample_rate: int = 16000


class VoiceSession:
    """Bidirectional connection to the OpenVox WS pipeline."""

    def __init__(self, base_url: str, options: VoiceSessionOptions | None = None) -> None:
        self._url = base_url.replace("http", "ws", 1).rstrip("/") + "/ws/voice"
        self._opts = options or VoiceSessionOptions()
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def __aenter__(self) -> "VoiceSession":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def start(self) -> None:
        self._ws = await websockets.connect(self._url, max_size=16 * 1024 * 1024)
        await self._ws.send(
            json.dumps(
                {
                    "type": "start",
                    "agent_id": self._opts.agent_id,
                    "system_prompt": self._opts.system_prompt,
                    "llm_provider": self._opts.llm_provider,
                    "llm_model": self._opts.llm_model,
                    "stt_provider": self._opts.stt_provider,
                    "tts_provider": self._opts.tts_provider,
                    "voice_id": self._opts.voice_id,
                    "voice_language": self._opts.voice_language,
                    "sample_rate": self._opts.sample_rate,
                }
            )
        )

    async def send_pcm(self, pcm: bytes) -> None:
        assert self._ws is not None
        await self._ws.send(pcm)

    async def end(self) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "end"}))

    async def interrupt(self) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "interrupt"}))

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        assert self._ws is not None
        async for raw in self._ws:
            if isinstance(raw, str):
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    yield {"type": "error", "message": "bad json", "raw": raw}
            else:
                yield {"type": "audio", "chunk": raw}

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
