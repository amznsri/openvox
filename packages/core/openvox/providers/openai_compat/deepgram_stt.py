"""Deepgram streaming STT (websocket)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import websockets

from openvox.config import get_settings
from openvox.providers.base import (
    AudioChunk,
    ProviderCapability,
    STTConfig,
    STTProvider,
    STTResult,
)


class DeepgramSTT(STTProvider):
    id = "deepgram"
    display_name = "Deepgram"
    capabilities = {ProviderCapability.STREAMING, ProviderCapability.LANGUAGE_DETECT}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.deepgram_api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def transcribe_stream(
        self, audio: AsyncIterator[AudioChunk], config: STTConfig
    ) -> AsyncIterator[STTResult]:
        if not self.is_available():
            raise RuntimeError("DEEPGRAM_API_KEY is not set")

        params = (
            f"encoding=linear16&sample_rate={config.sample_rate}"
            f"&channels=1&interim_results={'true' if config.interim_results else 'false'}"
            f"&language={config.language}&model=nova-3&smart_format=true"
        )
        url = f"wss://api.deepgram.com/v1/listen?{params}"
        headers = {"Authorization": f"Token {self._api_key}"}

        try:
            ws = await websockets.connect(url, additional_headers=headers, max_size=16 * 1024 * 1024)
        except TypeError:
            ws = await websockets.connect(url, extra_headers=headers, max_size=16 * 1024 * 1024)  # type: ignore[arg-type]

        async with ws:
            send_done = asyncio.Event()

            async def pump() -> None:
                async for c in audio:
                    if c.data:
                        await ws.send(c.data)
                # signal end
                await ws.send(json.dumps({"type": "CloseStream"}))
                send_done.set()

            t = asyncio.create_task(pump())
            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        if send_done.is_set():
                            return
                        continue
                    if isinstance(raw, bytes):
                        continue
                    obj = json.loads(raw)
                    if obj.get("type") != "Results":
                        continue
                    alt = (obj.get("channel") or {}).get("alternatives", [{}])[0]
                    text = alt.get("transcript") or ""
                    is_final = bool(obj.get("is_final"))
                    if text:
                        yield STTResult(
                            text=text,
                            is_final=is_final,
                            confidence=float(alt.get("confidence") or 0.0),
                            language=config.language,
                            raw=obj,
                        )
            finally:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
