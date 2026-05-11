"""BytePlus Seed ASR 2.0 — streaming WebSocket + audio-file (batch) APIs.

Reference: BytePlus voice docs (Streaming WS / Audio-file).

────────────────────────────────────────────────────────────────────────
Streaming WebSocket
────────────────────────────────────────────────────────────────────────
Endpoint:
    wss://voice.ap-southeast-1.bytepluses.com/api/v3/sauc/bigmodel_async
    (the optimized bidirectional mode — ASR 2.0)

Headers (sent on the WS upgrade):
    X-Api-Key:         <BYTEPLUS_VOICE_API_KEY>
    X-Api-Resource-Id: volc.seedasr.sauc.duration
    X-Api-Connect-Id:  <uuid>

Wire protocol — every WS message is binary, framed as:

    +-----------------------+
    | header (4 bytes)      |
    +-----------------------+
    | [sequence (4 bytes)]  |  ← only present when flags bit 0 is set
    +-----------------------+
    | payload size (BE u32) |
    +-----------------------+
    |       payload         |
    +-----------------------+

Header bits:
    byte 0 hi: protocol version (0b0001 = v1)
    byte 0 lo: header size in 4-byte units (0b0001 = 4 bytes)
    byte 1 hi: message type
                 0b0001 full client request
                 0b0010 audio-only request
                 0b1001 full server response
                 0b1111 error response
    byte 1 lo: message-type-specific flags
                 bit 0: 1 → sequence number is encoded in the frame
                 bit 1: 1 → this is the last audio packet (request) or
                            last response (response)
    byte 2 hi: serialization (0b0000 raw, 0b0001 JSON)
    byte 2 lo: compression  (0b0000 none, 0b0001 gzip)
    byte 3   : reserved (zero)

The first request is `full client request` carrying the JSON config.
Subsequent requests are `audio-only` carrying raw PCM (or gzipped).
Responses share the same framing; we parse the JSON payload and surface
sentence-level results to the orchestrator.

────────────────────────────────────────────────────────────────────────
Audio-file (batch) recognition
────────────────────────────────────────────────────────────────────────
Two HTTP POSTs:
    /api/v3/auc/bigmodel/submit   →  task accepted (no body)
    /api/v3/auc/bigmodel/query    →  poll until X-Api-Status-Code = 20000000

Resource ID for ASR 2.0 batch is `volc.seedasr.auc`.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import struct
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets

from openvox.config import get_settings
from openvox.utils.http import certifi_ssl_context, make_async_client
from openvox.providers.base import (
    AudioChunk,
    ProviderCapability,
    STTConfig,
    STTProvider,
    STTResult,
)

logger = logging.getLogger(__name__)

_WS_URL = "wss://voice.ap-southeast-1.bytepluses.com/api/v3/sauc/bigmodel_async"
_FILE_SUBMIT_URL = "https://voice.ap-southeast-1.bytepluses.com/api/v3/auc/bigmodel/submit"
_FILE_QUERY_URL = "https://voice.ap-southeast-1.bytepluses.com/api/v3/auc/bigmodel/query"

_RESOURCE_ID_STREAM = "volc.seedasr.sauc.duration"
_RESOURCE_ID_FILE = "volc.seedasr.auc"


# ── frame helpers ───────────────────────────────────────────────
def _frame_full_request(payload: bytes) -> bytes:
    # version=1, header_size=1, msg_type=0x01, flags=0x00, serialization=JSON,
    # compression=none, reserved=0
    header = bytes([0x11, 0x10, 0x10, 0x00])
    return header + struct.pack(">I", len(payload)) + payload


def _frame_audio(audio: bytes, *, is_last: bool = False) -> bytes:
    # msg_type=0x02 (audio), flags=0x02 if last else 0x00 — bit 1 = last packet,
    # bit 0 = sequence (we don't send sequences from client)
    flags = 0x02 if is_last else 0x00
    header = bytes([0x11, (0x02 << 4) | flags, 0x00, 0x00])
    return header + struct.pack(">I", len(audio)) + audio


def _parse_response(data: bytes) -> dict[str, Any]:
    """Parse a server frame. Handles full-response, error, and the optional
    4-byte sequence prefix when flags bit 0 is set."""
    if len(data) < 4:
        return {}
    header_size = (data[0] & 0x0F) * 4
    msg_type = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    serialization = (data[2] >> 4) & 0x0F
    compression = data[2] & 0x0F

    offset = header_size

    # Error frame: header + error_code(4) + error_size(4) + utf8 message.
    if msg_type == 0x0F:
        if len(data) < offset + 8:
            return {}
        err_code = struct.unpack(">I", data[offset : offset + 4])[0]
        err_size = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        msg = data[offset + 8 : offset + 8 + err_size].decode("utf-8", "replace")
        return {"_error_code": err_code, "_error_msg": msg, "_is_error": True}

    # Full server response — sequence comes first if flags bit 0 set.
    if flags & 0x01:
        if len(data) < offset + 4:
            return {}
        # We don't currently surface the sequence number to callers, but
        # we MUST consume it — that was the bug.
        offset += 4

    if len(data) < offset + 4:
        return {}
    payload_size = struct.unpack(">I", data[offset : offset + 4])[0]
    payload = data[offset + 4 : offset + 4 + payload_size]

    if compression == 0x01:
        try:
            payload = gzip.decompress(payload)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("gzip decode failed: %s", e)
            return {}

    if serialization == 0x01:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}

    return {}


def _extract_result(obj: dict[str, Any]) -> tuple[str, bool, float, str, list]:
    """Pull (text, is_final, confidence, language, utterances) out of a
    response payload. Server wraps the recognition in payload_msg.result."""
    res = (obj.get("payload_msg") or {}).get("result") or obj.get("result") or {}
    text = res.get("text") or ""
    utterances = res.get("utterances") or []
    additions = res.get("additions") or {}
    confidence = float(additions.get("confidence") or 0.0)
    language = res.get("language") or additions.get("language") or ""

    # In bigmodel_async dual-pass mode, the "definite": true flag marks
    # utterances that were re-recognised by the non-streaming pass —
    # those are our true finals. Otherwise, fall back to is_last_package.
    has_definite = any(u.get("definite") for u in utterances)
    is_last_package = bool(obj.get("is_last_package"))
    is_final = has_definite or is_last_package

    return text, is_final, confidence, language, utterances


class BytePlusSTT(STTProvider):
    """BytePlus Seed ASR 2.0 — streaming + audio-file."""

    id = "byteplus"
    display_name = "BytePlus Seed ASR 2.0"
    capabilities = {ProviderCapability.STREAMING, ProviderCapability.LANGUAGE_DETECT}

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.byteplus_voice_api_key

    def is_available(self) -> bool:
        return bool(self._api_key)

    # ── Streaming ───────────────────────────────────────────────
    async def transcribe_stream(
        self, audio: AsyncIterator[AudioChunk], config: STTConfig
    ) -> AsyncIterator[STTResult]:
        if not self.is_available():
            raise RuntimeError("BYTEPLUS_VOICE_API_KEY is not set")

        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": _RESOURCE_ID_STREAM,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

        kwargs = {"max_size": 16 * 1024 * 1024, "ssl": certifi_ssl_context()}
        try:
            ws = await websockets.connect(_WS_URL, additional_headers=headers, **kwargs)
        except TypeError:
            ws = await websockets.connect(_WS_URL, extra_headers=headers, **kwargs)  # type: ignore[arg-type]

        async with ws:
            # Initial JSON config — uses the audio.* and request.* shape from
            # the BytePlus streaming docs. enable_nonstream gives us dual-pass
            # (streaming partials + accurate non-streaming finals) which is
            # the recommended mode for Seed ASR 2.0.
            start = {
                "user": {"uid": str(uuid.uuid4())},
                "audio": {
                    "format": "pcm",
                    "codec": "raw",
                    "rate": config.sample_rate,
                    "bits": 16,
                    "channel": 1,
                },
                "request": {
                    "model_name": "bigmodel",
                    "enable_itn": True,
                    "enable_punc": True,
                    "enable_ddc": True,
                    "show_utterances": True,
                    "enable_nonstream": True,  # dual-pass for accurate finals
                    "result_type": "full",
                    "end_window_size": 800,
                },
            }
            await ws.send(_frame_full_request(json.dumps(start).encode("utf-8")))

            send_done = asyncio.Event()
            seen_definite_text = ""

            async def pump_audio() -> None:
                pending = b""
                async for chunk in audio:
                    if not chunk.data:
                        continue
                    if pending:
                        await ws.send(_frame_audio(pending, is_last=False))
                    pending = chunk.data
                    if chunk.is_final:
                        await ws.send(_frame_audio(pending, is_last=True))
                        pending = b""
                if pending:
                    await ws.send(_frame_audio(pending, is_last=True))
                send_done.set()

            pump = asyncio.create_task(pump_audio())
            try:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        if send_done.is_set():
                            return
                        continue
                    except websockets.exceptions.ConnectionClosedOK:
                        # Server finished processing and closed cleanly — that's
                        # the normal end-of-stream signal, not an error.
                        return
                    except websockets.exceptions.ConnectionClosedError as e:
                        logger.warning("byteplus stt closed unexpectedly: %s", e)
                        return

                    if isinstance(raw, str):
                        # Some intermediaries downgrade to text. Treat as JSON.
                        try:
                            obj = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                    else:
                        obj = _parse_response(raw)

                    if not obj:
                        continue
                    if obj.get("_is_error"):
                        yield STTResult(
                            text="", is_final=True,
                            language=config.language,
                            raw={"error": obj.get("_error_msg"), "code": obj.get("_error_code")},
                        )
                        return

                    text, is_final, confidence, language, _ = _extract_result(obj)
                    if not text:
                        # Skip empty chunks (server can send heartbeats / acks).
                        if obj.get("is_last_package") and send_done.is_set():
                            return
                        continue

                    # Avoid emitting the same finalised text twice — partials
                    # often re-include text already promoted to a definite
                    # utterance.
                    if is_final:
                        if text == seen_definite_text:
                            if obj.get("is_last_package") and send_done.is_set():
                                return
                            continue
                        seen_definite_text = text

                    yield STTResult(
                        text=text,
                        is_final=is_final,
                        confidence=confidence,
                        language=language or config.language,
                        raw=obj,
                    )

                    if is_final and obj.get("is_last_package") and send_done.is_set():
                        return
            finally:
                if not pump.done():
                    pump.cancel()
                    try:
                        await pump
                    except asyncio.CancelledError:
                        pass

    # ── Audio file (batch) ──────────────────────────────────────
    async def transcribe_file_url(
        self,
        audio_url: str,
        *,
        language: str | None = None,
        format: str = "mp3",
        channel: int = 1,
        enable_speaker_info: bool = False,
        enable_punc: bool = True,
        enable_itn: bool = True,
        show_utterances: bool = True,
        timeout_s: float = 300.0,
        poll_interval_s: float = 2.0,
    ) -> dict[str, Any]:
        """Submit an audio file URL for asynchronous batch transcription
        and poll until the result is ready.

        Audio source must be a public URL (or a presigned BytePlus TOS URL).
        Returns the full JSON result body — caller can pull text /
        utterances / words from `result.text`.
        """
        if not self.is_available():
            raise RuntimeError("BYTEPLUS_VOICE_API_KEY is not set")

        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": _RESOURCE_ID_FILE,
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
            "Content-Type": "application/json",
        }

        body: dict[str, Any] = {
            "user": {"uid": str(uuid.uuid4())},
            "audio": {"url": audio_url, "format": format, "channel": channel},
            "request": {
                "model_name": "bigmodel",
                "enable_punc": enable_punc,
                "enable_itn": enable_itn,
                "enable_speaker_info": enable_speaker_info,
                "show_utterances": show_utterances,
            },
        }
        if language:
            body["audio"]["language"] = language

        async with make_async_client() as c:
            submit = await c.post(_FILE_SUBMIT_URL, headers=headers, json=body)
            submit_status = submit.headers.get("X-Api-Status-Code") or ""
            if submit.status_code != 200 or submit_status != "20000000":
                raise RuntimeError(
                    f"submit failed: status={submit.status_code} "
                    f"x-api={submit_status} msg={submit.headers.get('X-Api-Message')}"
                )

            # Poll the query endpoint until the task reaches a terminal state.
            deadline = asyncio.get_event_loop().time() + timeout_s
            while True:
                if asyncio.get_event_loop().time() > deadline:
                    raise asyncio.TimeoutError("auc transcription timed out")
                await asyncio.sleep(poll_interval_s)
                q = await c.post(_FILE_QUERY_URL, headers=headers, json={})
                code = q.headers.get("X-Api-Status-Code") or ""
                if code == "20000000":  # done
                    return q.json()
                if code in ("20000001", "20000002"):  # processing / queued
                    continue
                # Anything else is a final error.
                raise RuntimeError(
                    f"query failed: status={q.status_code} "
                    f"x-api={code} msg={q.headers.get('X-Api-Message')}"
                )
