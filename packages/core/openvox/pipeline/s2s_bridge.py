"""S2S → VoiceSession adapter (Phase 3 PR-B, v0.2.24).

Bridges an :class:`~openvox.providers.base.S2SSession` to the same
``push_audio`` / ``end_audio`` / ``interrupt`` / ``run()`` surface that
:class:`openvox.pipeline.orchestrator.VoiceSession` exposes. That lets
``api/ws/voice.py`` choose between pipeline and S2S modes WITHOUT
branching on session type — it just builds whichever, calls the same
methods, and forwards the same ``TurnEvent`` stream.

Why a wrapper rather than two parallel WS routes:
  - Keeps the dashboard's single `/ws/voice` endpoint.
  - One persistence path for ``DBSession`` + ``Transcript`` rows.
  - One metrics shape (turn_count, first_token_ms, tts_chars, …).
  - When a future S2S vendor (Gemini Live, Anthropic) lands, it
    plugs into the same registry slot and the orchestrator doesn't
    care.

Event-protocol translation
==========================

S2SEvent.kind            → TurnEvent.kind
─────────────────────────  ───────────────────────────────────────────
user_partial             → user_partial
user_final               → user_final
assistant_text           → assistant_token   (delta concat in caller)
assistant_audio          → assistant_audio   (raw PCM16 forwarded)
tool_call                → skill_call + (we invoke) + skill_result
speech_started           → interrupt
response_done            → assistant_done
error                    → error

The orchestrator's ``assistant_text`` is delta-shaped (each event
carries one token / word fragment). The dashboard accumulates these
into a single bubble client-side — same UX as pipeline mode.

Audio sample rates
==================

The dashboard mic capture pushes 16 kHz PCM16 (the same rate STT
providers want). OpenAI Realtime expects 24 kHz PCM16. We resample
upstream (16 → 24 kHz) on each chunk. Realtime returns 24 kHz which
matches the dashboard's playback queue exactly — no resample needed
on the return path. If/when a future S2S provider wants a different
input rate, the bridge takes a ``target_sample_rate`` argument.

Tool calling
============

S2S providers expose tool-calling natively. When a ``tool_call``
event arrives, the bridge:
  1. Yields a ``skill_call`` TurnEvent so the dashboard renders the
     orange "→ skill_id(args)" line.
  2. Runs the skill via the same ``SkillRunner`` the pipeline uses —
     so MCP-bridged tools + built-in skills both work.
  3. Yields a ``skill_result`` TurnEvent for the orange "← output"
     line.
  4. Calls ``session.submit_tool_result(call_id, output)`` so the
     S2S server can produce its response.

Failover
========

If the S2S provider fails to connect (network, invalid key, model
not available), the bridge raises at ``__aenter__`` time. The
caller (``api/ws/voice.py::_build_session``) catches and falls
back to building a pipeline ``VoiceSession`` instead. The user
gets pipeline mode + a one-line "S2S unavailable" notice via the
``error`` event.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openvox.providers.base import S2SConfig, S2SEvent, S2SProvider, S2SSession
from openvox.skills.runner import SkillRunner

logger = logging.getLogger(__name__)


# Match the orchestrator's TurnEvent layout (cannot import directly
# without a circular dep, so a faithful re-declaration). When the
# bridge yields these, the WS forwarder's ``_event_to_json`` handles
# them identically.
@dataclass
class TurnEvent:
    kind: str
    text: str = ""
    audio: bytes = b""
    sample_rate: int = 24000
    encoding: str = "pcm16"
    data: dict[str, Any] | None = None


# ── Resampling helper (16 kHz → 24 kHz, PCM16) ────────────────────


def _upsample_pcm16_16k_to_24k(src: bytes) -> bytes:
    """Linear-interpolation resample 16 kHz PCM16 → 24 kHz PCM16.

    Voice STT-quality audio doesn't need a polyphase filter — the
    perceptual difference vs. a high-order resampler is negligible
    at this rate ratio (1.5×). We keep the dependency footprint at
    just ``numpy`` (already in the base install) rather than pulling
    ``scipy`` or ``samplerate`` back in.

    The implementation:
      - Decode PCM16 little-endian bytes to int16 samples.
      - Build a new index axis 1.5× as long and linearly interpolate.
      - Re-encode to PCM16 bytes.

    For empty input, returns empty bytes (so the caller can pipe
    silence frames through without special-casing).
    """
    if not src:
        return b""
    import numpy as np

    samples = np.frombuffer(src, dtype="<i2")
    if samples.size == 0:
        return b""
    n_out = int(samples.size * 1.5)
    # `np.linspace` of length n_out spans 0..samples.size-1 inclusive,
    # then np.interp linearly interpolates. Cast back to int16
    # explicitly — numpy.float64 → int16 conversion would truncate
    # the fractional part and silently clip on overflow, which is
    # what we want for the small interpolated values.
    x_old = np.arange(samples.size)
    x_new = np.linspace(0, samples.size - 1, n_out)
    interp = np.interp(x_new, x_old, samples.astype(np.float32))
    out = np.clip(interp, -32768, 32767).astype("<i2")
    return out.tobytes()


# ── The bridge itself ─────────────────────────────────────────────


class S2SBridge:
    """VoiceSession-shaped wrapper around an :class:`S2SProvider`.

    Lifecycle:
      bridge = S2SBridge(provider=..., config=..., skill_runner=...)
      async with bridge:
          await bridge.push_audio(chunk)
          ...
          async for ev in bridge.run():
              ...

    The ``run()`` generator owns the consume-events-and-route-tool-
    calls loop. It exits cleanly on ``response_done`` followed by
    no further activity, OR on an ``error`` event, OR when the
    underlying session is closed.
    """

    def __init__(
        self,
        *,
        provider: S2SProvider,
        config: S2SConfig,
        skill_runner: SkillRunner | None = None,
        input_sample_rate: int = 16000,
    ) -> None:
        self._provider = provider
        self._config = config
        self._skills = skill_runner or SkillRunner(skill_ids=[])
        self._input_sample_rate = input_sample_rate
        # Filled in ``__aenter__``.
        self._session: S2SSession | None = None
        self._closed = False
        # Tool-call bookkeeping. The S2S provider gives us the
        # function name + args + a call_id on tool_call; we hold the
        # call_id long enough to thread it back through
        # ``submit_tool_result``.

    # ── Lifecycle ────────────────────────────────────────────────

    async def __aenter__(self) -> "S2SBridge":
        # ``connect()`` returns an UNOPENED S2SSession. The session's
        # ``__aenter__`` is what actually opens the WS — we want
        # bridge.__aenter__ to mirror that, so exception during
        # connect surfaces here and the WS voice route's failover
        # branch can catch + revert to pipeline mode.
        session = self._provider.connect(self._config)
        await session.__aenter__()
        self._session = session
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._closed = True
        if self._session is not None:
            try:
                await self._session.__aexit__(exc_type, exc_val, exc_tb)
            except Exception:
                logger.exception("S2S session teardown error")
            self._session = None

    # ── Outbound (WS → S2S) ──────────────────────────────────────

    async def push_audio(self, chunk) -> None:
        """Forward a microphone PCM16 chunk to the S2S session.

        ``chunk`` is an :class:`AudioChunk` from
        ``openvox.providers.base``. We resample to 24 kHz when the
        source is 16 kHz; other source rates fall through unchanged
        (assumed already 24 kHz from a future caller that does its
        own resampling, OR the provider doesn't actually care).
        """
        if self._session is None or self._closed:
            return
        data = chunk.data
        # Today only 16 → 24 kHz is exercised. If a future provider
        # wants 48 kHz, this branch grows; we'd extract a small
        # `resample` helper module.
        if chunk.sample_rate == 16000 and self._input_sample_rate != 24000:
            data = _upsample_pcm16_16k_to_24k(data)
        await self._session.push_audio(data)

    async def end_audio(self) -> None:
        """Signal end-of-input. Server VAD usually finalises before
        this is called; explicit commit kicks Realtime when VAD is
        disabled via the config."""
        if self._session is None:
            return
        try:
            await self._session.commit_audio()
        except Exception:
            logger.debug("commit_audio failed (provider may not support it)")

    def interrupt(self) -> None:
        """Synchronous interrupt — matches ``VoiceSession.interrupt``
        contract. The S2S session expects an async cancel; we
        schedule it as a background task and return immediately so
        the caller (WS route's ``interrupt`` ctrl message handler)
        doesn't have to ``await``.
        """
        if self._session is None or self._closed:
            return
        asyncio.create_task(self._session.interrupt())

    # ── Inbound (S2S → WS) ───────────────────────────────────────

    async def run(self) -> AsyncIterator[TurnEvent]:
        """Drain S2S events, translate to TurnEvent, route tool calls.

        Pulls events from the session indefinitely until either the
        session closes or an unrecoverable error arrives. Tool calls
        are handled inline (no out-of-band channel) — we invoke the
        skill, submit the result back, and continue the iteration.
        """
        assert self._session is not None, "S2SBridge.run() called before __aenter__"

        async for ev in self._session.events():
            translated = await self._translate(ev)
            for out in translated:
                yield out
            if ev.kind == "error":
                # Stop iteration on hard errors — caller's `finally`
                # will tear down the session.
                return

    async def _translate(self, ev: S2SEvent) -> list[TurnEvent]:
        """Map one S2SEvent to zero-or-more TurnEvents.

        Tool calls expand to TWO events (skill_call + skill_result)
        because the skill runs inline here. Everything else is a
        straight 1:1 translation.
        """
        kind = ev.kind
        if kind == "user_partial":
            return [TurnEvent(kind="user_partial", text=ev.text or "")]
        if kind == "user_final":
            return [TurnEvent(kind="user_final", text=ev.text or "")]
        if kind == "assistant_text":
            return [TurnEvent(kind="assistant_token", text=ev.text or "")]
        if kind == "assistant_audio":
            return [
                TurnEvent(
                    kind="assistant_audio",
                    audio=ev.audio or b"",
                    sample_rate=24000,
                    encoding="pcm16",
                )
            ]
        if kind == "speech_started":
            # Maps to the dashboard's "interrupt" affordance — TTS
            # playback queue stops, mic stays open.
            return [TurnEvent(kind="interrupt")]
        if kind == "response_done":
            # Terminal event per turn. Pipeline mode emits this
            # after the final assistant_token; S2S mode mirrors.
            return [TurnEvent(kind="assistant_done", text=ev.text or "")]
        if kind == "error":
            return [
                TurnEvent(
                    kind="error",
                    text=(ev.text or "S2S session error"),
                    data=ev.data,
                )
            ]
        if kind == "tool_call":
            # Run the skill, emit skill_call + skill_result, push
            # the result back into the S2S session.
            return await self._handle_tool_call(ev)
        # Unknown event kind — log + drop. Realtime's bookkeeping
        # events are already filtered out in the adapter, so any
        # surprise here is informational only.
        logger.debug("S2SBridge: unhandled event kind %r", kind)
        return []

    async def _handle_tool_call(self, ev: S2SEvent) -> list[TurnEvent]:
        """Invoke a skill, push the result back, yield UI events."""
        data = ev.data or {}
        skill_id = data.get("name") or ""
        args = data.get("arguments") or {}
        call_id = data.get("call_id") or ""
        if isinstance(args, str):
            # OpenAI Realtime returns arguments as a JSON STRING in
            # the `function_call_arguments.done` event. The S2S
            # adapter is supposed to parse it, but be defensive.
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}

        out: list[TurnEvent] = [
            TurnEvent(
                kind="skill_call",
                text=skill_id,
                data={"args": args, "call_id": call_id},
            )
        ]

        # Invoke. The SkillRunner already handles MCP-bridged skills
        # alongside built-ins.
        try:
            result = await self._skills.invoke(skill_id, args)
            output = (result or {}).get("output", result)
            error = (result or {}).get("error", "")
        except Exception as e:
            logger.exception("S2S tool call invoke failed: %s", skill_id)
            output = {"error": str(e)}
            error = str(e)

        # Submit back to the S2S server so it can produce its
        # response. We do this BEFORE yielding the skill_result so
        # the server has the data ready by the time the dashboard
        # finishes rendering the orange line.
        if self._session is not None and call_id:
            try:
                await self._session.submit_tool_result(call_id, output)
            except Exception:
                logger.exception("submit_tool_result failed for call_id=%s", call_id)

        out.append(
            TurnEvent(
                kind="skill_result",
                text=skill_id,
                data={"output": output, "error": error, "call_id": call_id},
            )
        )
        return out
