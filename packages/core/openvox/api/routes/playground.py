"""Playground — quick test endpoints for the dashboard.

  - `text`            : streaming chat completion (no audio)
  - `audio_analyze`   : upload an audio file → transcribe + sentiment + profanity
  - `document_query`  : ask a question against an agent's uploaded documents
"""

from __future__ import annotations

import asyncio
import io
import json
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
    # Empty string → providers resolve their own default
    # (byteplus → settings.byteplus_llm_model). Hard-coding a model
    # here used to silently override the configured one with a stale
    # name; see orchestrator.py SessionConfig.llm_model for the
    # canonical pattern.
    model: str = ""
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
        raise HTTPException(
            400,
            f"LLM provider '{req.provider}' is not configured. "
            f"Add an API key via the dashboard setup wizard "
            f"(the /dashboard/setup page) "
            f"or set the provider's API key env var (e.g. "
            f"BYTEPLUS_LLM_API_KEY / OPENAI_API_KEY / "
            f"ANTHROPIC_API_KEY) in your .env file.",
        )

    # Persist a "text" session up-front so Observability has a row even
    # mid-stream. We update duration + first_token at end-of-gen below.
    from datetime import datetime, timezone

    from openvox.db import db_session
    from openvox.db.models import Agent
    from openvox.db.models import Session as DBSession
    from openvox.db.models import Transcript

    # ── Load the agent's skills + MCP servers if agent_id supplied ──
    # When the playground Text tab is targeted at a specific agent,
    # we must run the same skill-loop the voice WS + /turn run. Without
    # it, agents whose prompt instructs the LLM to call tools (every
    # productivity template post-Phase 1.6) emit their function-call
    # markup as plain content text — Seed-2-Pro's
    # ``<|FunctionCallBegin|>…<|FunctionCallEnd|>`` artefact. Same family
    # as CLAUDE.md §8 #92 — the /turn + Telegram fix — extended to
    # /playground/text.
    skill_ids: list[str] = []
    mcp_servers: list[dict] = []
    system_prompt = req.system  # default fallback when no agent_id
    if req.agent_id:
        try:
            async with db_session() as s:
                a = await s.get(Agent, req.agent_id)
                if a is not None:
                    skill_ids = list(a.skills or [])
                    mcp_servers = list(a.mcp_servers or [])
                    # Use the agent's own system prompt — the caller's
                    # `req.system` is a generic fallback that doesn't
                    # know about the agent's skill toolkit.
                    if a.system_prompt:
                        system_prompt = a.system_prompt
        except Exception:
            logger.exception("could not load agent config for text playground")

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

    # Skill loop runs inside the streaming generator. `open_agent_mcp`
    # + the streaming tool-call merge helpers are imported lazily so
    # the no-tools fast path stays trivial.
    from openvox.mcp import open_agent_mcp
    from openvox.pipeline.orchestrator import (
        _finalise_tool_calls,
        _merge_tool_call_deltas,
    )

    async def gen():
        first_token_ms = 0
        full = ""
        # Track real provider-reported usage when it arrives in the
        # terminal chunk; fall back to a word-count proxy if not.
        usage_in_real = 0
        usage_out_real = 0

        # Open MCP for this turn (no-op fast path when mcp_servers
        # is empty). Same async-context helper /turn + Telegram use.
        async with open_agent_mcp(mcp_servers) as mcp_extras:
            runner = SkillRunner(
                skill_ids=skill_ids,
                ctx=SkillContext(
                    agent_id=req.agent_id or "",
                    metadata={"source": "playground_text"},
                ),
                extra_skills=mcp_extras,
            )

            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=req.user),
            ]
            tool_specs = runner.tool_specs() or None
            cfg = LLMConfig(
                model=req.model,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                stream=True,
                tools=tool_specs,
            )

            # Bounded tool-call loop — same cap as orchestrator
            # `max_tool_iterations` + agent_text_turn. 6 is enough
            # for any reasonable chain (CLAUDE.md §8 #46).
            max_iters = 6
            for iteration in range(max_iters):
                # Stream this round's LLM tokens. Accumulate text +
                # MERGE streaming tool_call fragments by index.
                #
                # CRITICAL: BytePlus Ark (and OpenAI-compatible
                # providers in general) stream tool_calls in MANY
                # fragments — each chunk carries part of the JSON
                # `arguments` string. The first fragment for a given
                # index has the function name; subsequent fragments
                # only have args pieces. We MUST accumulate per-index
                # (CLAUDE.md §8 #17 — repeat offender), not just take
                # the last fragment. Hence the shared
                # ``_merge_tool_call_deltas`` helper from the
                # orchestrator — exactly the same code path the voice
                # WS uses.
                round_text = ""
                tool_calls_by_idx: dict[int, dict[str, Any]] = {}
                async for chunk in llm.chat_stream(messages, cfg):
                    if chunk.delta:
                        if first_token_ms == 0:
                            first_token_ms = int(
                                (datetime.now(timezone.utc) - started).total_seconds() * 1000
                            )
                        round_text += chunk.delta
                        full += chunk.delta
                        yield chunk.delta
                    if chunk.tool_calls:
                        _merge_tool_call_deltas(tool_calls_by_idx, chunk.tool_calls)
                    if chunk.usage:
                        usage_in_real = int(chunk.usage.get("prompt_tokens") or 0)
                        usage_out_real = int(chunk.usage.get("completion_tokens") or 0)

                final_tool_calls = _finalise_tool_calls(tool_calls_by_idx)
                if not final_tool_calls:
                    # No tool wants to fire — LLM is done. Exit loop.
                    break

                # Tools to run. Append the assistant tool_calls message
                # to history (Ark / OpenAI contract — see CLAUDE.md §8
                # #18). Then invoke each and feed the result back.
                messages.append(
                    LLMMessage(role="assistant", content=round_text, tool_calls=final_tool_calls)
                )
                # Brief status marker so the user sees something is
                # happening between LLM rounds. Kept terse — the
                # LLM's NEXT round will produce the real user-facing
                # text. Streaming-render-friendly: newline-delimited.
                for tc in final_tool_calls:
                    name = (tc.get("function") or {}).get("name") or ""
                    yield f"\n_…calling {name}…_\n"
                    raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                    try:
                        parsed = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        parsed = {"_raw": raw_args}
                    if not isinstance(parsed, dict):
                        parsed = {"_value": parsed}
                    result = await runner.invoke(name, parsed)
                    messages.append(
                        LLMMessage(
                            role="tool",
                            tool_call_id=tc.get("id") or "",
                            name=name,
                            content=json.dumps(result, ensure_ascii=False),
                        )
                    )
            else:
                yield "\n\n_(stopped after tool-call iteration cap)_"
        # Finalize the session row once the LLM stream completes. We do
        # this in a fresh db_session because the request-scoped one above
        # closed at the yield boundary.
        if session_id:
            try:
                ended = datetime.now(timezone.utc)
                duration_ms = int((ended - started).total_seconds() * 1000)
                # Prefer real over approx (word-count). Approx is the
                # length-of-words heuristic — fine for a fallback, bad
                # for billing.
                tokens_in = usage_in_real if usage_in_real > 0 else max(1, len(req.user.split()))
                tokens_out = usage_out_real if usage_out_real > 0 else max(1, len(full.split()))
                async with db_session() as s:
                    sess = await s.get(DBSession, session_id)
                    if sess is not None:
                        sess.ended_at = ended
                        sess.duration_ms = duration_ms
                        sess.first_token_ms = first_token_ms
                        sess.turn_count = 1
                        sess.llm_tokens_in = tokens_in
                        sess.llm_tokens_out = tokens_out
                        sess.tts_chars = len(full)
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
    # `oga` is Telegram's voice-note extension (OGG container, Opus codec).
    # Map onto "ogg" so pydub / ffmpeg pick the right demuxer.
    for ext in ("mp3", "wav", "m4a", "mp4", "ogg", "oga", "flac", "aac", "webm", "opus"):
        if name.endswith("." + ext):
            fmt = "mp4" if ext == "m4a" else ("ogg" if ext == "oga" else ext)
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
            400,
            "BytePlus STT is not configured. "
            "Add your BytePlus Voice API key via the dashboard setup wizard "
            "(the /dashboard/setup page) "
            "or set BYTEPLUS_VOICE_API_KEY in your .env file.",
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
            400,
            "BytePlus STT is not configured. "
            "Add your BytePlus Voice API key via the dashboard setup wizard "
            "(the /dashboard/setup page) "
            "or set BYTEPLUS_VOICE_API_KEY in your .env file.",
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
            400,
            "BytePlus TTS is not configured. "
            "Add your BytePlus Voice API key via the dashboard setup wizard "
            "(the /dashboard/setup page) "
            "or set BYTEPLUS_VOICE_API_KEY in your .env file.",
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
