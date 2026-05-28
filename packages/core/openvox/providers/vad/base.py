"""VAD provider base class + dataclasses.

VAD providers consume an async stream of `AudioChunk` (the same kind
the STT consumer sees) and emit `VADEvent`s describing speech onset
and offset. They are deliberately *passive* — they never block the
audio path; STT and VAD are parallel consumers of the same upstream.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

from openvox.providers.base import AudioChunk, Provider, ProviderType


@dataclass
class VADConfig:
    sample_rate: int = 16000
    # Probability threshold above which the model decides "this is speech".
    # Silero defaults to 0.5; 0.3 = more sensitive (catches quieter onset),
    # 0.7 = more conservative (fewer false positives).
    threshold: float = 0.5
    # How many consecutive non-speech frames before we declare end-of-speech.
    # 10 × 30 ms = 300 ms of silence to confirm the speaker stopped.
    min_silence_frames: int = 10
    # Minimum speech run before we trust the onset and emit an event.
    # 2 × 30 ms = 60 ms suppresses the occasional spurious blip.
    min_speech_frames: int = 2


@dataclass
class VADEvent:
    """Speech-onset / speech-offset event.

    `kind="speech_start"` fires the moment we cross from silence into
    confirmed speech; `kind="speech_end"` fires after `min_silence_frames`
    of quiet. `prob` is the latest frame's speech probability (0..1),
    useful for the dashboard to render a meter.
    """

    kind: Literal["speech_start", "speech_end"]
    timestamp_ms: int = 0
    prob: float = 0.0


class VADProvider(Provider):
    type = ProviderType.VAD

    @abstractmethod
    async def detect_stream(
        self, audio: AsyncIterator[AudioChunk], config: VADConfig
    ) -> AsyncIterator[VADEvent]:
        """Consume PCM frames, yield speech_start / speech_end events."""
