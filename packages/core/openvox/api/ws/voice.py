"""Realtime voice WebSocket: client streams mic → we stream audio back.

Wire format (text frames are control JSON, binary frames are audio):

  Client → Server:
    text  {"type":"start","agent_id":"...","sample_rate":16000}
    binary <PCM s16le mono frames>
    text  {"type":"end"}
    text  {"type":"interrupt"}

  Server → Client:
    text  {"type":"user_partial","text":"..."}
    text  {"type":"user_final","text":"..."}
    text  {"type":"assistant_token","text":"..."}
    binary <PCM s16le mono assistant audio frames>
    text  {"type":"assistant_done","text":"<full utterance>"}
    text  {"type":"skill_call","name":"...","args":{...}}
    text  {"type":"skill_result","name":"...","result":{...}}
    text  {"type":"interrupt"}
    text  {"type":"error","message":"..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openvox.db import db_session
from openvox.db.models import Agent, Transcript
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

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/voice")
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    session: VoiceSession | None = None
    mcp_mgr = None
    forward_task: asyncio.Task | None = None
    # Observability bookkeeping. We persist one DB row per voice call so
    # the dashboard's Observability page has something to render, plus
    # the pricing-calculator telemetry counters.
    db_session_id: str = ""
    started_at: datetime | None = None
    metrics = {
        "turn_count": 0,
        "first_token_ms": 0,
        # Word-count proxy (always populated) — used as fallback when
        # the provider doesn't return usage.
        "llm_tokens_in_approx": 0,
        "llm_tokens_out_approx": 0,
        # Provider-reported counts (populated by `llm_usage` events
        # when stream_options.include_usage is honoured).
        "llm_tokens_in_real": 0,
        "llm_tokens_out_real": 0,
        "tts_chars": 0,
        # ASR-side: char count of finalised user transcripts. Feeds
        # per-character STT pricing (BytePlus Seed ASR, Aliyun, etc.).
        "stt_chars": 0,
    }

    try:
        while True:
            msg = await ws.receive()
            if "text" in msg and msg["text"] is not None:
                ctrl = json.loads(msg["text"])
                kind = ctrl.get("type")
                if kind == "start":
                    built = await _build_session(ctrl)
                    if built is None:
                        await ws.send_text(json.dumps({"type": "error", "message": "agent not found or providers unavailable"}))
                        return
                    session, mcp_mgr = built
                    # Record the session row before forwarding starts so a
                    # client disconnect partway still leaves a trace.
                    agent_id = ctrl.get("agent_id") or ""
                    if agent_id:
                        started_at = datetime.now(timezone.utc)
                        try:
                            async with db_session() as s:
                                row = DBSession(
                                    agent_id=agent_id,
                                    channel=ctrl.get("channel") or "web",
                                    caller_id=ctrl.get("caller_id") or "voice-playground",
                                    started_at=started_at,
                                    status="active",
                                )
                                s.add(row)
                                await s.flush()
                                db_session_id = row.id
                        except Exception:
                            logger.exception("could not create voice session row")
                    forward_task = asyncio.create_task(_forward_events(session, ws, metrics, started_at, db_session_id))
                elif kind == "end":
                    if session:
                        await session.end_audio()
                elif kind == "interrupt":
                    if session:
                        session.interrupt()
            elif "bytes" in msg and msg["bytes"] is not None:
                if session is None:
                    continue
                await session.push_audio(
                    AudioChunk(data=msg["bytes"], sample_rate=16000, encoding="pcm16")
                )
    except WebSocketDisconnect:
        logger.info("voice ws disconnected")
    finally:
        if session is not None:
            await session.end_audio()
        if forward_task is not None:
            forward_task.cancel()
            try:
                await forward_task
            except asyncio.CancelledError:
                pass
        # Tear down any MCP stdio subprocesses we spawned for this session.
        if mcp_mgr is not None:
            try:
                await mcp_mgr.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("mcp teardown failed: %s", e)
        # Finalize the persisted session row. If the row creation failed
        # above (e.g. ad-hoc session with no agent_id), there's nothing
        # to update — just exit.
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
                        # Prefer provider-reported usage when any landed
                        # (some providers return 0 for sub-token finals);
                        # else fall back to the word-count proxy.
                        in_real = metrics.get("llm_tokens_in_real", 0)
                        out_real = metrics.get("llm_tokens_out_real", 0)
                        row.llm_tokens_in = (
                            in_real if in_real > 0 else metrics.get("llm_tokens_in_approx", 0)
                        )
                        row.llm_tokens_out = (
                            out_real if out_real > 0 else metrics.get("llm_tokens_out_approx", 0)
                        )
                        row.tts_chars = metrics.get("tts_chars", 0)
                        row.stt_chars = metrics.get("stt_chars", 0)
                        row.status = "completed"
            except Exception:
                logger.exception("could not finalize voice session row")


async def _build_session(ctrl: dict) -> tuple[VoiceSession, "MCPSessionManager | None"] | None:
    """Build a VoiceSession + (optional) MCP manager.

    Returns a tuple so the caller can close the MCP manager when the WS
    ends. None on failure (agent missing or providers unavailable).
    """
    agent_id = ctrl.get("agent_id")
    reg = get_registry()
    mcp_servers: list[dict] = []

    # Load agent config (or fall back to defaults for ad-hoc sessions).
    if agent_id:
        async with db_session() as s:
            a = await s.get(Agent, agent_id)
            if a is None:
                return None
            stt_id, tts_id, llm_id = a.stt_provider, a.tts_provider, a.llm_provider
            sys_prompt, greeting = a.system_prompt, a.greeting
            llm_model = a.llm_model
            voice_id = a.voice_id
            voice_lang = a.voice_language
            voice_speed = a.voice_speed
            skills = list(a.skills or [])
            mcp_servers = list(a.mcp_servers or [])
            voice_map = dict(a.voice_map or {})
            temperature = a.temperature
            max_tokens = a.max_tokens
            vad_id = (getattr(a, "vad_provider", None) or "silero")
    else:
        stt_id = ctrl.get("stt_provider", "byteplus")
        tts_id = ctrl.get("tts_provider", "byteplus")
        llm_id = ctrl.get("llm_provider", "byteplus")
        sys_prompt = ctrl.get("system_prompt", "You are a helpful voice assistant.")
        greeting = ctrl.get("greeting", "")
        # Empty string → BytePlus provider falls back to
        # settings.byteplus_llm_model (currently seed-2-0-pro-260328).
        # The stale hard-coded "doubao-seed-1.6-250615" default used to
        # silently override the configured model for ad-hoc sessions.
        llm_model = ctrl.get("llm_model", "")
        voice_id = ctrl.get("voice_id", "")
        voice_lang = ctrl.get("voice_language", "en-US")
        voice_speed = float(ctrl.get("voice_speed", 1.0))
        skills = list(ctrl.get("skills", []))
        mcp_servers = list(ctrl.get("mcp_servers", []))
        voice_map = dict(ctrl.get("voice_map", {}))
        temperature = float(ctrl.get("temperature", 0.7))
        max_tokens = int(ctrl.get("max_tokens", 2048))
        vad_id = (ctrl.get("vad_provider") or "silero")

    stt = reg.get(ProviderType.STT, stt_id)
    tts = reg.get(ProviderType.TTS, tts_id)
    llm = reg.get(ProviderType.LLM, llm_id)
    # VAD is optional — looking up an "none"/missing id returns None and
    # the orchestrator falls back to the client-driven interrupt path.
    vad = None
    if vad_id and vad_id.lower() != "none":
        candidate = reg.get(ProviderType.VAD, vad_id)
        # Late-binding type check to avoid a circular import at module load.
        from openvox.providers.vad.base import VADProvider as _VADProvider
        if isinstance(candidate, _VADProvider):
            vad = candidate
    if not isinstance(stt, STTProvider) or not isinstance(tts, TTSProvider) or not isinstance(llm, LLMProvider):
        return None
    if not (stt.is_available() and tts.is_available() and llm.is_available()):
        return None

    # Spin up MCP sessions (if any). Bridged skills become extra tools the
    # LLM can call alongside the built-in ones. The manager stays alive
    # for the duration of this VoiceSession.
    mcp_mgr = None
    extra_skills: list = []
    if mcp_servers:
        from openvox.mcp import MCPSessionManager
        mcp_mgr = MCPSessionManager(mcp_servers)
        try:
            await mcp_mgr.__aenter__()
            extra_skills = list(mcp_mgr.skills)
            logger.info("mcp: session built with %d bridged tools", len(extra_skills))
        except Exception as e:
            logger.warning("mcp: setup failed, continuing without external tools: %s", e)
            mcp_mgr = None
            extra_skills = []

    cfg = SessionConfig(
        system_prompt=sys_prompt,
        greeting=greeting,
        llm_model=llm_model,
        temperature=temperature,
        max_tokens=max_tokens,
        stt=STTConfig(sample_rate=int(ctrl.get("sample_rate", 16000)), language=voice_lang),
        tts=TTSConfig(voice_id=voice_id, language=voice_lang, speed=voice_speed),
        skills=skills,
        voice_map=voice_map,
    )
    # The orchestrator instantiates its own SkillRunner; we override to
    # include the MCP-bridged skills.
    from openvox.skills import SkillContext
    from openvox.skills.runner import SkillRunner
    runner = SkillRunner(
        skill_ids=skills,
        ctx=SkillContext(agent_id=agent_id or "", metadata={"source": "ws_voice"}),
        extra_skills=extra_skills,
    )
    session = VoiceSession(stt=stt, llm=llm, tts=tts, config=cfg, skill_runner=runner, vad=vad)
    return session, mcp_mgr


async def _forward_events(
    session: VoiceSession,
    ws: WebSocket,
    metrics: dict | None = None,
    started_at: datetime | None = None,
    db_session_id: str = "",
) -> None:
    try:
        async for ev in session.run():
            # Persist user_final + assistant_done as Transcript rows so
            # Observability shows turn-by-turn detail AND "Save as recording"
            # captures something the eval replay runner can feed back in.
            # Without this, voice recordings end up with transcript=[] and
            # replay evals always fail with "no agent dialogue in transcript".
            if db_session_id and ev.kind in ("user_final", "assistant_done") and (ev.text or "").strip():
                try:
                    async with db_session() as s:
                        s.add(Transcript(
                            session_id=db_session_id,
                            role="user" if ev.kind == "user_final" else "assistant",
                            text=(ev.text or "")[:8000],
                        ))
                except Exception:
                    logger.exception("could not persist transcript row")
            # Update observability counters as events flow past us.
            if metrics is not None:
                if ev.kind == "assistant_done":
                    metrics["turn_count"] = metrics.get("turn_count", 0) + 1
                    # On turn end, "speak" was the assistant's full text;
                    # count chars as a TTS-billing approximation. (Each
                    # turn pushes one full transcript through TTS.)
                    metrics["tts_chars"] = metrics.get("tts_chars", 0) + len(ev.text or "")
                if (
                    ev.kind == "assistant_token"
                    and started_at is not None
                    and not metrics.get("first_token_ms")
                ):
                    metrics["first_token_ms"] = int(
                        (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
                    )
                # Token accounting — we track *both* a word-count proxy
                # (`_approx`) and provider-reported usage (`_real`).
                # When the call finalises we prefer real if any landed,
                # else fall back to approx. This way pricing stays
                # honest even when a provider doesn't return usage.
                if ev.kind == "assistant_token":
                    metrics["llm_tokens_out_approx"] = metrics.get(
                        "llm_tokens_out_approx", 0
                    ) + max(1, len((ev.text or "").split()))
                if ev.kind == "user_final":
                    metrics["llm_tokens_in_approx"] = metrics.get(
                        "llm_tokens_in_approx", 0
                    ) + max(1, len((ev.text or "").split()))
                    # Per-character STT billing — accumulate raw char
                    # length of the finalised user utterance.
                    metrics["stt_chars"] = metrics.get(
                        "stt_chars", 0
                    ) + len(ev.text or "")
                # Real usage arrives on the terminal stream chunk —
                # accumulate so multi-turn sessions report correctly.
                if ev.kind == "llm_usage" and ev.data:
                    metrics["llm_tokens_in_real"] = metrics.get(
                        "llm_tokens_in_real", 0
                    ) + int(ev.data.get("prompt_tokens") or 0)
                    metrics["llm_tokens_out_real"] = metrics.get(
                        "llm_tokens_out_real", 0
                    ) + int(ev.data.get("completion_tokens") or 0)
            if ev.kind == "assistant_audio":
                await ws.send_bytes(ev.audio)
            else:
                await ws.send_text(json.dumps(_event_to_json(ev)))
    except WebSocketDisconnect:
        return
    except Exception as e:
        logger.exception("forward task error")
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass


def _event_to_json(ev) -> dict:
    out: dict = {"type": ev.kind}
    if ev.text:
        out["text"] = ev.text
    if ev.data is not None:
        out.update(ev.data if isinstance(ev.data, dict) else {"data": ev.data})
    if ev.kind == "assistant_audio":
        out["sample_rate"] = ev.sample_rate
        out["encoding"] = ev.encoding
    return out
