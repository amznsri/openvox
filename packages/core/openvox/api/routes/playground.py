"""Playground — quick test endpoints for the dashboard.

  - `text`            : streaming chat completion (no audio)
  - `audio_analyze`   : upload an audio file → transcribe + sentiment + profanity
  - `document_query`  : ask a question against an agent's uploaded documents
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from openvox.config import get_settings
from openvox.providers import ProviderType, get_registry
from openvox.providers.base import (
    AudioChunk,
    LLMConfig,
    LLMMessage,
    LLMProvider,
    STTConfig,
    STTProvider,
    TTSConfig,
    TTSProvider,
)
from openvox.skills import SkillContext
from openvox.skills.runner import SkillRunner

logger = logging.getLogger(__name__)
router = APIRouter()


class TextRequest(BaseModel):
    provider: str = "byteplus"
    model: str = "doubao-seed-1.6-250615"
    system: str = "You are a helpful voice assistant."
    user: str
    temperature: float = 0.7
    max_tokens: int = 1024
    # Optional — when present, we persist a Session row so the
    # Observability page has something to show. Sent by the playground
    # Text tab once an agent is selected.
    agent_id: str = ""


@router.post("/text")
async def text_chat(req: TextRequest) -> StreamingResponse:
    llm = get_registry().get(ProviderType.LLM, req.provider)
    if llm is None or not isinstance(llm, LLMProvider) or not llm.is_available():
        raise HTTPException(400, f"LLM provider '{req.provider}' is not available — set its API key in .env")

    msgs = [LLMMessage(role="system", content=req.system), LLMMessage(role="user", content=req.user)]
    cfg = LLMConfig(model=req.model, temperature=req.temperature, max_tokens=req.max_tokens, stream=True)

    # Persist a "text" session up-front so Observability has a row even
    # mid-stream. We update duration + first_token at end-of-gen below.
    from datetime import datetime, timezone

    from openvox.db import db_session
    from openvox.db.models import Session as DBSession
    from openvox.db.models import Transcript

    started = datetime.now(timezone.utc)
    session_id: str = ""
    if req.agent_id:
        try:
            async with db_session() as s:
                row = DBSession(
                    agent_id=req.agent_id,
                    channel="web",
                    caller_id="text-playground",
                    started_at=started,
                    status="active",
                )
                s.add(row)
                await s.flush()
                session_id = row.id
                s.add(Transcript(session_id=session_id, role="user", text=req.user[:4000]))
        except Exception:
            logger.exception("could not persist text session row")

    async def gen():
        first_token_ms = 0
        full = ""
        async for chunk in llm.chat_stream(msgs, cfg):
            if chunk.delta:
                if first_token_ms == 0:
                    first_token_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
                full += chunk.delta
                yield chunk.delta
        # Finalize the session row once the LLM stream completes. We do
        # this in a fresh db_session because the request-scoped one above
        # closed at the yield boundary.
        if session_id:
            try:
                ended = datetime.now(timezone.utc)
                duration_ms = int((ended - started).total_seconds() * 1000)
                async with db_session() as s:
                    sess = await s.get(DBSession, session_id)
                    if sess is not None:
                        sess.ended_at = ended
                        sess.duration_ms = duration_ms
                        sess.first_token_ms = first_token_ms
                        sess.turn_count = 1
                        sess.status = "completed"
                    s.add(Transcript(session_id=session_id, role="assistant", text=full[:8000]))
            except Exception:
                logger.exception("could not finalize text session row")

    return StreamingResponse(gen(), media_type="text/plain")


# ──────────────────────────────────────────────────────────────────────
# Audio file analyzer — used by the Audio Analyzer template / playground
# ──────────────────────────────────────────────────────────────────────


def _decode_to_pcm16k(data: bytes, content_type: str | None, filename: str | None) -> tuple[bytes, int]:
    """Decode an audio file (mp3/wav/m4a/ogg/flac/aac/webm) to PCM s16le
    mono 16 kHz. Uses pydub which shells out to ffmpeg (installed in the
    core image)."""
    from pydub import AudioSegment

    name = (filename or "").lower()
    fmt = None
    for ext in ("mp3", "wav", "m4a", "mp4", "ogg", "flac", "aac", "webm", "opus"):
        if name.endswith("." + ext):
            fmt = "mp4" if ext == "m4a" else ext
            break
    if fmt is None:
        ct = (content_type or "").lower()
        if "mpeg" in ct or "mp3" in ct:
            fmt = "mp3"
        elif "wav" in ct:
            fmt = "wav"
        elif "ogg" in ct:
            fmt = "ogg"
        elif "flac" in ct:
            fmt = "flac"
        elif "aac" in ct or "mp4" in ct or "m4a" in ct:
            fmt = "mp4"
        elif "webm" in ct:
            fmt = "webm"
        else:
            fmt = "mp3"  # last-resort guess

    seg = AudioSegment.from_file(io.BytesIO(data), format=fmt)
    seg = seg.set_channels(1).set_frame_rate(16000).set_sample_width(2)
    return seg.raw_data, len(seg)  # raw PCM, duration ms


async def _stream_pcm_to_stt(
    pcm: bytes,
    duration_ms: int,
    stt: STTProvider,
    *,
    language: str = "en-US",
) -> tuple[str, list[dict[str, Any]]]:
    """Push PCM through the streaming STT and collect final text + utterances."""
    chunk_bytes = 16000 * 2 // 10  # ~100 ms of PCM s16le mono

    async def frames() -> AsyncIterator[AudioChunk]:
        for i in range(0, len(pcm), chunk_bytes):
            piece = pcm[i : i + chunk_bytes]
            is_last = (i + chunk_bytes) >= len(pcm)
            yield AudioChunk(data=piece, sample_rate=16000, encoding="pcm16", is_final=is_last)
            # Mild pacing so we don't overwhelm the WS — 25 ms per 100 ms of audio.
            await asyncio.sleep(0.025)

    full_text: list[str] = []
    utterances: list[dict[str, Any]] = []
    cfg = STTConfig(sample_rate=16000, language=language, interim_results=False)
    async for r in stt.transcribe_stream(frames(), cfg):
        if r.is_final and r.text:
            full_text.append(r.text)
            for u in (r.raw or {}).get("payload_msg", {}).get("result", {}).get("utterances", []) or []:
                if u.get("definite"):
                    utterances.append(
                        {
                            "text": u.get("text"),
                            "start_ms": u.get("start_time"),
                            "end_ms": u.get("end_time"),
                        }
                    )
    return " ".join(full_text).strip(), utterances


@router.post("/audio_analyze")
async def audio_analyze(
    file: UploadFile = File(...),
    language: str = Form("en-US"),
    sentiment: bool = Form(True),
    profanity: bool = Form(True),
) -> dict[str, Any]:
    """Upload an audio file, transcribe, run sentiment + profanity skills,
    return everything as one structured response.

    Uses the streaming STT WebSocket (so it works with any storage backend
    — no requirement on TOS / S3 to host the file)."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")

    try:
        pcm, duration_ms = await asyncio.to_thread(
            _decode_to_pcm16k, raw, file.content_type, file.filename
        )
    except Exception as e:
        logger.exception("audio decode failed")
        raise HTTPException(400, f"could not decode audio: {e}") from e

    stt = get_registry().get(ProviderType.STT, "byteplus")
    if stt is None or not isinstance(stt, STTProvider) or not stt.is_available():
        raise HTTPException(
            400, "BytePlus STT unavailable — set BYTEPLUS_VOICE_API_KEY in .env"
        )

    transcript, utterances = await _stream_pcm_to_stt(pcm, duration_ms, stt, language=language)

    out: dict[str, Any] = {
        "transcript": transcript,
        "utterances": utterances,
        "duration_ms": duration_ms,
        "filename": file.filename,
    }
    if transcript:
        skills_to_run: list[str] = []
        if sentiment:
            skills_to_run.append("sentiment_analyze")
        if profanity:
            skills_to_run.append("profanity_check")
        if skills_to_run:
            runner = SkillRunner(skill_ids=skills_to_run, ctx=SkillContext(metadata={"source": "audio_analyze"}))
            if sentiment:
                out["sentiment"] = await runner.invoke("sentiment_analyze", {"text": transcript})
            if profanity:
                out["profanity"] = await runner.invoke("profanity_check", {"text": transcript})
    return out


