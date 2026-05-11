"""BytePlus Seed-Speech 2.0 — TTS via the unidirectional HTTP streaming endpoint.

Reference: https://docs.byteplus.com/en/docs/byteplusvoice/unidirectional_tts_http

Endpoint:
    POST https://voice.ap-southeast-1.bytepluses.com/api/v3/tts/unidirectional

Required headers (X-Api-Key auth — recommended):
    X-Api-Key:        <BYTEPLUS_VOICE_API_KEY>
    X-Api-Resource-Id: seed-tts-2.0
    X-Api-App-Key:     aGjiRDfUWi          # fixed service-id constant
    X-Api-Request-Id:  <uuid>
    Content-Type:      application/json
    Connection:        keep-alive

Body shape (subset — see docs for the full additions catalogue):
    {
      "user":   {"id": "<user-id>"},
      "req_params": {
        "text":     "<utterance>",
        "speaker":  "en_male_tim_uranus_bigtts",
        "audio_params": {
          "format":     "pcm" | "mp3" | "ogg_opus",
          "sample_rate": 8000 | 16000 | 22050 | 24000 | 32000 | 44100 | 48000,
          "speech_rate":  -50..100,    # 0 = 1x, 100 = 2x, -50 = 0.5x
          "loudness_rate": -50..100,   # volume
          "emotion":       "happy" | "angry" | …  (per-voice support varies)
        },
        "additions": "<json-encoded string>"  # see docs (post_process.pitch, etc.)
      }
    }

The response is a chunked HTTP stream of newline-delimited JSON lines:
    {"code": 0, "message": "", "data": "<base64 PCM>"}
    {"code": 0, "message": "", "data": "<base64 PCM>"}
    ...
    {"code": 20000000, "message": "ok", "data": null}
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator

import httpx

from openvox.config import get_settings
from openvox.utils.http import make_async_client
from openvox.providers.base import (
    AudioChunk,
    ProviderCapability,
    TTSConfig,
    TTSProvider,
)

_TTS_URL_TMPL = "https://{base}/api/v3/tts/unidirectional"
_RESOURCE_ID = "seed-tts-2.0"  # OpenVox targets Seed-Speech 2.0 only.
_APP_KEY = "aGjiRDfUWi"  # service identifier required by the HTTP endpoint


def _speed_to_rate(speed: float) -> int:
    rate = int((speed - 1.0) * 100)
    return max(-50, min(100, rate))


class BytePlusTTS(TTSProvider):
    id = "byteplus"
    display_name = "BytePlus Seed-Speech 2.0"
    capabilities = {ProviderCapability.STREAMING}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.byteplus_voice_api_key
        self._url = _TTS_URL_TMPL.format(base=s.byteplus_voice_base)
        self._default_voice = s.byteplus_tts_default_voice
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def warmup(self) -> None:
        if self._client is None:
            self._client = make_async_client(timeout=120.0)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def synthesize_stream(
        self, text: str | AsyncIterator[str], config: TTSConfig
    ) -> AsyncIterator[AudioChunk]:
        if not self.is_available():
            raise RuntimeError("BYTEPLUS_VOICE_API_KEY is not set")
        await self.warmup()
        assert self._client is not None

        # Collapse async-iterator text into a single string.
        if isinstance(text, str):
            full = text
        else:
            parts: list[str] = []
            async for piece in text:
                parts.append(piece)
            full = "".join(parts)

        sample_rate = config.sample_rate or 24000
        # Map our generic encoding labels to BytePlus's accepted values.
        if config.encoding == "pcm16":
            fmt = "pcm"
        elif config.encoding == "opus":
            fmt = "ogg_opus"
        elif config.encoding in ("mp3", "wav"):
            fmt = "mp3" if config.encoding == "wav" else config.encoding
        else:
            fmt = "pcm"

        speaker = config.voice_id or self._default_voice
        body = {
            "user": {"id": str(uuid.uuid4())},
            "req_params": {
                "text": full,
                "speaker": speaker,
                "audio_params": {
                    "format": fmt,
                    "sample_rate": sample_rate,
                    "speech_rate": _speed_to_rate(config.speed),
                },
            },
        }
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": _RESOURCE_ID,
            "X-Api-App-Key": _APP_KEY,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
            "Connection": "keep-alive",
        }

        async with self._client.stream("POST", self._url, headers=headers, json=body) as resp:
            resp.raise_for_status()

            def _emit(obj: dict[str, object]) -> AudioChunk | None:
                code = obj.get("code")
                # 0 = audio chunk; 20000000 = terminal "ok"; everything else
                # is a hard error per the docs.
                if isinstance(code, int) and code not in (0, 20000000):
                    raise RuntimeError(
                        f"BytePlus TTS error code={code} message={obj.get('message')!r}"
                    )
                audio_b64 = obj.get("data")
                if not audio_b64 or not isinstance(audio_b64, str):
                    return None
                return AudioChunk(
                    data=base64.b64decode(audio_b64),
                    sample_rate=sample_rate,
                    encoding="pcm16" if fmt == "pcm" else fmt,
                )

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    audio = _emit(obj)
                    if audio is not None:
                        yield audio
            if buffer.strip():
                try:
                    obj = json.loads(buffer)
                except json.JSONDecodeError:
                    obj = None
                if obj is not None:
                    audio = _emit(obj)
                    if audio is not None:
                        yield audio
