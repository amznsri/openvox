"""Unit tests for the OpenAI Realtime S2S adapter.

These exercise the protocol-level translation between Realtime's 30+
event types and our canonical ``S2SEvent`` shape. They don't actually
open a WebSocket — the adapter's `_normalise()` method is pure and
trivially testable, and the lifecycle methods that DO talk to a
socket are exercised via a mock session in `test_s2s_lifecycle`.
"""

from __future__ import annotations

import base64
import json

import pytest


# ── Registration + provider surface ────────────────────────────


def test_openai_realtime_is_registered():
    """Phase 3 PR-A — make sure bootstrap exposes the provider."""
    from openvox.providers.bootstrap import register_builtins
    from openvox.providers.registry import get_registry
    from openvox.providers.base import ProviderType

    register_builtins()
    reg = get_registry()
    p = reg.get(ProviderType.S2S, "openai_realtime")
    assert p is not None
    assert p.id == "openai_realtime"
    assert p.type == ProviderType.S2S


def test_openai_realtime_unavailable_without_api_key(tmp_openvox_home):
    """No OPENAI_API_KEY → registry says unavailable, no crash."""
    from openvox.providers.s2s import OpenAIRealtimeProvider

    p = OpenAIRealtimeProvider()
    assert p.is_available() is False

    # connect() raises rather than silently producing a session with
    # no auth — the route layer catches and surfaces the message.
    from openvox.providers.base import S2SConfig

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        p.connect(S2SConfig())


def test_openai_realtime_available_when_api_key_set(
    tmp_openvox_home, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-12345")
    from openvox.providers.s2s import OpenAIRealtimeProvider

    p = OpenAIRealtimeProvider()
    assert p.is_available() is True


# ── Event normalisation ────────────────────────────────────────


@pytest.fixture
def _session():
    """A bare session instance — fine for unit-testing _normalise.

    We don't enter the async context (no WS connect) — just construct
    the object so we can call its pure translation method. The
    constructor takes (api_key, model, config); the config carries the
    output sample-rate that audio events get tagged with.
    """
    from openvox.providers.base import S2SConfig
    from openvox.providers.s2s.openai_realtime import _OpenAIRealtimeSession

    return _OpenAIRealtimeSession(
        api_key="sk-test",
        model="gpt-4o-realtime-preview",
        config=S2SConfig(sample_rate_out=24000),
    )


def test_normalise_audio_delta(_session):
    """response.audio.delta → assistant_audio (base64-decoded)."""
    raw_pcm = b"\x00\x01\x02\x03" * 100
    msg = {
        "type": "response.audio.delta",
        "delta": base64.b64encode(raw_pcm).decode(),
    }
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "assistant_audio"
    assert ev.audio == raw_pcm
    assert ev.sample_rate == 24000


def test_normalise_audio_transcript_delta(_session):
    msg = {"type": "response.audio_transcript.delta", "delta": "Hello"}
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "assistant_text"
    assert ev.text == "Hello"


def test_normalise_user_transcription_partial(_session):
    msg = {
        "type": "conversation.item.input_audio_transcription.delta",
        "delta": "what's",
    }
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "user_partial"
    assert ev.text == "what's"


def test_normalise_user_transcription_completed(_session):
    msg = {
        "type": "conversation.item.input_audio_transcription.completed",
        "transcript": "what's the weather",
    }
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "user_final"
    assert ev.text == "what's the weather"


def test_normalise_function_call(_session):
    """Tool call payload is JSON-decoded and split into name/args/id.

    Realtime sends `arguments` as a STRING (the streaming-accumulated
    JSON). We parse it here so the orchestrator gets a real dict.
    """
    msg = {
        "type": "response.function_call_arguments.done",
        "name": "get_time",
        "arguments": '{"timezone":"UTC"}',
        "call_id": "call_abc123",
    }
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "tool_call"
    assert ev.data == {
        "name": "get_time",
        "args": {"timezone": "UTC"},
        "call_id": "call_abc123",
    }


def test_normalise_function_call_with_malformed_args(_session):
    """Garbage `arguments` doesn't crash — wrap in {_raw: …}."""
    msg = {
        "type": "response.function_call_arguments.done",
        "name": "x",
        "arguments": "{not json",
        "call_id": "c1",
    }
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "tool_call"
    assert ev.data["args"] == {"_raw": "{not json"}


def test_normalise_speech_started(_session):
    msg = {"type": "input_audio_buffer.speech_started"}
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "speech_started"


def test_normalise_response_done(_session):
    msg = {"type": "response.done"}
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "response_done"


def test_normalise_error(_session):
    msg = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "missing_required_parameter",
            "message": "voice is required",
        },
    }
    ev = _session._normalise(msg)
    assert ev is not None
    assert ev.kind == "error"
    assert ev.text == "voice is required"
    assert ev.data == {
        "code": "missing_required_parameter",
        "type": "invalid_request_error",
    }


