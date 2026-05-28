"""Telegram bot-token uniqueness guard (F2 of the v0.2.9 side-quest).

A Telegram bot can only have ONE consumer at the protocol level:
  * `setWebhook` REPLACES the previous URL/secret — silent overwrite.
  * Concurrent `getUpdates` pollers race for each update.

Letting the dashboard wire the same `bot_token` to two agents was a
silent footgun: the second connect would quietly win while the
first agent lost its inbound traffic with no error surfaced.

These tests pin down the new behaviour:
  - Same token to a NEW agent → 409 with a readable message that
    names the conflicting agent.
  - Same token to the SAME agent → success (this is the natural
    "edit channel config" flow — change reply_mode, mode, etc.).
  - Token is checked BEFORE Telegram's getMe so a stolen-looking
    token never even hits Telegram if it's already in use locally.
"""

from __future__ import annotations

import httpx
import pytest


_TG_GET_ME = "https://api.telegram.org/bot12345:ABC-fake-token/getMe"
_FAKE_TOKEN = "12345:ABC-fake-token"


async def _create_agent(name: str, agent_id: str) -> None:
    """Seed one agent into the DB. Bypasses the route to keep the
    test focused on the connect endpoint."""
    from openvox.db import db_session
    from openvox.db.models import Agent

    async with db_session() as s:
        s.add(
            Agent(
                id=agent_id,
                name=name,
                description="",
                system_prompt="hi",
                greeting="",
                stt_provider="byteplus",
                tts_provider="byteplus",
                llm_provider="byteplus",
                llm_model="",
                voice_id="",
                voice_speed=1.0,
                voice_language="en-US",
                temperature=0.3,
                max_tokens=400,
                skills=[],
                channels={},
                mcp_servers=[],
                voice_map={},
                status="draft",
            )
        )


def _connect_request_body(agent_id: str, token: str = _FAKE_TOKEN) -> dict:
    return {
        "agent_id": agent_id,
        "bot_token": token,
        "mode": "polling",
        "reply_mode": "text",
    }


# ── Helper: stub out Telegram's getMe + the polling startup ────────


@pytest.fixture
def _stub_telegram_io(monkeypatch):
    """Block any real call to api.telegram.org.

    The connect endpoint calls `verify_bot()` (getMe) and, on success,
    `tg.delete_webhook()` + `tg_poll.start_polling()`. We replace
    all three so the test never hits the network.

    NOTE: respx alone isn't enough because `start_polling` spawns a
    background asyncio task that owns its own httpx client. Stubbing
    the module-level functions side-steps that.
    """
    import openvox.telephony.telegram as tg
    import openvox.telephony.telegram_polling as tg_poll

    async def _ok_get_me(token: str):
        return {"id": 999, "is_bot": True, "username": "stub_bot"}

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(tg, "verify_bot", _ok_get_me)
    monkeypatch.setattr(tg, "delete_webhook", _noop)
    monkeypatch.setattr(tg_poll, "start_polling", _noop)
    monkeypatch.setattr(tg_poll, "stop_polling", _noop)


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_token_two_agents_returns_409(isolated_db, _stub_telegram_io):
    """The whole point of the guard."""
    from fastapi import HTTPException

    from openvox.api.routes.telephony import (
        telegram_connect,
        TelegramConnectRequest,
    )

    await _create_agent("Personal Assistant", "agent-personal")
    await _create_agent("Work Assistant", "agent-work")

    # First connect works.
    await telegram_connect(
        TelegramConnectRequest(**_connect_request_body("agent-personal")),
        request=None,  # not used by the endpoint's logic
    )

    # Second connect (same token, different agent) → 409.
    with pytest.raises(HTTPException) as ei:
        await telegram_connect(
            TelegramConnectRequest(**_connect_request_body("agent-work")),
            request=None,
        )

    assert ei.value.status_code == 409
    # Error message names the conflicting agent so the user knows
    # where to go disconnect it from.
    assert "Personal Assistant" in ei.value.detail
    # And hints at the resolution.
    assert "Disconnect" in ei.value.detail or "BotFather" in ei.value.detail


@pytest.mark.asyncio
async def test_same_token_same_agent_is_allowed(isolated_db, _stub_telegram_io):
    """Re-connecting to the SAME agent is the natural edit flow.

    A user toggling `reply_mode` from text → voice, or switching
    polling → webhook, hits this path. Refusing it would be a
    regression.
    """
    from openvox.api.routes.telephony import (
        telegram_connect,
        TelegramConnectRequest,
    )

    await _create_agent("Solo Assistant", "agent-solo")

    body = _connect_request_body("agent-solo")
    # First connect.
    r1 = await telegram_connect(TelegramConnectRequest(**body), request=None)
    # Reconnect with mode flip — should succeed.
    body["reply_mode"] = "both"
    r2 = await telegram_connect(TelegramConnectRequest(**body), request=None)

    # Both calls returned a non-error body (the endpoint returns a
    # dict; no exception was raised).
    assert isinstance(r1, dict)
    assert isinstance(r2, dict)


@pytest.mark.asyncio
async def test_uniqueness_check_runs_before_telegram_verify(
    isolated_db, monkeypatch
):
    """The guard must fire BEFORE we call Telegram's getMe.

    Two reasons:
      1. Avoid pointless network round-trip on what we already know
         is a conflict.
      2. If the token is currently bound to another agent, calling
         getMe + setWebhook (in webhook mode) would tamper with
         that other agent's setup. The check has to be the FIRST
         thing the endpoint does.
    """
    import openvox.telephony.telegram as tg
    import openvox.telephony.telegram_polling as tg_poll
    from fastapi import HTTPException

    from openvox.api.routes.telephony import (
        telegram_connect,
        TelegramConnectRequest,
    )

    # Track whether verify_bot got called.
    calls = {"verify_bot": 0}

    async def _track_verify_bot(token: str):
        calls["verify_bot"] += 1
        return {"id": 999, "is_bot": True, "username": "stub_bot"}

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(tg, "verify_bot", _track_verify_bot)
    monkeypatch.setattr(tg, "delete_webhook", _noop)
    monkeypatch.setattr(tg_poll, "start_polling", _noop)
    monkeypatch.setattr(tg_poll, "stop_polling", _noop)

    await _create_agent("First", "agent-first")
    await _create_agent("Second", "agent-second")

    # First connect — verify_bot fires.
    await telegram_connect(
        TelegramConnectRequest(**_connect_request_body("agent-first")),
        request=None,
    )
    assert calls["verify_bot"] == 1

    # Second connect (conflict) — verify_bot must NOT fire.
    with pytest.raises(HTTPException):
        await telegram_connect(
            TelegramConnectRequest(**_connect_request_body("agent-second")),
            request=None,
        )
    assert calls["verify_bot"] == 1, (
        "verify_bot was called despite the uniqueness check failing — "
        "the guard fired AFTER the network call instead of before."
    )
