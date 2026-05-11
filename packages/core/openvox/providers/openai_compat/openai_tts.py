"""OpenAI TTS (`tts-1` / `tts-1-hd`) — streaming HTTP."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from openvox.config import get_settings
from openvox.providers.base import (
    AudioChunk,
    ProviderCapability,
    TTSConfig,
    TTSProvider,
)


class OpenAITTS(TTSProvider):
    id = "openai"
    display_name = "OpenAI TTS"
    capabilities = {ProviderCapability.STREAMING}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.openai_api_key
        self._default_voice = s.openai_tts_default_voice
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def warmup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=5.0),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def synthesize_stream(
        self, text: str | AsyncIterator[str], config: TTSConfig
    ) -> AsyncIterator[AudioChunk]:
        if not self.is_available():
            raise RuntimeError("OPENAI_API_KEY is not set")
        await self.warmup()
        assert self._client is not None

        if isinstance(text, str):
            full = text
        else:
            parts: list[str] = []
            async for piece in text:
                parts.append(piece)
            full = "".join(parts)

        body = {
            "model": "tts-1",
            "voice": config.voice_id or self._default_voice,
            "input": full,
            "response_format": "mp3" if config.encoding != "pcm16" else "pcm",
        }
        url = "https://api.openai.com/v1/audio/speech"
        async with self._client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(8192):
                if chunk:
                    yield AudioChunk(
                        data=chunk,
                        sample_rate=24000,
                        encoding="mp3" if config.encoding != "pcm16" else "pcm16",
                    )
