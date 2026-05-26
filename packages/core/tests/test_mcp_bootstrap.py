"""Tests for ``openvox.mcp.open_agent_mcp`` — the shared MCP bootstrap
helper used by every text-mode transport (Telegram, ``/turn``, future
WhatsApp / WeChat).

The bug this helper exists to fix: in v0.1.8 only the voice WS
bootstrapped MCPSessionManager per-session. Text-mode transports
constructed a ``SkillRunner`` from ``a.skills`` alone, so the LLM saw
no MCP-bridged tools and (for the Gmail / Calendar productivity
templates) dutifully recited the system prompt's "configure your
Google OAuth on the MCP tab" fallback.

These tests assert the helper:

  - Yields an empty list when given no MCP configs (no-op fast path).
  - Spawns the session manager and yields its skills when configs ARE
    present, then tears the manager down on exit.
  - Yields empty (with a log) when the manager's setup raises — a
    transient MCP failure shouldn't kill a turn the LLM could still
    complete with built-in tools.
  - Doesn't try to call ``__aexit__`` when ``__aenter__`` failed —
    that would be a double fault.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_no_configs_yields_empty_list_and_does_not_spawn():
    """Fast path: ``mcp_servers=None`` or ``[]`` → no manager constructed."""
    from openvox.mcp import open_agent_mcp

    async with open_agent_mcp(None) as extras:
        assert extras == []

    async with open_agent_mcp([]) as extras:
        assert extras == []


@pytest.mark.asyncio
async def test_with_configs_yields_skills_and_tears_down(monkeypatch):
    """Happy path: configs → manager spun up, skills exposed, then torn down."""
    from openvox.mcp import bridge, open_agent_mcp

    # Use a sentinel BaseSkill instance for the manager to expose.
    from openvox.skills.base import BaseSkill, SkillContext

    class _Stub(BaseSkill):
        id = "mcp__stub__tool"
        display_name = "Stub MCP tool"
        description = "stub"

        async def run(self, args, ctx):
            return {"ok": True}

    fake_mgr_state = {"entered": 0, "exited": 0}

    class _FakeMgr:
        def __init__(self, configs):
            self.configs = configs
            self.skills: list[BaseSkill] = []

        async def __aenter__(self):
            fake_mgr_state["entered"] += 1
            self.skills = [_Stub()]
            return self

        async def __aexit__(self, exc_type, exc, tb):
            fake_mgr_state["exited"] += 1

    monkeypatch.setattr("openvox.mcp.MCPSessionManager", _FakeMgr)

    cfg = [{"name": "stub", "transport": "stdio", "command": "echo"}]
    async with open_agent_mcp(cfg) as extras:
        assert len(extras) == 1
        assert extras[0].id == "mcp__stub__tool"
        # Mid-context: manager has been entered exactly once.
        assert fake_mgr_state["entered"] == 1
        assert fake_mgr_state["exited"] == 0

    # Post-context: teardown ran exactly once.
    assert fake_mgr_state["exited"] == 1


@pytest.mark.asyncio
async def test_setup_failure_yields_empty_and_does_not_call_exit(monkeypatch):
    """If ``__aenter__`` raises, we yield empty AND skip ``__aexit__``.

    The asymmetric pattern matters: calling ``__aexit__`` on a manager
    whose ``__aenter__`` never completed would touch attributes the
    constructor never initialised (e.g. an mcp.ClientSession that's
    None), so the teardown itself would raise — masking the original
    failure and crashing the turn.
    """
    from openvox.mcp import open_agent_mcp

    fake_state = {"entered": 0, "exited": 0}

    class _ExplodyMgr:
        def __init__(self, configs):
            self.configs = configs
            self.skills = []

        async def __aenter__(self):
            fake_state["entered"] += 1
            raise RuntimeError("subprocess binary not found")

        async def __aexit__(self, exc_type, exc, tb):
            fake_state["exited"] += 1

    monkeypatch.setattr("openvox.mcp.MCPSessionManager", _ExplodyMgr)

    cfg = [{"name": "broken", "transport": "stdio", "command": "nonexistent-binary"}]
    async with open_agent_mcp(cfg) as extras:
        # Turn still proceeds with NO MCP tools.
        assert extras == []
        assert fake_state["entered"] == 1
    # __aexit__ must NOT have been called — the manager was never
    # successfully entered.
    assert fake_state["exited"] == 0


@pytest.mark.asyncio
async def test_teardown_runs_even_when_inner_block_raises(monkeypatch):
    """A turn that raises inside the ``async with`` must still tear MCP down.

    Otherwise an exception in the LLM call would leak MCP subprocesses
    forever.
    """
    from openvox.mcp import open_agent_mcp
    from openvox.skills.base import BaseSkill

    teardown_calls = {"n": 0}

    class _OkMgr:
        def __init__(self, configs):
            self.skills = []

        async def __aenter__(self):
            self.skills = []
            return self

        async def __aexit__(self, exc_type, exc, tb):
            teardown_calls["n"] += 1

    monkeypatch.setattr("openvox.mcp.MCPSessionManager", _OkMgr)

    with pytest.raises(RuntimeError, match="LLM blew up"):
        async with open_agent_mcp([{"name": "x", "transport": "stdio"}]):
            raise RuntimeError("LLM blew up")
    assert teardown_calls["n"] == 1
