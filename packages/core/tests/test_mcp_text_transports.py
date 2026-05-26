"""Integration tests for the MCP bootstrap fix on text-mode transports.

Bug context (CLAUDE.md candidate entry):
    v0.1.8 only wired ``MCPSessionManager`` into the voice WS. The
    ``/api/v1/agents/{id}/turn`` endpoint and the Telegram inbound
    handler both built a ``SkillRunner`` from ``a.skills`` alone —
    so when an agent's ``mcp_servers`` declared a Gmail / Calendar /
    Notion / etc. MCP server, the LLM saw zero bridged tools and
    fell back to the system prompt's "configure your Google OAuth
    on the MCP tab" guidance.

These tests prove the fix by:

  1. Creating an Agent with a non-empty ``mcp_servers`` list.
  2. Patching ``MCPSessionManager`` so the bridge returns one stub
     skill named ``mcp__gmail__list_emails``.
  3. Patching the LLM provider with a recorder that captures the
     ``tools=`` list it was handed.
  4. Calling ``agent_text_turn(…)``.
  5. Asserting the recorder saw the MCP-bridged tool in its
     ``tools=`` list.

If the fix regresses, the assertion in step 5 fails — exactly the
shape of the user-visible bug.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest


# ── Test fakes ────────────────────────────────────────────────────


def _make_recorder_llm():
    """Build a fresh LLMProvider stand-in per test.

    Stores its captures on the instance — the fixture stashes the
    instance directly in ``ProviderRegistry._instances`` so tests can
    read ``captured_tools`` after the route runs.
    """
    from openvox.providers import ProviderType
    from openvox.providers.base import LLMProvider

    class _RecorderLLM(LLMProvider):
        id = "recorder"
        type = ProviderType.LLM
        display_name = "Recorder (test)"

        def __init__(self):
            self.captured_tools: list = []
            self.captured_messages: list = []
            self.reply_text = "ack"

        def is_available(self) -> bool:
            return True

        async def chat_stream(self, messages, cfg) -> AsyncIterator[Any]:
            from openvox.providers.base import LLMResponseChunk

            self.captured_messages = list(messages)
            self.captured_tools = list(cfg.tools or [])
            yield LLMResponseChunk(
                delta=self.reply_text,
                tool_calls=None,
                finish_reason="stop",
            )

    return _RecorderLLM()


@pytest.fixture
async def _seeded_agent(isolated_db):
    """Insert one Agent into the DB with ``mcp_servers`` populated.

    Returns the agent_id. Uses provider="recorder" so the registered
    mock picks it up (see ``_register_recorder_llm``).
    """
    from openvox.db import db_session
    from openvox.db.models import Agent

    agent_id = "test-agent-mcp-wired"
    async with db_session() as s:
        s.add(
            Agent(
                id=agent_id,
                name="Email Assistant (test)",
                description="",
                system_prompt="You are helpful.",
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
                skills=["get_time"],  # one built-in
                channels={},
                mcp_servers=[
                    {"name": "gmail", "transport": "stdio", "command": "echo"}
                ],
                voice_map={},
                status="published",
            )
        )
    return agent_id


@pytest.fixture
def _register_recorder_llm():
    """Inject a recorder LLM into the provider registry under id ``recorder``.

    We bypass ``ProviderRegistry.register`` (which expects a class) and
    write directly into ``_instances`` because the registry lazily
    instantiates classes — we want the SAME instance the test reads
    afterwards, not a fresh ``cls()`` the registry would build.

    Cleans the registry entry on teardown so subsequent tests don't
    inherit a stale recorder.
    """
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
def _stub_mcp_manager(monkeypatch):
    """Replace ``MCPSessionManager`` with a stub that exposes one bridged tool."""
    from openvox.skills.base import BaseSkill

    class _StubBridgedSkill(BaseSkill):
        id = "mcp__gmail__list_emails"
        display_name = "List Gmail messages (bridged)"
        description = "Stub bridged tool — present only if MCP bootstrap fired."

        async def run(self, args, ctx):
            return {"ok": True, "stub": True}

    class _StubMgr:
        def __init__(self, configs):
            self.configs = configs
            self.skills: list[BaseSkill] = []

        async def __aenter__(self):
            self.skills = [_StubBridgedSkill()]
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self.skills.clear()

    monkeypatch.setattr("openvox.mcp.MCPSessionManager", _StubMgr)


# ── /api/v1/agents/{id}/turn ──────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_endpoint_bootstraps_mcp_tools(
    _seeded_agent, _register_recorder_llm, _stub_mcp_manager
):
    """The LLM's ``tools=`` list must include the MCP-bridged tool.

    Before the fix this test fails: ``recorder.captured_tools`` would
    contain only ``get_time`` (the built-in), not
    ``mcp__gmail__list_emails``.
    """
    from openvox.api.routes.agents import agent_text_turn

    class _Body:
        user_text = "What's in my inbox?"
        history: list[dict] = []

    result = await agent_text_turn(_seeded_agent, _Body())
    assert result["text"] == "ack"

    tool_names = [
        t.get("function", {}).get("name")
        for t in _register_recorder_llm.captured_tools
    ]
    assert "mcp__gmail__list_emails" in tool_names, (
        "MCP-bridged tool was missing from the LLM's tools= list — "
        "the open_agent_mcp wiring regressed."
    )
    # Built-in still present too.
    assert "get_time" in tool_names


@pytest.mark.asyncio
async def test_turn_endpoint_works_with_no_mcp_servers(
    isolated_db, _register_recorder_llm
):
    """When mcp_servers is empty, no manager is constructed.

    This is the fast path in ``open_agent_mcp`` — important because
    most agents have no MCP servers and we don't want to pay the
    subprocess-spawn cost for them.
    """
    from openvox.db import db_session
    from openvox.db.models import Agent

    agent_id = "test-agent-no-mcp"
    async with db_session() as s:
        s.add(
            Agent(
                id=agent_id,
                name="Plain agent",
                description="",
                system_prompt="You are helpful.",
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
                skills=["get_time"],
                channels={},
                mcp_servers=[],  # the no-op fast path
                voice_map={},
                status="published",
            )
        )

    from openvox.api.routes.agents import agent_text_turn

    class _Body:
        user_text = "hi"
        history: list[dict] = []

    result = await agent_text_turn(agent_id, _Body())
    assert result["text"] == "ack"

    tool_names = [
        t.get("function", {}).get("name")
        for t in _register_recorder_llm.captured_tools
    ]
    # Only the built-in.
    assert "get_time" in tool_names
    assert not any(n and n.startswith("mcp__") for n in tool_names)
