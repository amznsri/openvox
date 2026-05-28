"""Integration test for /api/v1/playground/text — the skill-loop fix.

Bug context:
    Before this fix, /playground/text built ``LLMConfig`` without
    ``tools=``. When the request targeted an agent whose prompt
    instructs the LLM to use specific skills (every productivity
    template post-Phase 1.6), BytePlus Seed-2-Pro would either:
      a. Emit its native function-call markup as plain content text
         (``<|FunctionCallBegin|>...<|FunctionCallEnd|>``), OR
      b. Fall back to a "I don't have access" reply.
    Both leak through to the user — neither invokes the actual skill.

    The fix mirrors what /turn does: load ``a.skills`` + ``a.mcp_servers``
    when ``agent_id`` is supplied, build a SkillRunner, run a bounded
    tool-call loop. This test pins down the wiring so the bug class
    can't regress.

Mirrors the structure of ``test_mcp_text_transports.py`` (PR #31).
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest


# ── Reusable recorder LLM ────────────────────────────────────────


def _make_recorder_llm():
    """Build a per-test LLMProvider that captures the kwargs it was
    handed AND can be scripted to emit tool_calls on the first round
    then a clean text reply on the second."""
    from openvox.providers import ProviderType
    from openvox.providers.base import LLMProvider, LLMResponseChunk

    class _RecorderLLM(LLMProvider):
        id = "recorder"
        type = ProviderType.LLM
        display_name = "Recorder (test)"

        def __init__(self):
            self.captured_tools: list = []
            self.captured_messages: list[list] = []  # one entry per round
            self.round = 0
            # Scripted responses — index by round number.
            # Round 0: emit a streaming tool_call (in BytePlus's
            # fragmented format) so the test exercises
            # _merge_tool_call_deltas.
            # Round 1: plain text reply once the tool result is in.
            self.scripted_rounds: list[list[LLMResponseChunk]] = []

        def is_available(self) -> bool:
            return True

        async def chat_stream(self, messages, cfg) -> AsyncIterator[Any]:
            self.captured_messages.append(list(messages))
            if self.round == 0:
                self.captured_tools = list(cfg.tools or [])
            chunks = self.scripted_rounds[self.round]
            self.round += 1
            for c in chunks:
                yield c

    return _RecorderLLM()


@pytest.fixture
def _register_recorder_llm():
    """Drop a recorder LLM into the provider registry under id 'recorder'."""
    from openvox.providers import ProviderType, get_registry

    recorder = _make_recorder_llm()
    reg = get_registry()
    key = (ProviderType.LLM, "recorder")
    with reg._lock:
        reg._instances[key] = recorder
    yield recorder
    with reg._lock:
        reg._instances.pop(key, None)


@pytest.fixture
async def _agent_with_skills(isolated_db):
    """Seed one agent with the email-assistant native-skills shape."""
    from openvox.db import db_session
    from openvox.db.models import Agent

    agent_id = "test-agent-playground-text"
    async with db_session() as s:
        s.add(
            Agent(
                id=agent_id,
                name="Email Assistant (test)",
                description="",
                system_prompt=(
                    "You are an email assistant. Use list_emails to fetch unread mail."
                ),
                greeting="",
                stt_provider="recorder",
                tts_provider="recorder",
                llm_provider="recorder",
                llm_model="",
                voice_id="",
                voice_speed=1.0,
                voice_language="en-US",
                temperature=0.3,
                max_tokens=400,
                skills=["get_time"],  # any built-in skill that the registry knows
                channels={},
                mcp_servers=[],
                voice_map={},
                status="published",
            )
        )
    return agent_id


# ── Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_playground_text_passes_tools_when_agent_id_supplied(
    _agent_with_skills, _register_recorder_llm
):
    """The whole point of the fix: tools= must reach the LLM."""
    from openvox.api.routes.playground import TextRequest, text_chat
    from openvox.providers.base import LLMResponseChunk

    # No tool calls — recorder just emits a single text-only chunk.
    _register_recorder_llm.scripted_rounds = [
        [LLMResponseChunk(delta="ok", finish_reason="stop")],
    ]

    req = TextRequest(
        agent_id=_agent_with_skills,
        provider="recorder",
        user="hi",
        system="(ignored — agent's own prompt should win)",
    )
    resp = await text_chat(req)

    # Drain the streaming body so the inner gen() runs to completion.
    text = ""
    async for chunk in resp.body_iterator:
        text += chunk if isinstance(chunk, str) else chunk.decode("utf-8")
    assert text == "ok"

    # The recorder captured the LLMConfig.tools list — assert get_time
    # was sent through. Without the fix this list would be empty.
    tool_names = [t.get("function", {}).get("name") for t in _register_recorder_llm.captured_tools]
    assert "get_time" in tool_names, (
        f"agent's skills not sent to LLM via tools=; captured: {tool_names}"
    )

    # The agent's own system prompt should override the caller's
    # generic `req.system` when agent_id is supplied — that's how the
    # LLM learns it has tools to call.
    first_round_messages = _register_recorder_llm.captured_messages[0]
    sys_msg = next(m for m in first_round_messages if m.role == "system")
    assert "email assistant" in sys_msg.content.lower()
    assert "list_emails" in sys_msg.content


@pytest.mark.asyncio
async def test_playground_text_merges_streaming_tool_call_fragments_and_invokes_skill(
    _agent_with_skills, _register_recorder_llm
):
    """The actual bug repro: BytePlus streams tool_calls in fragments.

    Without ``_merge_tool_call_deltas`` (CLAUDE.md §8 #17), each chunk
    overwrites the previous and the final tool call has no function
    name → never invokes the skill → LLM falls through to text and
    the user sees confusion.

    Simulate BytePlus's fragmental shape: first chunk carries the
    function name + empty args, subsequent chunks each carry a piece
    of the arguments JSON string. After all fragments arrive, the
    finalised tool call should have the assembled name + arguments.
    Then after the skill runs, round 2's LLM call gets the tool
    result in `messages` and emits a clean text reply.
    """
    from openvox.api.routes.playground import TextRequest, text_chat
    from openvox.providers.base import LLMResponseChunk

    # Round 0: fragmental tool_call streaming, BytePlus shape.
    # Note: each delta only contains a SUBSET of the function object.
    # The merge helper accumulates them by index.
    round_0_chunks = [
        LLMResponseChunk(
            delta="",
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "get_time", "arguments": ""},
                }
            ],
        ),
        LLMResponseChunk(
            delta="",
            tool_calls=[{"index": 0, "function": {"arguments": "{}"}}],
        ),
        LLMResponseChunk(delta="", finish_reason="tool_calls"),
    ]
    # Round 1: after the tool runs, the LLM produces a real reply.
    round_1_chunks = [
        LLMResponseChunk(delta="The current UTC time is "),
        LLMResponseChunk(delta="2026-05-27.", finish_reason="stop"),
    ]
    _register_recorder_llm.scripted_rounds = [round_0_chunks, round_1_chunks]

    req = TextRequest(
        agent_id=_agent_with_skills,
        provider="recorder",
        user="what time is it?",
    )
    resp = await text_chat(req)
    text = ""
    async for chunk in resp.body_iterator:
        text += chunk if isinstance(chunk, str) else chunk.decode("utf-8")

    # Two LLM rounds happened (round 0 produced a tool_call, round 1
    # produced the actual user-facing text).
    assert _register_recorder_llm.round == 2, (
        f"expected 2 LLM rounds; got {_register_recorder_llm.round}"
    )

    # Round 1's messages should include the assistant's tool_calls
    # message AND the tool result — proves we built the history
    # correctly (Ark / OpenAI contract — CLAUDE.md §8 #18).
    round_1_messages = _register_recorder_llm.captured_messages[1]
    roles = [m.role for m in round_1_messages]
    assert "assistant" in roles
    assert "tool" in roles
    tool_msg = next(m for m in round_1_messages if m.role == "tool")
    assert tool_msg.tool_call_id == "call_abc", (
        f"tool message tool_call_id wrong: {tool_msg.tool_call_id!r}"
    )
    assert "get_time" in (tool_msg.name or "")

    # Stream surfaces:
    #   - the inline status marker we emit between rounds
    #   - the LLM's eventual text reply
    assert "calling get_time" in text
    assert "current UTC time" in text


@pytest.mark.asyncio
async def test_playground_text_no_agent_id_uses_caller_system_prompt(
    isolated_db, _register_recorder_llm
):
    """Backwards compat: when no agent_id is supplied, the route stays
    in its original "raw LLM" mode — no skills loaded, ``req.system``
    is the system prompt used."""
    from openvox.api.routes.playground import TextRequest, text_chat
    from openvox.providers.base import LLMResponseChunk

    _register_recorder_llm.scripted_rounds = [
        [LLMResponseChunk(delta="hi", finish_reason="stop")],
    ]

    req = TextRequest(
        provider="recorder",
        user="hello",
        system="You are a generic assistant.",
        # no agent_id
    )
    resp = await text_chat(req)
    async for _ in resp.body_iterator:
        pass

    # No skills should have been loaded — captured_tools is empty
    # because the runner had skill_ids=[].
    assert _register_recorder_llm.captured_tools == []

    # System prompt is what the caller supplied.
    first_msgs = _register_recorder_llm.captured_messages[0]
    sys_msg = next(m for m in first_msgs if m.role == "system")
    assert sys_msg.content == "You are a generic assistant."