def test_normalise_ignores_bookkeeping_events(_session):
    """The 20+ bookkeeping events Realtime emits should return None."""
    for bookkeeping in (
        "session.created",
        "session.updated",
        "response.created",
        "rate_limits.updated",
        "input_audio_buffer.committed",
        "response.audio.done",
        "response.content_part.added",
    ):
        ev = _session._normalise({"type": bookkeeping})
        assert ev is None, f"{bookkeeping} should be ignored"


# ── Tool spec normalisation ────────────────────────────────────


def test_normalise_tools_flattens_chat_completions_shape():
    """Realtime wants flat shape — no `{"type":"function","function":…}`."""
    from openvox.providers.s2s.openai_realtime import _OpenAIRealtimeSession

    chat_completions_shape = [
        {
            "type": "function",
            "function": {
                "name": "list_emails",
                "description": "list inbox",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    out = _OpenAIRealtimeSession._normalise_tools(chat_completions_shape)
    assert out == [
        {
            "type": "function",
            "name": "list_emails",
            "description": "list inbox",
            "parameters": {"type": "object", "properties": {}},
        }
    ]


def test_normalise_tools_passes_through_already_flat_specs():
    """An already-flat tool spec round-trips unchanged."""
    from openvox.providers.s2s.openai_realtime import _OpenAIRealtimeSession

    flat = [
        {
            "type": "function",
            "name": "ping",
            "description": "",
            "parameters": {"type": "object"},
        }
    ]
    assert _OpenAIRealtimeSession._normalise_tools(flat) == flat


# ── Outbound message construction (with a mock WS) ─────────────


class _StubWS:
    """Drop-in replacement for the websockets.connect() return value.

    Captures the JSON payloads `_send` writes so we can assert the
    adapter sent the right Realtime events on push_audio / interrupt
    / submit_tool_result calls.
    """

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_push_audio_base64_encodes_pcm(_session):
    """input_audio_buffer.append carries the audio bytes b64-encoded."""
    _session._ws = _StubWS()
    pcm = b"\x12\x34\x56\x78" * 10
    await _session.push_audio(pcm)
    assert len(_session._ws.sent) == 1
    msg = _session._ws.sent[0]
    assert msg["type"] == "input_audio_buffer.append"
    assert base64.b64decode(msg["audio"]) == pcm


@pytest.mark.asyncio
async def test_push_audio_skips_empty_chunks(_session):
    _session._ws = _StubWS()
    await _session.push_audio(b"")
    assert _session._ws.sent == []


@pytest.mark.asyncio
async def test_interrupt_sends_response_cancel(_session):
    _session._ws = _StubWS()
    await _session.interrupt()
    assert _session._ws.sent == [{"type": "response.cancel"}]


@pytest.mark.asyncio
async def test_submit_tool_result_then_resume(_session):
    """Tool result → conversation.item.create → response.create.

    Realtime won't resume on its own after a tool result; we need
    the explicit response.create. The order matters: the item must
    be in the conversation BEFORE we ask for a response.
    """
    _session._ws = _StubWS()
    await _session.submit_tool_result(
        "call_xyz", {"ok": True, "output": "current time"}
    )
    assert [m["type"] for m in _session._ws.sent] == [
        "conversation.item.create",
        "response.create",
    ]
    item = _session._ws.sent[0]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_xyz"
    assert json.loads(item["output"]) == {"ok": True, "output": "current time"}


@pytest.mark.asyncio
async def test_submit_tool_result_passes_strings_unchanged(_session):
    """A pre-serialised string output isn't double-encoded."""
    _session._ws = _StubWS()
    await _session.submit_tool_result("c1", "already a string")
    assert _session._ws.sent[0]["item"]["output"] == "already a string"


@pytest.mark.asyncio
async def test_send_before_aenter_raises(_session):
    """Sending before the WS is open is a programming error."""
    with pytest.raises(RuntimeError, match="not open"):
        await _session.push_audio(b"\x00\x00")
