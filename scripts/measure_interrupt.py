"""Measure server-side VAD interrupt latency.

What it does:
    1. Loads the SileroVAD provider directly (no WS / no orchestrator).
    2. Synthesises a deterministic input stream: 1 s of silence, then
       speech, then silence again. The "speech" is white noise at
       roughly speech-band energy — enough for Silero to fire above
       threshold without needing real audio.
    3. Counts the wall-clock ms from "feed first speech sample" to
       "speech_start event observed".

This isolates the VAD latency itself (model inference + window
buffering + queue dispatch) from any WS / network / orchestrator
overhead. The end-to-end interrupt-during-TTS latency in a real call
will be this number + queue handoff time (~1–2 ms).

Run inside the core container:
    docker compose exec -T core python /app/openvox/../scripts/measure_interrupt.py

Or from host (with deps installed locally):
    python scripts/measure_interrupt.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

# Make `openvox` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "core"))

import numpy as np

from openvox.providers.base import AudioChunk
from openvox.providers.vad.base import VADConfig
from openvox.providers.vad.silero import SileroVAD


# 30 ms frames at 16 kHz — what the real audio path uses.
FRAME_SAMPLES = 480
FRAME_BYTES = FRAME_SAMPLES * 2
SAMPLE_RATE = 16000


def _silence_frame() -> bytes:
    return (np.zeros(FRAME_SAMPLES, dtype=np.int16)).tobytes()


def _speech_frame(t_offset: int) -> bytes:
    """Synthetic "speech-like" signal — sum of voice-band harmonics
    (200/400/800 Hz, ~5–7 Hz envelope modulation). Silero scores this
    at ~0.95 reliably; white noise scores ~0.4 and won't fire."""
    t = (np.arange(FRAME_SAMPLES) + t_offset) / SAMPLE_RATE
    sig = (
        np.sin(2 * np.pi * 200 * t)
        + 0.5 * np.sin(2 * np.pi * 400 * t)
        + 0.3 * np.sin(2 * np.pi * 800 * t)
    )
    env = 0.5 + 0.5 * np.sin(2 * np.pi * 7 * t)
    sig = sig * env * 0.5
    return (sig * 24000).astype(np.int16).tobytes()


async def _stream(speech_start_event: asyncio.Event, n_silence: int, n_speech: int) -> AsyncIterator[AudioChunk]:
    """Yield silence frames, set the event, yield speech frames.

    The event captures the wall-clock instant we *committed* the first
    speech sample to the queue — interrupt latency is measured from
    that moment.
    """
    for _ in range(n_silence):
        yield AudioChunk(data=_silence_frame(), sample_rate=SAMPLE_RATE)
        await asyncio.sleep(0.005)  # pace at ~realtime so we don't burst
    speech_start_event.set()
    speech_start_event.t0 = time.monotonic_ns()  # type: ignore[attr-defined]
    offset = 0
    for _ in range(n_speech):
        yield AudioChunk(data=_speech_frame(offset), sample_rate=SAMPLE_RATE)
        offset += FRAME_SAMPLES
        await asyncio.sleep(0.005)


async def measure_once(vad: SileroVAD) -> float | None:
    """Return latency in ms, or None if VAD never fired."""
    fire = asyncio.Event()
    cfg = VADConfig(threshold=0.5, min_speech_frames=2, min_silence_frames=10)

    async for ev in vad.detect_stream(_stream(fire, 30, 60), cfg):  # 30 silence + 60 speech
        if ev.kind == "speech_start":
            now = time.monotonic_ns()
            return (now - fire.t0) / 1_000_000  # type: ignore[attr-defined]
    return None


async def main() -> int:
    vad = SileroVAD()
    if not vad.is_available():
        print("ERROR: SileroVAD reports unavailable. Install silero-vad + torch.")
        return 2
    await vad.warmup()

    samples: list[float] = []
    runs = 20
    print(f"Running {runs} samples through SileroVAD...")
    for i in range(runs):
        latency = await measure_once(vad)
        if latency is None:
            print(f"  run {i+1}: VAD never fired (unexpected)")
            continue
        samples.append(latency)
        print(f"  run {i+1}: {latency:.1f} ms")

    if not samples:
        print("ERROR: no successful detections")
        return 3

    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
    mean = statistics.fmean(samples)
    print()
    print("Summary:")
    print(f"  P50:  {p50:6.1f} ms")
    print(f"  Mean: {mean:6.1f} ms")
    print(f"  P95:  {p95:6.1f} ms")
    print(f"  Min:  {min(samples):6.1f} ms")
    print(f"  Max:  {max(samples):6.1f} ms")
    print()
    # Acceptance from the Session 8 plan.
    if p50 < 100 and p95 < 150:
        print("✅ PASS — under target (P50 <100 ms, P95 <150 ms).")
        return 0
    else:
        print("⚠️  WARN — exceeds target. Investigate model load / CPU.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
