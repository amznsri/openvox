"""Silero VAD — local ONNX speech detector.

Why this provider exists:
    The existing interrupt path was client-driven — the dashboard mic
    component sent `{"type":"interrupt"}` when it detected audio. That
    costs at least one WS round-trip (~30 ms LAN, ~150 ms phone) and
    relies on the client doing energy detection. With Silero running
    server-side, we cut interrupt latency to ~30–60 ms regardless of
    network distance, and clients no longer need to do VAD themselves.

How it's wired:
    The orchestrator's audio queue gets tee'd: one fork to STT (slow but
    transcribes), one fork to VAD (fast but only detects voice/silence).
    A `speech_start` event mid-TTS-playback triggers `interrupt()`
    immediately, so the assistant stops talking before the user has
    even finished their first syllable.

Silero specifics:
    - Input: 16 kHz mono PCM s16le, in 512-sample windows (~32 ms).
    - Model is the official Silero ONNX (~1.8 MB), vendored by the
      `silero-vad` PyPI package — no network download at runtime.
    - CPU-bound at ~5–10 ms per window on a modern x86 / M-series.
      We run it via `asyncio.to_thread` so the event loop stays free.

Graceful degradation:
    If onnxruntime or silero-vad isn't installed, `is_available()` returns
    False, the orchestrator falls back to the client-driven interrupt
    path, and we log a single warning rather than crashing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

import numpy as np

from openvox.providers.base import AudioChunk
from openvox.providers.vad.base import VADConfig, VADEvent, VADProvider

logger = logging.getLogger(__name__)

# Silero expects exactly 512 samples per inference at 16 kHz (32 ms).
# Don't change this — the model file bakes in the window size.
_WINDOW_SAMPLES = 512
_WINDOW_BYTES = _WINDOW_SAMPLES * 2  # int16 = 2 bytes
_SAMPLE_RATE = 16000


class SileroVAD(VADProvider):
    id = "silero"
    display_name = "Silero VAD (local ONNX)"

    def __init__(self) -> None:
        self._model: object | None = None
        self._available: bool | None = None  # tri-state: unknown until first probe
        self._import_error: str = ""

    def is_available(self) -> bool:
        # Probe lazily so a broken install doesn't crash provider bootstrap.
        if self._available is not None:
            return self._available
        # Escape hatch for tiny-image deployments: skip the torch import
        # if the operator explicitly opted out.
        import os
        if os.environ.get("OPENVOX_DISABLE_SILERO", "").lower() in ("1", "true", "yes"):
            self._available = False
            self._import_error = "disabled via OPENVOX_DISABLE_SILERO"
            return False
        try:
            from silero_vad import load_silero_vad  # type: ignore
            # We default to the torch backend rather than ONNX because:
            #   - torch is already in our deps (silero-vad pulls it).
            #   - ONNX would add ~80 MB of onnxruntime for a 5 ms speed-up
            #     that's irrelevant in our budget (model is ~5–10 ms either way).
            #   - One fewer dep to keep CVE-patched.
            # Set OPENVOX_VAD_BACKEND=onnx if you've installed onnxruntime
            # separately and want the speed-up.
            import os
            use_onnx = os.environ.get("OPENVOX_VAD_BACKEND", "torch").lower() == "onnx"
            self._model = load_silero_vad(onnx=use_onnx)
            self._available = True
            logger.info(
                "silero-vad loaded successfully (%s backend)",
                "ONNX" if use_onnx else "torch",
            )
        except Exception as e:
            self._available = False
            self._import_error = repr(e)
            logger.warning(
                "silero-vad unavailable: %s — falling back to client-driven interrupts", e
            )
        return self._available

    async def warmup(self) -> None:
        # Force a single forward pass so the first real frame doesn't pay
        # JIT-compile / ONNX-graph-allocation latency (~150 ms cold).
        if not self.is_available():
            return
        silent = np.zeros(_WINDOW_SAMPLES, dtype=np.float32)
        await asyncio.to_thread(self._infer, silent)

    def _infer(self, samples: np.ndarray) -> float:
        """Synchronous inference — caller wraps in asyncio.to_thread."""
        if self._model is None:
            return 0.0
        # silero-vad's loaded model is a torch.jit module; calling it with a
        # tensor returns a 1-element tensor with the speech probability.
        import torch  # imported lazily so test environments don't pay for it

        with torch.no_grad():
            t = torch.from_numpy(samples)
            prob = self._model(t, _SAMPLE_RATE).item()  # type: ignore[operator]
        return float(prob)

    async def detect_stream(
        self, audio: AsyncIterator[AudioChunk], config: VADConfig
    ) -> AsyncIterator[VADEvent]:
        """Detect speech boundaries on a stream of PCM frames.

        Strategy: accumulate bytes until we have a full 512-sample window,
        run inference in a thread, and apply a small state machine:
          - We start in `silence` mode.
          - When prob > threshold for `min_speech_frames` consecutive
            windows → emit `speech_start`, switch to `speech` mode.
          - In `speech` mode, when prob ≤ threshold for
            `min_silence_frames` consecutive windows → emit `speech_end`.
        """
        if not self.is_available():
            return

        buf = bytearray()
        speech_run = 0
        silence_run = 0
        in_speech = False
        start_ts = time.monotonic_ns()

        async for chunk in audio:
            # Reject non-PCM or wrong-rate frames — we'd just produce
            # garbage if we tried to coerce them.
            if chunk.encoding not in ("pcm16", "pcm_s16le") or chunk.sample_rate != _SAMPLE_RATE:
                continue

            buf.extend(chunk.data)

            # Drain as many windows as we have.
            while len(buf) >= _WINDOW_BYTES:
                window = bytes(buf[:_WINDOW_BYTES])
                del buf[:_WINDOW_BYTES]

                samples = np.frombuffer(window, dtype=np.int16).astype(np.float32) / 32768.0
                prob = await asyncio.to_thread(self._infer, samples)
                now_ms = (time.monotonic_ns() - start_ts) // 1_000_000

                if prob >= config.threshold:
                    speech_run += 1
                    silence_run = 0
                    if not in_speech and speech_run >= config.min_speech_frames:
                        in_speech = True
                        yield VADEvent(kind="speech_start", timestamp_ms=now_ms, prob=prob)
                else:
                    silence_run += 1
                    speech_run = 0
                    if in_speech and silence_run >= config.min_silence_frames:
                        in_speech = False
                        yield VADEvent(kind="speech_end", timestamp_ms=now_ms, prob=prob)
