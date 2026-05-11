"""VoiceSession — the per-call orchestrator.

Pipeline (per turn):

    mic ─► STT ──(text)──► LLM ──(token stream)──► TTS ──(audio)──► speaker
                                  │
                                  └► tool/skill calls ─► result feeds back into LLM

Key responsibilities:
  - Buffer audio frames from the client and forward to STT.
  - When STT emits a *final* utterance, push it to the LLM as a user turn.
  - As the LLM streams tokens, feed them to TTS in sentence-sized chunks
    so audio starts playing within ~300 ms of the first token.
  - Detect interruptions: if the user starts speaking while we're playing
    the assistant's audio, cancel the current TTS stream and start over.

This is a *cooperative* orchestrator — it coordinates async generators,
nothing more. The websocket route in `openvox.api.routes.voice` wires
real audio in/out around it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from openvox.providers.base import (
    AudioChunk,
    LLMConfig,
    LLMMessage,
    LLMProvider,
    STTConfig,
    STTProvider,
    STTResult,
    TTSConfig,
    TTSProvider,
)
from openvox.skills import SkillRunner

logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    system_prompt: str = "You are a helpful voice assistant."
    greeting: str = ""
    llm_model: str = "doubao-seed-1.6-250615"
    temperature: float = 0.7
    max_tokens: int = 2048
    stt: STTConfig = None  # type: ignore[assignment]
    tts: TTSConfig = None  # type: ignore[assignment]
    skills: list[str] | None = None

    def __post_init__(self) -> None:
        if self.stt is None:
            self.stt = STTConfig()
        if self.tts is None:
            self.tts = TTSConfig()


# Sentence break heuristic — flush TTS at sentence boundaries to keep
# audio playback latency low. Splits on `.`, `?`, `!`, or newline.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?。！？])\s+|\n+")


@dataclass
class TurnEvent:
    kind: str  # "user_partial" | "user_final" | "assistant_token" | "assistant_audio" |
              # "assistant_done" | "skill_call" | "skill_result" | "interrupt" | "error"
    text: str = ""
    audio: bytes = b""
    sample_rate: int = 24000
    encoding: str = "pcm16"
    data: dict[str, Any] | None = None


class VoiceSession:
    """One conversation = one VoiceSession."""

    def __init__(
        self,
        *,
        stt: STTProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        config: SessionConfig,
        skill_runner: SkillRunner | None = None,
    ) -> None:
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._cfg = config
        self._skills = skill_runner or SkillRunner(skill_ids=config.skills or [])
        self._history: list[LLMMessage] = [LLMMessage(role="system", content=config.system_prompt)]
        self._inbound: asyncio.Queue[AudioChunk | None] = asyncio.Queue(maxsize=128)
        self._cancel_tts: asyncio.Event = asyncio.Event()
        # Once TTS errors out for a turn, stop hammering the API for every
        # remaining sentence — surface a single friendly error and let the
        # text response complete.
        self._tts_disabled_for_turn: bool = False
        self._tts_error_emitted: bool = False

    # ── Public: feed audio in, get events out ────────────────────────
    async def push_audio(self, chunk: AudioChunk) -> None:
        await self._inbound.put(chunk)

    async def end_audio(self) -> None:
        await self._inbound.put(None)

    async def run(self) -> AsyncIterator[TurnEvent]:
        """Drive the pipeline. Yields TurnEvents until the session ends."""
        # Optional greeting before the user speaks.
        if self._cfg.greeting:
            async for ev in self._speak(self._cfg.greeting):
                yield ev
            yield TurnEvent(kind="assistant_done", text=self._cfg.greeting)

        while True:
            # Listen until STT gives us a final utterance.
            user_text = ""
            async for ev in self._listen_one_turn():
                if ev.kind == "user_final":
                    user_text = ev.text
                yield ev
                if ev.kind == "user_final":
                    break
                if ev.kind == "error":
                    return
            if not user_text:
                # End of audio with nothing to say.
                return

            # Run the LLM turn (with optional skill loop). Reset the
            # per-turn TTS error gate so a previous failure doesn't keep
            # audio disabled forever.
            self._tts_disabled_for_turn = False
            self._tts_error_emitted = False
            self._history.append(LLMMessage(role="user", content=user_text))
            full_assistant = ""
            async for ev in self._llm_turn():
                if ev.kind == "assistant_token":
                    full_assistant += ev.text
                yield ev

            self._history.append(LLMMessage(role="assistant", content=full_assistant))
            yield TurnEvent(kind="assistant_done", text=full_assistant)

    # ── Internals ────────────────────────────────────────────────────
    async def _audio_iterator(self) -> AsyncIterator[AudioChunk]:
        while True:
            chunk = await self._inbound.get()
            if chunk is None:
                return
            yield chunk

    async def _listen_one_turn(self) -> AsyncIterator[TurnEvent]:
        try:
            async for r in self._stt.transcribe_stream(self._audio_iterator(), self._cfg.stt):
                yield TurnEvent(
                    kind="user_final" if r.is_final else "user_partial",
                    text=r.text,
                    data={"confidence": r.confidence, "language": r.language},
                )
                if r.is_final:
                    return
        except Exception as e:
            logger.exception("stt error")
            yield TurnEvent(kind="error", text=str(e))

    async def _llm_turn(self) -> AsyncIterator[TurnEvent]:
        cfg = LLMConfig(
            model=self._cfg.llm_model,
            temperature=self._cfg.temperature,
            max_tokens=self._cfg.max_tokens,
            stream=True,
            tools=self._skills.tool_specs() or None,
        )
        try:
            buffer = ""
            # OpenAI-style streaming sends tool_call arguments in fragments
            # (one chunk per few characters of JSON). We must accumulate
            # them by `index` to get the final list of well-formed calls.
            tool_calls_by_idx: dict[int, dict[str, Any]] = {}

            async for chunk in self._llm.chat_stream(self._history, cfg):
                if chunk.tool_calls:
                    _merge_tool_call_deltas(tool_calls_by_idx, chunk.tool_calls)
                if chunk.delta:
                    buffer += chunk.delta
                    yield TurnEvent(kind="assistant_token", text=chunk.delta)
                    # Flush completed sentences to TTS for low latency.
                    while True:
                        m = _SENTENCE_BREAK.search(buffer)
                        if not m:
                            break
                        sentence = buffer[: m.end()].strip()
                        buffer = buffer[m.end():]
                        if sentence:
                            async for ev in self._speak(sentence):
                                yield ev
                if chunk.finish_reason:
                    break
            # Flush trailing buffer.
            if buffer.strip():
                async for ev in self._speak(buffer.strip()):
                    yield ev

            tool_calls = _finalise_tool_calls(tool_calls_by_idx)
            if tool_calls:
                # OpenAI / Ark protocol requires the assistant message that
                # *issued* the tool_calls to appear in history before the
                # tool reply. Without it the next request returns 400.
                self._history.append(
                    LLMMessage(role="assistant", content=buffer, tool_calls=tool_calls)
                )

                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except json.JSONDecodeError:
                        parsed_args = {"_raw": raw_args}
                    if not isinstance(parsed_args, dict):
                        parsed_args = {"_value": parsed_args}

                    yield TurnEvent(kind="skill_call", text=name, data=parsed_args)
                    result = await self._skills.invoke(name, parsed_args)
                    yield TurnEvent(kind="skill_result", text=name, data=result)

                    # Tool-result message must reference the call's id, not
                    # the function name (OpenAI / Ark contract).
                    self._history.append(
                        LLMMessage(
                            role="tool",
                            tool_call_id=tc.get("id") or "",
                            name=name,
                            content=json.dumps(result, ensure_ascii=False),
                        )
                    )
                # Recurse — let the LLM continue with the tool result.
                async for ev in self._llm_turn():
                    yield ev
        except Exception as e:
            logger.exception("llm error")
            yield TurnEvent(kind="error", text=str(e))


    async def _speak(self, sentence: str) -> AsyncIterator[TurnEvent]:
        # If TTS already failed in this turn, stop retrying — the LLM text
        # will still finish but we won't generate audio.
        if self._tts_disabled_for_turn:
            return
        try:
            async for chunk in self._tts.synthesize_stream(sentence, self._cfg.tts):
                if self._cancel_tts.is_set():
                    self._cancel_tts.clear()
                    yield TurnEvent(kind="interrupt")
                    return
                yield TurnEvent(
                    kind="assistant_audio",
                    audio=chunk.data,
                    sample_rate=chunk.sample_rate,
                    encoding=chunk.encoding,
                )
        except Exception as e:
            logger.exception("tts error")
            self._tts_disabled_for_turn = True
            if not self._tts_error_emitted:
                self._tts_error_emitted = True
                # `tts_error` is a soft failure — text continues, audio
                # doesn't. The dashboard surfaces this in the transcript.
                yield TurnEvent(
                    kind="tts_error",
                    text=str(e),
                    data={"hint": "Set BYTEPLUS_TTS_DEFAULT_VOICE in .env to a voice your BytePlus key is licensed for, or change the agent's Voice ID."},
                )

    def interrupt(self) -> None:
        """User started speaking again — cut current playback."""
        self._cancel_tts.set()


# ──────────────────────────────────────────────────────────────────────
# Module-level helpers
# ──────────────────────────────────────────────────────────────────────


def _merge_tool_call_deltas(
    acc: dict[int, dict[str, Any]],
    deltas: list[dict[str, Any]],
) -> None:
    """Merge a streaming tool_calls delta list into `acc` (keyed by index).

    A delta looks like one of:
        {"index": 0, "id": "call_abc", "type": "function",
         "function": {"name": "lookup_order", "arguments": ""}}
        {"index": 0, "function": {"arguments": "{\\"order_id"}}
        {"index": 0, "function": {"arguments": "\\":\\"ORD-1001\\"}"}}
    """
    for d in deltas:
        idx = d.get("index", 0)
        slot = acc.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        if d.get("id"):
            slot["id"] = d["id"]
        if d.get("type"):
            slot["type"] = d["type"]
        fn_d = d.get("function") or {}
        fn_s = slot.setdefault("function", {"name": "", "arguments": ""})
        if fn_d.get("name"):
            fn_s["name"] = fn_d["name"]
        if "arguments" in fn_d and fn_d["arguments"] is not None:
            fn_s["arguments"] = (fn_s.get("arguments") or "") + fn_d["arguments"]


def _finalise_tool_calls(acc: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the merged tool_calls in deterministic index order."""
    out: list[dict[str, Any]] = []
    for idx in sorted(acc):
        tc = acc[idx]
        # Drop calls with no name — they're spurious deltas.
        if not (tc.get("function") or {}).get("name"):
            continue
        # Ensure every call has an id (some providers omit it on the
        # final delta only; generate a synthetic one if needed).
        if not tc.get("id"):
            tc["id"] = f"call_{idx}_{int(time.time() * 1000) % 100000}"
        out.append(tc)
    return out
