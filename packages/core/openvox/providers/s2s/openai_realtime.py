"""OpenAI Realtime adapter — Speech-to-Speech via wss://api.openai.com/v1/realtime.

Translates OpenAI's Realtime event protocol (30+ event types) down to
the canonical ``S2SEvent`` shape (8 kinds) the orchestrator expects.
The translation table:

  OpenAI Realtime event                                 → S2SEvent.kind
  ─────────────────────────────────────────────────────  ──────────────────────
  conversation.item.input_audio_transcription.completed  user_final
  conversation.item.input_audio_transcription.delta      user_partial
  response.audio_transcript.delta                        assistant_text
  response.audio.delta                                   assistant_audio
  response.function_call_arguments.done                  tool_call
  input_audio_buffer.speech_started                      speech_started
  response.done                                          response_done
  error                                                  error

Everything else is logged at DEBUG and ignored — Realtime emits a lot
of bookkeeping events (`session.created`, `rate_limits.updated`,
`response.created`, etc.) that the orchestrator doesn't need.

Audio framing:
  Realtime expects PCM16 24 kHz mono, base64-encoded inside the
  ``input_audio_buffer.append`` event's ``audio`` field. Output audio
  arrives the same way. The session's ``push_audio`` accepts raw PCM
  bytes and handles the encode; ``events()`` decodes incoming audio
  back to raw bytes before yielding.

Tool calling:
  Realtime supports OpenAI-format tool specs natively — pass them
  via ``S2SConfig.tools`` and they're injected into the session via
  ``session.update``. Tool calls arrive as
  ``response.function_call_arguments.done`` events; reply with
  ``conversation.item.create`` (role=function_call_output) + a
  ``response.create`` to resume.

Interrupt:
  Two signals matter:
    1. Server-side VAD fires `input_audio_buffer.speech_started`
       while the assistant is mid-response. We forward this as
       ``S2SEvent(kind="speech_started")`` so the orchestrator can
       stop forwarding assistant audio to the WS client. Realtime
       cancels its own response automatically.
    2. Explicit cancel (e.g., dashboard Stop button) — we send
       `response.cancel`.

Failure modes:
  - No API key → ``is_available()`` returns False, registry skips.
  - WS disconnect mid-session → ``events()`` raises, orchestrator's
    finally block tears the session down and falls back to the
    pipeline (Phase 3.3's branch logic).
  - Rate limit (429) on connect → adapter raises RuntimeError with
    the Realtime API's error message verbatim.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from openvox.config import get_settings
from openvox.providers.base import (
    ProviderCapability,
    ProviderType,
    S2SConfig,
    S2SEvent,
    S2SProvider,
    S2SSession,
)
from openvox.utils.http import certifi_ssl_context

logger = logging.getLogger(__name__)


_WS_URL = "wss://api.openai.com/v1/realtime"
_DEFAULT_MODEL = "gpt-4o-realtime-preview-2024-12-17"


class OpenAIRealtimeProvider(S2SProvider):
    id = "openai_realtime"
    type = ProviderType.S2S
    display_name = "OpenAI Realtime"
    capabilities = frozenset(
        {
            ProviderCapability.STREAMING,
            ProviderCapability.BIDIRECTIONAL,
            ProviderCapability.INTERRUPTION,
            ProviderCapability.TOOL_CALLING,
        }
    )

    def __init__(self) -> None:
        # Snapshot the key at construction time, like other providers.
        # The lifespan hydration step (api/app.py::_hydrate_secrets_into_env)
        # runs BEFORE register_builtins(), so this picks up wizard-entered
        # keys without a daemon restart (see CLAUDE.md #77 + #78).
        settings = get_settings()
        self._api_key = (settings.openai_api_key or "").strip()
        # Adapter's canonical default model. The session uses
        # ``S2SConfig.model`` when present, falls back here.
        self._default_model = _DEFAULT_MODEL

    def is_available(self) -> bool:
        return bool(self._api_key)

    def connect(self, config: S2SConfig) -> "_OpenAIRealtimeSession":
        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set — Realtime requires a real OpenAI key "
                "(not BytePlus or other OpenAI-compatible)."
            )
        model = (config.model or "").strip() or self._default_model
        return _OpenAIRealtimeSession(
            api_key=self._api_key, model=model, config=config
        )


class _OpenAIRealtimeSession(S2SSession):
    """Per-call WS session against Realtime.

    Spawns one background task that reads from the socket and routes
    events to an internal queue; ``events()`` drains that queue. This
    decouples the send and recv halves of the WS so ``push_audio``
    isn't blocked behind whatever the server is sending us right now.
    """

    def __init__(self, *, api_key: str, model: str, config: S2SConfig) -> None:
        self._api_key = api_key
        self._model = model
        self._config = config
        self._ws = None
        self._recv_task: asyncio.Task | None = None
        # Bounded queue so a slow consumer can't make us OOM in a
        # rapid-fire response burst.
        self._events_queue: asyncio.Queue[S2SEvent] = asyncio.Queue(maxsize=256)
        self._closed = asyncio.Event()

    # ── Lifecycle ────────────────────────────────────────────────

    async def __aenter__(self) -> "_OpenAIRealtimeSession":
        import websockets

        url = f"{_WS_URL}?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        kwargs = {"max_size": 16 * 1024 * 1024, "ssl": certifi_ssl_context()}
        # Newer + older websockets API parameter rename — same dance
        # CLAUDE.md's existing BytePlus STT adapter does (#13 area).
        try:
            self._ws = await websockets.connect(url, additional_headers=headers, **kwargs)
        except TypeError:
            self._ws = await websockets.connect(url, extra_headers=headers, **kwargs)  # type: ignore[arg-type]

        # Push session config via `session.update`. We do this once
        # at handshake rather than incrementally — Realtime's
        # session-level config is sticky.
        update_payload: dict[str, Any] = {
            "modalities": ["audio", "text"],
            "voice": self._config.voice or "alloy",
            "instructions": self._config.instructions or "",
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
            "turn_detection": {
                "type": "server_vad",
                "threshold": float(self._config.vad_threshold),
                # 200 ms of silence ends a turn — empirically the
                # sweet spot for back-and-forth feel without
                # clipping mid-sentence pauses.
                "silence_duration_ms": 200,
            },
            "temperature": float(self._config.temperature),
        }
        if self._config.tools:
            # Realtime accepts OpenAI tool-spec shape directly.
            update_payload["tools"] = self._normalise_tools(self._config.tools)
            update_payload["tool_choice"] = "auto"

        await self._send({"type": "session.update", "session": update_payload})

        # Start the background receiver. Caller pulls events via
        # ``events()`` which drains the same queue.
        self._recv_task = asyncio.create_task(self._recv_loop())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._closed.set()
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── Outbound (orchestrator → Realtime) ───────────────────────

    async def push_audio(self, pcm_bytes: bytes) -> None:
        """Append a PCM chunk to the input buffer.

        Realtime's `input_audio_buffer.append` event takes base64-
        encoded audio. We don't resample — the orchestrator is
        expected to send 24 kHz PCM16 per the contract in
        ``S2SConfig.sample_rate_in``. Future adapters for providers
        that want different rates can do the conversion here.
        """
        if not pcm_bytes:
            return
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm_bytes).decode("ascii"),
            }
        )

    async def commit_audio(self) -> None:
        """Explicit "end of input" for callers without server VAD.

        Realtime's default ``turn_detection: server_vad`` makes this a
        no-op in most cases — the server commits on its own when it
        detects end-of-speech. We still expose it for clients that
        disable server VAD via the config (Phase 3.4 may add a
        dashboard toggle for this).
        """
        await self._send({"type": "input_audio_buffer.commit"})

    async def submit_tool_result(self, call_id: str, output: Any) -> None:
        """Push a skill's result back into the conversation."""
        body = output if isinstance(output, str) else json.dumps(output)
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": body,
                },
            }
        )
        # Resume — without this Realtime waits indefinitely for an
        # explicit response.create after every tool result.
        await self._send({"type": "response.create"})

    async def interrupt(self) -> None:
        """Explicit cancel — for the dashboard Stop button case.

        Server-VAD interrupts are handled automatically (Realtime
        cancels its own response when speech_started fires mid-
        response). This is the manual path: user clicks Stop, the
        client WS forwards an `interrupt` control message, the
        voice WS handler calls this.
        """
        await self._send({"type": "response.cancel"})

    # ── Inbound (Realtime → orchestrator) ────────────────────────

    async def events(self) -> AsyncIterator[S2SEvent]:
        """Drain normalised events.

        Single consumer per session. The background receiver task is
        feeding the queue; this just dequeues + yields. Iteration
        ends when the session closes (queue gets a sentinel error
        event followed by the connection cleanup in __aexit__).
        """
        while not self._closed.is_set():
            try:
                ev = await asyncio.wait_for(self._events_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Periodic timeout so __aexit__ can clean up promptly
                # even if the queue is permanently empty.
                continue
            yield ev
            if ev.kind == "error":
                # Surface the error but keep iterating — the caller
                # decides whether to bail. (Realtime occasionally
                # emits recoverable errors mid-session.)
                continue

    # ── Internal ─────────────────────────────────────────────────

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("S2S session not open — call __aenter__ first")
        await self._ws.send(json.dumps(payload))

    async def _recv_loop(self) -> None:
        """Background reader — translates Realtime events into S2SEvents.

        Exits when the WS closes (clean or otherwise). On exit the
        queue receives a final error event so any pending events()
        iterator wakes up and sees the close.
        """
        import websockets.exceptions as wse

        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("realtime: non-JSON frame ignored: %r", raw[:80])
                    continue
                ev = self._normalise(msg)
                if ev is not None:
                    try:
                        self._events_queue.put_nowait(ev)
                    except asyncio.QueueFull:
                        # Drop assistant_audio chunks first — losing
                        # one is recoverable; losing a tool_call is not.
                        if ev.kind == "assistant_audio":
                            logger.warning("realtime: queue full, dropped audio chunk")
                            continue
                        # For non-audio events, wait it out.
                        await self._events_queue.put(ev)
        except wse.ConnectionClosedOK:
            logger.info("realtime: connection closed cleanly")
        except wse.ConnectionClosedError as e:
            logger.warning("realtime: connection closed with error: %s", e)
            try:
                self._events_queue.put_nowait(
                    S2SEvent(kind="error", text=f"connection lost: {e}")
                )
            except asyncio.QueueFull:
                pass
        except Exception as e:
            logger.exception("realtime: recv loop crashed: %s", e)
            try:
                self._events_queue.put_nowait(S2SEvent(kind="error", text=str(e)))
            except asyncio.QueueFull:
                pass
        finally:
            self._closed.set()

    def _normalise(self, msg: dict[str, Any]) -> S2SEvent | None:
        """One translation step: Realtime envelope → S2SEvent.

        Returns None for the bookkeeping events the orchestrator
        doesn't care about (session.created, rate_limits.updated,
        response.created, response.audio.done, etc.).
        """
        kind = msg.get("type", "")

        if kind == "response.audio.delta":
            data = msg.get("delta", "")
            try:
                audio = base64.b64decode(data) if data else b""
            except Exception:
                logger.warning("realtime: bad base64 audio delta, dropped")
                return None
            return S2SEvent(
                kind="assistant_audio",
                audio=audio,
                sample_rate=self._config.sample_rate_out,
            )

        if kind == "response.audio_transcript.delta":
            return S2SEvent(kind="assistant_text", text=msg.get("delta", ""))

        if kind == "conversation.item.input_audio_transcription.delta":
            return S2SEvent(kind="user_partial", text=msg.get("delta", ""))

        if kind == "conversation.item.input_audio_transcription.completed":
            return S2SEvent(kind="user_final", text=msg.get("transcript", ""))

        if kind == "response.function_call_arguments.done":
            # Realtime sends accumulated args as a JSON string.
            args_raw = msg.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            return S2SEvent(
                kind="tool_call",
                data={
                    "name": msg.get("name", ""),
                    "args": args,
                    "call_id": msg.get("call_id", ""),
                },
            )

        if kind == "input_audio_buffer.speech_started":
            return S2SEvent(kind="speech_started")

        if kind == "response.done":
            return S2SEvent(kind="response_done")

        if kind == "error":
            err = msg.get("error", {})
            return S2SEvent(
                kind="error",
                text=err.get("message", "unknown Realtime error"),
                data={"code": err.get("code", ""), "type": err.get("type", "")},
            )

        # Bookkeeping events — drop quietly.
        logger.debug("realtime: ignored event %s", kind)
        return None

    @staticmethod
    def _normalise_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Project our `runner.tool_specs()` shape onto Realtime's.

        Both use OpenAI-compatible function-call schemas, BUT Realtime
        wants the spec FLAT (no `{"type": "function", "function": …}`
        wrapper that chat completions uses). Translate accordingly.
        """
        out = []
        for t in tools:
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                out.append(
                    {
                        "type": "function",
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    }
                )
            else:
                # Already flat — pass through.
                out.append(t)
        return out