# ──────────────────────────────────────────────────────────────────────
# Document Q&A — text mode (the agent voice loop reaches the same path
# via the query_documents skill; this is a convenience for the dashboard)
# ──────────────────────────────────────────────────────────────────────


class DocQueryRequest(BaseModel):
    agent_id: str
    question: str
    top_k: int = 5
    temperature: float = 0.2
    max_tokens: int = 800


@router.post("/document_query")
async def document_query(req: DocQueryRequest) -> dict[str, Any]:
    """Retrieve passages for an agent's documents and synthesize an answer."""
    from openvox.rag import query as rag_query

    passages = await rag_query(agent_id=req.agent_id, question=req.question, top_k=req.top_k)
    if not passages:
        return {"answer": "", "passages": [], "note": "no documents indexed for this agent"}

    llm = get_registry().get(ProviderType.LLM, "byteplus")
    if llm is None or not isinstance(llm, LLMProvider) or not llm.is_available():
        raise HTTPException(400, "BytePlus LLM unavailable")

    # Build a RAG prompt: system + retrieved context block + user question.
    context = "\n\n".join(
        f"[{p.document_name} p{p.page} score={p.score:.2f}]\n{p.text}"
        for p in passages if p.kind == "text"
    )
    images = [p.text for p in passages if p.kind == "image"]
    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Answer the question using only the passages and images provided. "
                "If the answer isn't in them, say so. Cite sources by document name.\n\n"
                f"Passages:\n{context}\n\nQuestion: {req.question}"
            ),
        }
    ]
    for img in images:
        user_content.append({"type": "image_url", "image_url": {"url": img}})

    msgs = [
        LLMMessage(role="system", content="You are a careful document-grounded assistant."),
        LLMMessage(role="user", content=user_content),  # type: ignore[arg-type]
    ]
    answer = await llm.chat(
        msgs, LLMConfig(model="", temperature=req.temperature, max_tokens=req.max_tokens, stream=False)
    )
    return {
        "answer": answer.strip(),
        "passages": [
            {
                "source": p.document_name,
                "page": p.page,
                "kind": p.kind,
                "score": round(p.score, 3),
                "snippet": p.text[:300] if p.kind == "text" else "(image)",
            }
            for p in passages
        ],
    }


