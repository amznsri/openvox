"""Whisper — uses OpenAI's audio.transcriptions endpoint by default;
optionally local inference if WHISPER_MODE=local (requires extra deps)."""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator

import httpx

from openvox.config import get_settings
from openvox.providers.base import (
    AudioChunk,
    ProviderCapability,
    STTConfig,
    STTProvider,
    STTResult,
)


def _frames_to_wav(frames: list[bytes], sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # int16
        w.setframerate(sample_rate)
        for f in frames:
            w.writeframes(f)
    return buf.getvalue()


class WhisperSTT(STTProvider):
    id = "whisper"
    display_name = "Whisper"
    capabilities = {ProviderCapability.LANGUAGE_DETECT}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.openai_api_key
        self._mode = s.whisper_mode

    def is_available(self) -> bool:
        return self._mode == "local" or bool(self._api_key)

    async def transcribe_stream(
        self, audio: AsyncIterator[AudioChunk], config: STTConfig
    ) -> AsyncIterator[STTResult]:
        # Whisper isn't truly streaming — we batch and submit at the end.
        frames: list[bytes] = []
        sample_rate = config.sample_rate
        async for c in audio:
            if c.data:
                frames.append(c.data)
                sample_rate = c.sample_rate or sample_rate

        if not frames:
            return

        wav_bytes = _frames_to_wav(frames, sample_rate)

        if self._mode == "local":
            # Optional dependency — kept lazy so the rest of the code
            # doesn't pay for a multi-hundred-MB model dependency.
            try:
                import whisper  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "WHISPER_MODE=local requires the optional [local-stt] extra: "
                    "pip install -e '.[local-stt]'"
                ) from e
            # offload to thread (whisper is synchronous)
            import asyncio
            import tempfile

            def _run() -> str:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
                    t.write(wav_bytes)
                    path = t.name
                model = whisper.load_model("base")
                return model.transcribe(path).get("text", "")

            text = await asyncio.to_thread(_run)
            yield STTResult(text=text, is_final=True, language=config.language)
            return

        # API mode
        async with httpx.AsyncClient(timeout=60.0) as client:
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            data = {"model": "whisper-1", "language": config.language[:2]}
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files=files,
                data=data,
            )
            r.raise_for_status()
            obj = r.json()
            yield STTResult(text=obj.get("text", ""), is_final=True, language=config.language, raw=obj)
