"""AssemblyAI Real-Time Streaming STT."""

from __future__ import annotations

import asyncio
import base64
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


class AssemblyAISTT(STTProvider):
    id = "assemblyai"
    display_name = "AssemblyAI"
    capabilities = {ProviderCapability.STREAMING}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.assemblyai_api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def transcribe_stream(
        self, audio: AsyncIterator[AudioChunk], config: STTConfig
    ) -> AsyncIterator[STTResult]:
        if not self.is_available():
            raise RuntimeError("ASSEMBLYAI_API_KEY is not set")

        url = f"wss://api.assemblyai.com/v2/realtime/ws?sample_rate={config.sample_rate}"
        headers = {"Authorization": self._api_key}

        try:
            ws = await websockets.connect(url, additional_headers=headers, max_size=16 * 1024 * 1024)
        except TypeError:
            ws = await websockets.connect(url, extra_headers=headers, max_size=16 * 1024 * 1024)  # type: ignore[arg-type]

        async with ws:
            send_done = asyncio.Event()

            async def pump() -> None:
                async for c in audio:
                    if c.data:
                        await ws.send(json.dumps({"audio_data": base64.b64encode(c.data).decode()}))
                await ws.send(json.dumps({"terminate_session": True}))
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
                    text = obj.get("text") or ""
                    msg_type = obj.get("message_type")
                    if not text:
                        continue
                    yield STTResult(
                        text=text,
                        is_final=msg_type == "FinalTranscript",
                        confidence=float(obj.get("confidence") or 0.0),
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
