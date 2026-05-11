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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openvox.db import db_session
from openvox.db.models import Agent
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
    forward_task: asyncio.Task | None = None

    try:
        while True:
            msg = await ws.receive()
            if "text" in msg and msg["text"] is not None:
                ctrl = json.loads(msg["text"])
                kind = ctrl.get("type")
                if kind == "start":
                    session = await _build_session(ctrl)
                    if session is None:
                        await ws.send_text(json.dumps({"type": "error", "message": "agent not found or providers unavailable"}))
                        return
                    forward_task = asyncio.create_task(_forward_events(session, ws))
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


async def _build_session(ctrl: dict) -> VoiceSession | None:
    agent_id = ctrl.get("agent_id")
    reg = get_registry()

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
            temperature = a.temperature
            max_tokens = a.max_tokens
    else:
        stt_id = ctrl.get("stt_provider", "byteplus")
        tts_id = ctrl.get("tts_provider", "byteplus")
        llm_id = ctrl.get("llm_provider", "byteplus")
        sys_prompt = ctrl.get("system_prompt", "You are a helpful voice assistant.")
        greeting = ctrl.get("greeting", "")
        llm_model = ctrl.get("llm_model", "doubao-seed-1.6-250615")
        voice_id = ctrl.get("voice_id", "")
        voice_lang = ctrl.get("voice_language", "en-US")
        voice_speed = float(ctrl.get("voice_speed", 1.0))
        skills = list(ctrl.get("skills", []))
        temperature = float(ctrl.get("temperature", 0.7))
        max_tokens = int(ctrl.get("max_tokens", 2048))

    stt = reg.get(ProviderType.STT, stt_id)
    tts = reg.get(ProviderType.TTS, tts_id)
    llm = reg.get(ProviderType.LLM, llm_id)
    if not isinstance(stt, STTProvider) or not isinstance(tts, TTSProvider) or not isinstance(llm, LLMProvider):
        return None
    if not (stt.is_available() and tts.is_available() and llm.is_available()):
        return None

    cfg = SessionConfig(
        system_prompt=sys_prompt,
        greeting=greeting,
        llm_model=llm_model,
        temperature=temperature,
        max_tokens=max_tokens,
        stt=STTConfig(sample_rate=int(ctrl.get("sample_rate", 16000)), language=voice_lang),
        tts=TTSConfig(voice_id=voice_id, language=voice_lang, speed=voice_speed),
        skills=skills,
    )
    return VoiceSession(stt=stt, llm=llm, tts=tts, config=cfg)


async def _forward_events(session: VoiceSession, ws: WebSocket) -> None:
    try:
        async for ev in session.run():
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
