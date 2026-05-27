"""S2SBridge unit tests (Phase 3 PR-B, v0.2.24).

The bridge wraps an S2SSession (live WS to OpenAI Realtime / etc.)
behind the same ``push_audio`` / ``end_audio`` / ``interrupt`` /
``run()`` surface as the pipeline ``VoiceSession``. These tests
exercise the translation layer against a HAND-WRITTEN FAKE
S2SProvider — no network, no real OpenAI key required.

What's pinned:

  test_translate_user_partial_to_partial
      S2SEvent(kind="user_partial") → TurnEvent(kind="user_partial").

  test_translate_user_final_to_final
      Final transcript flows through verbatim.

  test_translate_assistant_text_to_token
      S2S "assistant_text" maps to pipeline "assistant_token" — the
      dashboard's accumulate-deltas-into-one-bubble code is the same
      as pipeline mode, so the rename is the load-bearing change.

  test_translate_assistant_audio_passthrough
      Raw PCM bytes pass through unchanged, kind becomes
      "assistant_audio" with sample_rate=24000.

  test_translate_speech_started_to_interrupt
      Server-VAD barge-in maps to the dashboard's interrupt
      affordance.

  test_translate_response_done_to_assistant_done
      Terminal event ends the turn.

  test_translate_error_to_error
      Hard errors propagate.

  test_tool_call_invokes_skill_and_submits_result
      The end-to-end tool path: S2S emits tool_call → bridge calls
      SkillRunner.invoke → emits skill_call + skill_result →
      bridge calls session.submit_tool_result with the output.

  test_upsample_16k_to_24k_length_ratio
      Resampler unit check — 16 kHz input gets 1.5× more output
      samples at 24 kHz. The actual signal fidelity isn't tested
      (linear interp is a known-lossy operation); just the
      sample-count contract that keeps Realtime happy.

  test_bridge_aexit_closes_session
      Lifecycle: the bridge's __aexit__ tears down the wrapped
      session even if it's been called multiple times (idempotent).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import field
from typing import Any

import pytest

from openvox.pipeline.s2s_bridge import S2SBridge, _upsample_pcm16_16k_to_24k
from openvox.providers.base import (
    AudioChunk,
    ProviderCapability,
    ProviderType,
    S2SConfig,
    S2SEvent,
    S2SProvider,
    S2SSession,
)


# ── Fake S2S provider used by every test ──────────────────────────


class _FakeSession(S2SSession):
    """Hand-controlled S2S session for tests.

    We expose a queue the test pushes onto, and the session emits
    those as events from ``events()``. Outbound calls
    (push_audio / commit_audio / submit_tool_result / interrupt)
    are recorded as tuples so tests can assert what the bridge
    pushed back.
    """

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[S2SEvent | None] = asyncio.Queue()
        self.pushed_audio: list[bytes] = []
        self.committed = 0
        self.interrupted = 0
        self.submitted: list[tuple[str, Any]] = []
        self.entered = False
        self.exited = 0

    async def __aenter__(self) -> "_FakeSession":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exited += 1

    async def push_audio(self, pcm_bytes: bytes) -> None:
        self.pushed_audio.append(pcm_bytes)

    async def commit_audio(self) -> None:
        self.committed += 1

    async def submit_tool_result(self, call_id: str, output: Any) -> None:
        self.submitted.append((call_id, output))

    async def interrupt(self) -> None:
        self.interrupted += 1

    async def events(self) -> AsyncIterator[S2SEvent]:
        while True:
            ev = await self.inbound.get()
            if ev is None:
                return
            yield ev


class _FakeProvider(S2SProvider):
    id = "fake_s2s"
    type = ProviderType.S2S
    display_name = "Fake S2S"
    capabilities = frozenset({ProviderCapability.STREAMING})

    def __init__(self) -> None:
        self.session = _FakeSession()

    def is_available(self) -> bool:
        return True

    def connect(self, config: S2SConfig) -> S2SSession:
        return self.session


# ── Translation tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_translate_user_partial_to_partial():
    p = _FakeProvider()
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        await p.session.inbound.put(S2SEvent(kind="user_partial", text="hel"))
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    assert len(out) == 1
    assert out[0].kind == "user_partial"
    assert out[0].text == "hel"


@pytest.mark.asyncio
async def test_translate_user_final_to_final():
    p = _FakeProvider()
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        await p.session.inbound.put(S2SEvent(kind="user_final", text="hello"))
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    assert out[0].kind == "user_final"
    assert out[0].text == "hello"


@pytest.mark.asyncio
async def test_translate_assistant_text_to_token():
    p = _FakeProvider()
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        for tok in ["Hi", " there", "!"]:
            await p.session.inbound.put(S2SEvent(kind="assistant_text", text=tok))
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    assert [ev.kind for ev in out] == ["assistant_token"] * 3
    assert "".join(ev.text for ev in out) == "Hi there!"


@pytest.mark.asyncio
async def test_translate_assistant_audio_passthrough():
    p = _FakeProvider()
    pcm = b"\x01\x02\x03\x04\x05\x06"
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        await p.session.inbound.put(S2SEvent(kind="assistant_audio", audio=pcm))
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    assert out[0].kind == "assistant_audio"
    assert out[0].audio == pcm
    assert out[0].sample_rate == 24000
    assert out[0].encoding == "pcm16"


@pytest.mark.asyncio
async def test_translate_speech_started_to_interrupt():
    p = _FakeProvider()
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        await p.session.inbound.put(S2SEvent(kind="speech_started"))
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    assert out[0].kind == "interrupt"


@pytest.mark.asyncio
async def test_translate_response_done_to_assistant_done():
    p = _FakeProvider()
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        await p.session.inbound.put(
            S2SEvent(kind="response_done", text="full assistant text")
        )
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    assert out[0].kind == "assistant_done"
    assert out[0].text == "full assistant text"


@pytest.mark.asyncio
async def test_translate_error_to_error_and_stops_iteration():
    """Hard errors must terminate the loop — letting iteration
    continue could mask the failure from the WS forwarder."""
    p = _FakeProvider()
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        await p.session.inbound.put(S2SEvent(kind="error", text="rate limit"))
        # Anything queued AFTER error must not be observed.
        await p.session.inbound.put(S2SEvent(kind="user_final", text="never seen"))
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    assert len(out) == 1
    assert out[0].kind == "error"
    assert "rate limit" in out[0].text


# ── Tool calls ────────────────────────────────────────────────────


class _StubRunner:
    """Minimal SkillRunner stand-in — exposes invoke() + tool_specs()
    so the bridge's tool-call handler works without spinning up the
    real runner (which would need a SkillContext + skill discovery)."""

    def __init__(self, return_output: Any = None, raises: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._return = return_output if return_output is not None else {"ok": 1}
        self._raises = raises

    def tool_specs(self) -> list[dict]:
        return []

    async def invoke(self, skill_id: str, args: dict) -> dict:
        self.calls.append((skill_id, args))
        if self._raises:
            raise RuntimeError("skill explosion")
        return {"output": self._return, "error": ""}


@pytest.mark.asyncio
async def test_tool_call_invokes_skill_and_submits_result():
    p = _FakeProvider()
    runner = _StubRunner(return_output={"city": "Tokyo", "temp_c": 22})
    async with S2SBridge(
        provider=p, config=S2SConfig(), skill_runner=runner  # type: ignore[arg-type]
    ) as bridge:
        await p.session.inbound.put(
            S2SEvent(
                kind="tool_call",
                data={
                    "name": "get_weather",
                    "arguments": {"city": "Tokyo"},
                    "call_id": "call_abc",
                },
            )
        )
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]

    # Two events surfaced — skill_call + skill_result.
    assert [ev.kind for ev in out] == ["skill_call", "skill_result"]
    assert out[0].text == "get_weather"
    assert out[0].data == {"args": {"city": "Tokyo"}, "call_id": "call_abc"}
    assert out[1].data == {
        "output": {"city": "Tokyo", "temp_c": 22},
        "error": "",
        "call_id": "call_abc",
    }
    # Runner was actually invoked with the parsed args.
    assert runner.calls == [("get_weather", {"city": "Tokyo"})]
    # Result was submitted back to the S2S session.
    assert p.session.submitted == [("call_abc", {"city": "Tokyo", "temp_c": 22})]


@pytest.mark.asyncio
async def test_tool_call_with_string_arguments_is_json_decoded():
    """OpenAI Realtime returns ``arguments`` as a JSON STRING in
    the ``function_call_arguments.done`` event. The adapter is
    supposed to parse it before yielding, but the bridge is
    defensive — passing a string still works."""
    p = _FakeProvider()
    runner = _StubRunner()
    async with S2SBridge(
        provider=p, config=S2SConfig(), skill_runner=runner  # type: ignore[arg-type]
    ) as bridge:
        await p.session.inbound.put(
            S2SEvent(
                kind="tool_call",
                data={
                    "name": "lookup",
                    "arguments": '{"q": "hello"}',  # ← string, not dict
                    "call_id": "call_str",
                },
            )
        )
        await p.session.inbound.put(None)
        _ = [ev async for ev in bridge.run()]
    assert runner.calls == [("lookup", {"q": "hello"})]


@pytest.mark.asyncio
async def test_tool_call_invoke_raise_is_translated_to_error_output():
    """A skill that raises should NOT crash the bridge — the bridge
    captures the exception, emits skill_result with the error, and
    still pushes that result back to the S2S session so the model
    can see it and respond with a graceful fallback."""
    p = _FakeProvider()
    runner = _StubRunner(raises=True)
    async with S2SBridge(
        provider=p, config=S2SConfig(), skill_runner=runner  # type: ignore[arg-type]
    ) as bridge:
        await p.session.inbound.put(
            S2SEvent(
                kind="tool_call",
                data={"name": "boom", "arguments": {}, "call_id": "call_boom"},
            )
        )
        await p.session.inbound.put(None)
        out = [ev async for ev in bridge.run()]
    # skill_result still emitted, with error populated.
    assert out[1].kind == "skill_result"
    assert out[1].data is not None
    assert "skill explosion" in out[1].data["error"]
    # Session got the error payload too.
    assert p.session.submitted[0][0] == "call_boom"
    assert "error" in p.session.submitted[0][1]


# ── Resampler ─────────────────────────────────────────────────────


def test_upsample_16k_to_24k_length_ratio():
    """320 samples at 16 kHz = 20 ms. At 24 kHz that's 480 samples.
    PCM16 = 2 bytes per sample, so 640 bytes in → 960 bytes out."""
    # 320 samples = 640 bytes
    src = b"\x00\x10" * 320
    out = _upsample_pcm16_16k_to_24k(src)
    assert len(out) == 960


def test_upsample_empty_is_empty():
    assert _upsample_pcm16_16k_to_24k(b"") == b""


# ── Lifecycle ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_aexit_closes_session():
    """Double-exit must be idempotent — voice_ws.py teardown can
    call __aexit__ even when the session has already been torn
    down by an earlier path (error in run(), for instance)."""
    p = _FakeProvider()
    bridge = S2SBridge(provider=p, config=S2SConfig())
    await bridge.__aenter__()
    assert p.session.entered is True
    await bridge.__aexit__(None, None, None)
    await bridge.__aexit__(None, None, None)
    # Second exit must not throw + session.__aexit__ either ran
    # twice or was guarded against the double-call. _FakeSession
    # records exited count; bridge guards against multiple
    # teardowns by nulling _session after the first.
    assert p.session.exited >= 1


@pytest.mark.asyncio
async def test_push_audio_resamples_16k_chunks():
    """A 16 kHz AudioChunk gets resampled to 24 kHz before reaching
    the underlying S2S session."""
    p = _FakeProvider()
    async with S2SBridge(provider=p, config=S2SConfig()) as bridge:
        chunk = AudioChunk(
            data=b"\x00\x10" * 320,    # 320 samples = 640 bytes @ 16 kHz
            sample_rate=16000,
            encoding="pcm16",
        )
        await bridge.push_audio(chunk)
    # The fake session received the 24 kHz bytes (960 = 1.5× 640).
    assert len(p.session.pushed_audio) == 1
    assert len(p.session.pushed_audio[0]) == 960