# ──────────────────────────────────────────────────────────────────────
# One-shot STT: audio file → transcript text. Used by the Documents tab
# to record a voice question, transcribe it, then submit to /document_query.
# ──────────────────────────────────────────────────────────────────────


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("en-US"),
) -> dict[str, Any]:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    try:
        pcm, duration_ms = await asyncio.to_thread(
            _decode_to_pcm16k, raw, file.content_type, file.filename
        )
    except Exception as e:
        logger.exception("audio decode failed")
        raise HTTPException(400, f"could not decode audio: {e}") from e

    stt = get_registry().get(ProviderType.STT, "byteplus")
    if stt is None or not isinstance(stt, STTProvider) or not stt.is_available():
        raise HTTPException(
            400, "BytePlus STT unavailable — set BYTEPLUS_VOICE_API_KEY in .env"
        )

    transcript, _ = await _stream_pcm_to_stt(pcm, duration_ms, stt, language=language)
    return {"transcript": transcript, "duration_ms": duration_ms}


# ──────────────────────────────────────────────────────────────────────
# One-shot TTS: text → PCM bytes. Used by the Documents tab to speak
# the document Q&A answer, and elsewhere when we just want a single
# audio blob without spinning up a full voice session.
# ──────────────────────────────────────────────────────────────────────


class SynthesizeRequest(BaseModel):
    text: str
    voice_id: str | None = None
    sample_rate: int = 24000
    speed: float = 1.0
    language: str = "en-US"


@router.post("/synthesize")
async def synthesize(req: SynthesizeRequest) -> Response:
    if not req.text.strip():
        raise HTTPException(400, "empty text")
    tts = get_registry().get(ProviderType.TTS, "byteplus")
    if tts is None or not isinstance(tts, TTSProvider) or not tts.is_available():
        raise HTTPException(
            400, "BytePlus TTS unavailable — set BYTEPLUS_VOICE_API_KEY in .env"
        )

    settings = get_settings()
    cfg = TTSConfig(
        voice_id=req.voice_id or settings.byteplus_tts_default_voice,
        language=req.language,
        speed=req.speed,
        sample_rate=req.sample_rate,
        encoding="pcm16",
    )

    chunks: list[bytes] = []
    sample_rate = req.sample_rate
    async for c in tts.synthesize_stream(req.text, cfg):
        chunks.append(c.data)
        if c.sample_rate:
            sample_rate = c.sample_rate

    audio = b"".join(chunks)
    return Response(
        content=audio,
        media_type="audio/pcm",
        headers={
            "X-Sample-Rate": str(sample_rate),
            "X-Encoding": "pcm_s16le",
            "X-Channels": "1",
        },
    )
