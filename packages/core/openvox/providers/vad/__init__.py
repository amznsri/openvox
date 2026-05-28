"""Voice Activity Detection providers.

VAD runs in parallel with STT to detect speech onset/offset faster than
the STT partial pipeline can — usually sub-100 ms. The orchestrator
uses these events to fire `interrupt()` mid-TTS-playback, giving the
agent natural turn-taking behaviour without waiting for STT to confirm
"yes, that was a real word".

Default implementation: Silero VAD (ONNX, CPU-bound, ~10 ms per frame).
"""

from openvox.providers.vad.base import VADConfig, VADEvent, VADProvider

__all__ = ["VADProvider", "VADConfig", "VADEvent"]
