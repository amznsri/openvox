"""Twilio Media Streams ⇄ VoiceSession bridge.

Twilio Media Streams is the "Connect → Stream" path: when our TwiML
returns `<Connect><Stream url="wss://…/ws/twilio?agent_id=…"/></Connect>`,
Twilio opens a WebSocket to our endpoint and pipes the call's audio
through it. Frames flow both ways:

    Twilio → us:  base64-encoded **8 kHz mono μ-law** PCM, 20 ms (160 bytes
                  decoded). Sent as JSON `{"event":"media","media":{...}}`.
    us → Twilio:  same format, also base64 + 20 ms μ-law. The browser
                  pipeline uses 16 kHz PCM in and 24 kHz PCM out, so we
                  do two sample-rate conversions + μ-law (de)compression
                  on each side.

The protocol carries a small JSON control language too: `connected`,
`start` (gives us streamSid + callSid + from/to numbers), `media`,
`mark`, `stop`. The `clear` event we can *send* back will flush
Twilio's playback buffer instantly — exactly what we want on barge-in.

Why the conversion code is hand-rolled:
    `audioop` (stdlib) handles μ-law-PCM round-trips and built-in
    `ratecv` does fixed-point linear resampling. That's fine for a
    8↔16 kHz hop on PCM16 — no external deps needed. We tried scipy
    first; the audioop path is ~30 µs per 20 ms frame on the Mac,
    which is well below real-time budget.

Caveats:
    - Audio quality is limited by the 8 kHz phone codec. Don't expect
      the same listening experience as the browser path.
    - The VAD provider on the VoiceSession sees 16 kHz frames (after
      upsample), so existing Silero-based interrupt logic works as-is.
    - When the user interrupts, we send Twilio a `clear` event so the
      audio they've already received but not yet played gets dropped.
      Without this, the next ~500 ms of the assistant's audio buffer
      keeps playing even after we stop generating.
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openvox.db import db_session
from openvox.db.models import Agent
from openvox.db.models import Session as DBSession
from openvox.pipeline.orchestrator import SessionConfig, VoiceSession
from openvox.providers import ProviderType, get_registry
from openvox.providers.base import (
    AudioChunk,
    LLMProvider,
    STTConfig,
    STTProvider,
    TTSConfig,
    TTSProvider,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Twilio Media Streams sends 20-ms frames of 8 kHz mono μ-law.
_TWILIO_RATE = 8000
_PIPELINE_RATE = 16000
_TTS_OUTPUT_RATE_DEFAULT = 24000  # what most providers emit; we measure per chunk


# ── μ-law / sample-rate helpers ──────────────────────────────────────


def _mulaw_to_pcm16k(mu: bytes) -> bytes:
    """Twilio 8 kHz μ-law → 16 kHz PCM s16le for the orchestrator."""
    pcm8 = audioop.ulaw2lin(mu, 2)  # μ-law → PCM 16-bit
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, _TWILIO_RATE, _PIPELINE_RATE, None)
    return pcm16


class _MuLawDownsampler:
    """24 kHz / 22.05 kHz / 16 kHz PCM → 8 kHz μ-law for Twilio playback.

    Stateful because `ratecv` requires the previous filter state to
    avoid clicks between chunks. We get one of these per call session.
    """

    def __init__(self) -> None:
        self._state: object | None = None
        self._last_rate: int = 0

    def encode(self, pcm: bytes, src_rate: int) -> bytes:
        # Reset filter state when the source rate changes mid-call (it
        # really shouldn't, but TTS providers can swap sample rates if
        # the agent's voice changes between languages).
        if src_rate != self._last_rate:
            self._state = None
            self._last_rate = src_rate
        pcm8, self._state = audioop.ratecv(pcm, 2, 1, src_rate, _TWILIO_RATE, self._state)
        return audioop.lin2ulaw(pcm8, 2)


# ── WebSocket handler ────────────────────────────────────────────────


@router.websocket("/ws/twilio")
async def twilio_stream(ws: WebSocket) -> None:
    """Accept a Twilio Media Stream and bridge it to a VoiceSession.

    The agent_id comes in via the URL's customParameters mapping that
    we set in the TwiML `<Stream>` element. We also accept it as a
    query param for manual testing.
    """
    await ws.accept(subprotocol="audio.twilio.com")

    # Pull agent_id out of the query string first; the TwiML start
    # frame may override it via customParameters.
    agent_id = ws.query_params.get("agent_id", "")
    session: VoiceSession | None = None
    forward_task: asyncio.Task | None = None
    downsampler = _MuLawDownsampler()
    stream_sid: str = ""
    call_sid: str = ""
    from_number: str = ""
    started_at: datetime | None = None
    db_session_id: str = ""
    metrics = {"turn_count": 0, "first_token_ms": 0}

    try:
        while True:
            raw = await ws.receive_text()
            frame = json.loads(raw)
            event = frame.get("event")

            if event == "connected":
                # Just a heartbeat — Twilio confirms WS is alive.
                continue

            elif event == "start":
                start = frame.get("start") or {}
                stream_sid = start.get("streamSid", "")
                call_sid = start.get("callSid", "")
                custom = start.get("customParameters") or {}
                # customParameters overrides the URL agent_id if present.
                agent_id = custom.get("agent_id") or agent_id
                from_number = (start.get("from") or custom.get("from") or "")

                # Build the session — same flow as the browser WS path,
                # just with different audio framing.
                built = await _build_session(agent_id, from_number)
                if built is None:
                    logger.warning("twilio: could not build session for agent_id=%s", agent_id)
                    await ws.close()
                    return
                session, db_session_id, started_at = built
                forward_task = asyncio.create_task(
                    _forward_audio_to_twilio(session, ws, stream_sid, downsampler, metrics, started_at)
                )

            elif event == "media":
                if session is None:
                    continue
                payload_b64 = (frame.get("media") or {}).get("payload") or ""
                if not payload_b64:
                    continue
                pcm16k = _mulaw_to_pcm16k(base64.b64decode(payload_b64))
                # Feed into the orchestrator just like a browser WS
                # frame; the rest of the pipeline doesn't care that the
                # bytes originated from a phone.
                await session.push_audio(
                    AudioChunk(data=pcm16k, sample_rate=_PIPELINE_RATE, encoding="pcm16")
                )

            elif event == "mark":
                # Twilio echoes our marks back when playback hits them —
                # useful for measuring playback latency. Ignore for now.
                continue

            elif event == "stop":
                # Caller hung up. Drain the session and exit cleanly.
                if session is not None:
                    await session.end_audio()
                break

    except WebSocketDisconnect:
        logger.info("twilio ws disconnected (call_sid=%s)", call_sid)
    except Exception:
        logger.exception("twilio stream handler crashed")
    finally:
        if session is not None:
            await session.end_audio()
        if forward_task is not None:
            forward_task.cancel()
            try:
                await forward_task
            except (asyncio.CancelledError, Exception):
                pass
        # Persist final session metrics.
        if db_session_id and started_at is not None:
            try:
                ended = datetime.now(timezone.utc)
                duration_ms = int((ended - started_at).total_seconds() * 1000)
                async with db_session() as s:
                    row = await s.get(DBSession, db_session_id)
                    if row is not None:
                        row.ended_at = ended
                        row.duration_ms = duration_ms
                        row.turn_count = metrics["turn_count"]
                        row.first_token_ms = metrics["first_token_ms"]
                        row.status = "completed"
            except Exception:
                logger.exception("could not finalize twilio session row")


# ── Session construction ────────────────────────────────────────────


async def _build_session(
    agent_id: str, caller_id: str
) -> tuple[VoiceSession, str, datetime] | None:
    """Load the agent, wire providers, persist a Session row.

    Returns (session, db_session_id, started_at) or None on failure.
    Mostly the same shape as the browser WS path but channelled to
    `phone` and pulling caller_id from Twilio's start frame.
    """
    if not agent_id:
        return None
    reg = get_registry()

    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            return None
        stt = reg.get(ProviderType.STT, a.stt_provider)
        tts = reg.get(ProviderType.TTS, a.tts_provider)
        llm = reg.get(ProviderType.LLM, a.llm_provider)
        if (
            not isinstance(stt, STTProvider)
            or not isinstance(tts, TTSProvider)
            or not isinstance(llm, LLMProvider)
        ):
            return None
        if not (stt.is_available() and tts.is_available() and llm.is_available()):
            return None

        cfg = SessionConfig(
            system_prompt=a.system_prompt,
            greeting=a.greeting,
            llm_model=a.llm_model,
            temperature=a.temperature,
            max_tokens=a.max_tokens,
            stt=STTConfig(sample_rate=_PIPELINE_RATE, language=a.voice_language),
            tts=TTSConfig(voice_id=a.voice_id, language=a.voice_language, speed=a.voice_speed),
            skills=list(a.skills or []),
            voice_map=dict(a.voice_map or {}),
        )

        # Persist the call as a Session row right away so even mid-call
        # crashes leave an observable trace.
        started_at = datetime.now(timezone.utc)
        row = DBSession(
            agent_id=agent_id,
            channel="phone",
            caller_id=caller_id,
            started_at=started_at,
            status="active",
        )
        s.add(row)
        await s.flush()
        db_session_id = row.id

        # Optional VAD.
        from openvox.providers.vad.base import VADProvider as _VADProvider
        vad_candidate = reg.get(ProviderType.VAD, getattr(a, "vad_provider", "silero") or "silero")
        vad = vad_candidate if isinstance(vad_candidate, _VADProvider) else None

        from openvox.skills import SkillContext
        from openvox.skills.runner import SkillRunner
        runner = SkillRunner(
            skill_ids=cfg.skills or [],
            ctx=SkillContext(agent_id=agent_id, metadata={"source": "twilio", "caller_id": caller_id}),
        )
        session = VoiceSession(stt=stt, llm=llm, tts=tts, config=cfg, skill_runner=runner, vad=vad)
        return session, db_session_id, started_at


# ── Outbound: agent → phone ─────────────────────────────────────────


async def _forward_audio_to_twilio(
    session: VoiceSession,
    ws: WebSocket,
    stream_sid: str,
    downsampler: _MuLawDownsampler,
    metrics: dict,
    started_at: datetime,
) -> None:
    """Pump VoiceSession events back into Twilio's WS.

    Three event kinds matter for phone playback:
      - `assistant_audio` → re-encode and send as a Twilio media frame.
      - `interrupt`       → send Twilio a `clear` event so any
                            already-buffered audio gets discarded.
      - `assistant_done`  → bookkeeping for metrics.
    """
    try:
        async for ev in session.run():
            if ev.kind == "assistant_audio":
                # PCM in, μ-law out.
                mu = downsampler.encode(ev.audio, ev.sample_rate or _TTS_OUTPUT_RATE_DEFAULT)
                payload = base64.b64encode(mu).decode("ascii")
                await ws.send_text(json.dumps({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": payload},
                }))
            elif ev.kind == "interrupt":
                # Tell Twilio to drop anything we've sent but it hasn't
                # played yet. Without this the user keeps hearing 300–
                # 500 ms of stale audio after they barge in.
                await ws.send_text(json.dumps({
                    "event": "clear",
                    "streamSid": stream_sid,
                }))
            elif ev.kind == "assistant_done":
                metrics["turn_count"] = metrics.get("turn_count", 0) + 1
            elif ev.kind == "assistant_token" and metrics.get("first_token_ms", 0) == 0:
                metrics["first_token_ms"] = int(
                    (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
                )
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("twilio outbound forward crashed")
