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
from openvox.providers.vad.base import VADConfig, VADProvider
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
    # Multilingual: optional map of BCP-47 short codes → speaker voice id.
    # When the STT-detected language matches a key, `_speak()` uses that
    # voice instead of `tts.voice_id`. Empty map = always use `tts.voice_id`.
    voice_map: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.stt is None:
            self.stt = STTConfig()
        if self.tts is None:
            self.tts = TTSConfig()
        if self.voice_map is None:
            self.voice_map = {}


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
        vad: VADProvider | None = None,
    ) -> None:
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._vad = vad
        self._cfg = config
        self._skills = skill_runner or SkillRunner(skill_ids=config.skills or [])
        self._history: list[LLMMessage] = [LLMMessage(role="system", content=config.system_prompt)]
        # We tee inbound audio into two queues: STT reads one (slow but
        # transcribes), VAD reads the other (fast but only detects voice).
        # If no VAD provider is configured, the VAD queue stays unused and
        # the second `put` is a near-zero cost.
        self._inbound: asyncio.Queue[AudioChunk | None] = asyncio.Queue(maxsize=128)
        self._vad_inbound: asyncio.Queue[AudioChunk | None] = asyncio.Queue(maxsize=128)
        self._cancel_tts: asyncio.Event = asyncio.Event()
        # Track whether we're currently mid-speak so the VAD task can
        # decide whether a speech_start should actually trigger interrupt.
        # We only want to barge in if we're actively producing TTS audio —
        # interrupting the user while they're talking to us is nonsense.
        self._speaking: bool = False
        # Background task that drains the VAD stream. Lives for the whole
        # session lifetime and is cancelled in run()'s `finally`.
        self._vad_task: asyncio.Task | None = None
        # Most recent server-side VAD timestamp of speech_start, in
        # monotonic ms. Useful for measuring interrupt latency in tests
        # (see scripts/measure_interrupt.py).
        self._last_vad_speech_start_ms: int = 0
        # Once TTS errors out for a turn, stop hammering the API for every
        # remaining sentence — surface a single friendly error and let the
        # text response complete.
        self._tts_disabled_for_turn: bool = False
        self._tts_error_emitted: bool = False
        # Multilingual support: the last STT-detected language for this
        # session. Used to (a) pick the matching TTS voice from `voice_map`
        # at speak time, and (b) inform the `detect_language` skill via the
        # SkillRunner's ctx.metadata.
        self._last_language: str = config.tts.language or "en-US"
        self._last_language_confidence: float = 1.0
        # Inject the initial language into any skill context the runner has.
        if hasattr(self._skills, "_ctx") and self._skills._ctx is not None:
            self._skills._ctx.metadata.setdefault("last_language", self._last_language)
            self._skills._ctx.metadata.setdefault("last_language_confidence", 1.0)

    # ── Public: feed audio in, get events out ────────────────────────
    async def push_audio(self, chunk: AudioChunk) -> None:
        await self._inbound.put(chunk)
        # Tee to VAD if active. Use put_nowait + drop on overflow rather
        # than block — VAD lagging shouldn't backpressure the STT path.
        if self._vad_task is not None:
            try:
                self._vad_inbound.put_nowait(chunk)
            except asyncio.QueueFull:
                logger.debug("vad queue full — dropping frame for detection")

    async def end_audio(self) -> None:
        await self._inbound.put(None)
        if self._vad_task is not None:
            try:
                self._vad_inbound.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def run(self) -> AsyncIterator[TurnEvent]:
        """Drive the pipeline. Yields TurnEvents until the session ends."""
        # Spin up the VAD background task if a provider was supplied and
        # is actually available (silero-vad may be missing in some envs).
        if self._vad is not None and self._vad.is_available():
            await self._vad.warmup()
            self._vad_task = asyncio.create_task(self._vad_loop())

        try:
            async for ev in self._run_inner():
                yield ev
        finally:
            if self._vad_task is not None:
                self._vad_task.cancel()
                try:
                    await self._vad_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _run_inner(self) -> AsyncIterator[TurnEvent]:
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

    async def _vad_audio_iterator(self) -> AsyncIterator[AudioChunk]:
        while True:
            chunk = await self._vad_inbound.get()
            if chunk is None:
                return
            yield chunk

    async def _vad_loop(self) -> None:
        """Drain the VAD provider's event stream for the whole session.

        On `speech_start`, if we're currently producing TTS audio
        (`_speaking == True`), set the cancel flag so the in-flight TTS
        stream aborts on its next iteration. We do NOT interrupt when
        the user is the one talking (the natural case during _listen_one_turn)
        because the user-side speech is what STT is *supposed* to be
        eating right now.
        """
        if self._vad is None:
            return
        try:
            async for ev in self._vad.detect_stream(self._vad_audio_iterator(), VADConfig()):
                if ev.kind == "speech_start":
                    self._last_vad_speech_start_ms = ev.timestamp_ms
                    if self._speaking:
                        logger.debug(
                            "vad: speech_start at %d ms (prob=%.2f) — interrupting",
                            ev.timestamp_ms, ev.prob,
                        )
                        self._cancel_tts.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            # VAD lives in the background — if it goes sideways, log and
            # exit quietly. The main pipeline still works without it.
            logger.exception("vad loop crashed; falling back to client-driven interrupt")

    async def _listen_one_turn(self) -> AsyncIterator[TurnEvent]:
        try:
            async for r in self._stt.transcribe_stream(self._audio_iterator(), self._cfg.stt):
                # Track the most recent detected language so _speak() can
                # pick a matching voice and `detect_language` skill can
                # report it back to the LLM.
                if r.language:
                    self._last_language = r.language
                    self._last_language_confidence = r.confidence
                    if hasattr(self._skills, "_ctx") and self._skills._ctx is not None:
                        self._skills._ctx.metadata["last_language"] = r.language
                        self._skills._ctx.metadata["last_language_confidence"] = r.confidence
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

                    yield TurnEvent(kind="skill_call", text=name, data={"args": parsed_args})
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
        # Multilingual support: if the agent's voice_map has an entry for
        # the currently-detected language, swap the speaker for this
        # utterance. Falls through to the configured voice_id otherwise.
        tts_cfg = self._cfg.tts
        vm = self._cfg.voice_map or {}
        if vm:
            short = (self._last_language or "").split("-")[0].lower()
            picked = vm.get(short) or vm.get(self._last_language)
            if picked and picked != tts_cfg.voice_id:
                # Use a copy so we don't mutate the shared config.
                from dataclasses import replace
                tts_cfg = replace(tts_cfg, voice_id=picked, language=self._last_language)
        # `_speaking` toggles the VAD background task's decision: a
        # speech_start while `_speaking == True` means user is barging in,
        # so we cancel TTS. Outside this block, user audio is the expected
        # input (during _listen_one_turn) and shouldn't trigger interrupt.
        self._speaking = True
        try:
            async for chunk in self._tts.synthesize_stream(sentence, tts_cfg):
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
        finally:
            # Important: clear the speaking flag before returning so a
            # follow-up user utterance during _listen_one_turn doesn't
            # get treated as an interrupt-mid-TTS.
            self._speaking = False

    def interrupt(self) -> None:
        """User started speaking again — cut current playback."""
        self._cancel_tts.set()

    # ── Observability ────────────────────────────────────────────────
    @property
    def last_vad_speech_start_ms(self) -> int:
        """Monotonic timestamp (ms) of the most recent VAD speech_start.

        Read by `scripts/measure_interrupt.py` to compute interrupt
        latency: (time TTS cancel event is observed) - (this value).
        """
        return self._last_vad_speech_start_ms


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
