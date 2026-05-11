"""Cartesia TTS — Sonic streaming via HTTP."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from openvox.config import get_settings
from openvox.providers.base import (
    AudioChunk,
    ProviderCapability,
    TTSConfig,
    TTSProvider,
)


class CartesiaTTS(TTSProvider):
    id = "cartesia"
    display_name = "Cartesia Sonic"
    capabilities = {ProviderCapability.STREAMING}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.cartesia_api_key
        self._default_voice = s.cartesia_default_voice_id
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def warmup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=5.0),
                headers={
                    "X-API-Key": self._api_key,
                    "Cartesia-Version": "2024-06-10",
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
            raise RuntimeError("CARTESIA_API_KEY is not set")
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
            "model_id": "sonic-english",
            "transcript": full,
            "voice": {"mode": "id", "id": config.voice_id or self._default_voice},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": config.sample_rate or 24000,
            },
        }
        url = "https://api.cartesia.ai/tts/sse"
        async with self._client.stream("POST", url, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line.removeprefix("data:").strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "chunk" and obj.get("data"):
                    import base64
                    yield AudioChunk(
                        data=base64.b64decode(obj["data"]),
                        sample_rate=config.sample_rate or 24000,
                        encoding="pcm16",
                    )
