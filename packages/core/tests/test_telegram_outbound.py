"""Telegram outbound channel (D.tg-out — v0.2.23).

Inbound was wired in v0.2.11 (polling + webhook), but the agent
couldn't initiate a conversation — no way to push "your weekly
digest is ready" from a cron, no way to fire a notification from
inside a skill, no scheduled-job kind for blast-style sends.

This commit adds:
  - ``POST /api/v1/telephony/telegram/send`` for single-shot sends
    from any internal caller.
  - ``outbound_telegram`` scheduled-job kind for cron-driven
    multi-recipient sends with preview / skill-fetch / agent-query
    knobs.

These tests pin both surfaces:

  test_send_route_requires_connected_bot
      The 400 contract — sending from an agent that hasn't been
      through /telegram/connect must NOT silently no-op.

  test_send_route_happy_path
      Bot connected, text non-empty, tg.send_text called with the
      cleaned body. No network — we monkeypatch send_text.

  test_send_route_404_unknown_agent
      Standard CRUD 404 shape.

  test_outbound_scheduler_preview_doesnt_send
      The blast-job kind defaults preview=true. Asserts that even
      with valid recipients, no actual delivery happens until the
      operator flips preview=false.

  test_outbound_scheduler_send_to_explicit_chat_ids
      preview=false path: every chat_id gets `tg.send_text` called
      with the static `message` payload field. Per-recipient
      errors collected; one bad ID doesn't abort the batch.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest


@asynccontextmanager
async def _app_client():
    """In-process ASGI client. Same pattern as test_oauth_google_routes.

    Built per-test rather than as a session fixture so each test's
    monkeypatched env vars (if any) take effect at app construction
    time — the FastAPI lifespan reads settings on startup.
    """
    from openvox.api.app import create_app

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# Mirror the helper from test_telegram_uniqueness.py — same shape
# so the fixtures are interchangeable. Kept as a duplicate (vs
# imported) so each test file stays self-contained.
async def _create_agent(
    agent_id: str,
    *,
    with_tg_token: str | None = None,
    name: str = "Test agent",
) -> None:
    from openvox.db import db_session
    from openvox.db.models import Agent

    channels: dict = {}
    if with_tg_token:
        channels["telegram"] = {"bot_token": with_tg_token, "mode": "polling"}

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
                channels=channels,
                mcp_servers=[],
                voice_map={},
                status="draft",
            )
        )


@pytest.fixture
def _capture_send_text(monkeypatch):
    """Collect every (token, chat_id, text) tuple in a list. Returns
    the list so tests can assert ordering / contents."""
    sends: list[tuple[str, object, str]] = []

    async def _capture(token: str, chat_id, text: str) -> None:
        sends.append((token, chat_id, text))

    import openvox.telephony.telegram as tg
    monkeypatch.setattr(tg, "send_text", _capture)
    return sends


# ── /telegram/send route ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_route_404_unknown_agent(isolated_db):
    async with _app_client() as app_client:
        r = await app_client.post(
            "/api/v1/telephony/telegram/send",
            json={"agent_id": "no-such-agent", "chat_id": 123, "text": "hi"},
        )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_send_route_requires_connected_bot(isolated_db):
    await _create_agent("agent-no-tg")  # no telegram in channels
    async with _app_client() as app_client:
        r = await app_client.post(
            "/api/v1/telephony/telegram/send",
            json={"agent_id": "agent-no-tg", "chat_id": 123, "text": "hi"},
        )
    assert r.status_code == 400
    assert "telegram bot connected" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_send_route_happy_path(isolated_db, _capture_send_text):
    await _create_agent("agent-with-tg", with_tg_token="111:fake-token")
    async with _app_client() as app_client:
        r = await app_client.post(
            "/api/v1/telephony/telegram/send",
            json={"agent_id": "agent-with-tg", "chat_id": 42, "text": "Hello *world*"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] is True
    assert body["chat_id"] == 42
    assert len(_capture_send_text) == 1
    token, chat_id, _ = _capture_send_text[0]
    assert token == "111:fake-token"
    assert chat_id == 42


@pytest.mark.asyncio
async def test_send_route_empty_text_is_noop(isolated_db, _capture_send_text):
    """Empty body shouldn't blow up — just return `{sent: false}`
    so callers can passthrough optional fields without guarding."""
    await _create_agent("agent-empty-text", with_tg_token="111:fake")
    async with _app_client() as app_client:
        r = await app_client.post(
            "/api/v1/telephony/telegram/send",
            json={"agent_id": "agent-empty-text", "chat_id": 1, "text": "   "},
        )
    assert r.status_code == 200
    assert r.json()["sent"] is False
    assert _capture_send_text == []


# ── outbound_telegram scheduler job kind ──────────────────────────


@pytest.mark.asyncio
async def test_outbound_scheduler_preview_doesnt_send(isolated_db, _capture_send_text):
    """preview=true is the default. Even with valid recipients +
    bot token, the kind must return a preview structure and NOT
    call tg.send_text. This is the load-bearing safety rail —
    a misconfigured cron firing 10K messages would be very bad."""
    from openvox.scheduler.runner import _dispatch

    await _create_agent("agent-blast", with_tg_token="111:fake")
    out, err = await _dispatch(
        kind="outbound_telegram",
        agent_id="agent-blast",
        payload={
            "chat_ids": [1, 2, 3],
            "message": "Static blast body.",
            # preview omitted — defaults to True.
        },
    )
    assert err == "", err
    assert out["preview"] is True
    assert out["would_send_to"] == [1, 2, 3]
    assert _capture_send_text == []


@pytest.mark.asyncio
async def test_outbound_scheduler_send_to_explicit_chat_ids(
    isolated_db, _capture_send_text
):
    """preview=false path. Every chat_id gets a delivery. The
    cleaned body is sent verbatim (we don't check the cleaning
    rules — that's clean_for_tts's own test surface)."""
    from openvox.scheduler.runner import _dispatch

    await _create_agent("agent-blast2", with_tg_token="222:fake")
    out, err = await _dispatch(
        kind="outbound_telegram",
        agent_id="agent-blast2",
        payload={
            "chat_ids": [10, 20, 30],
            "message": "Weekly summary: 3 incidents.",
            "preview": False,
        },
    )
    assert err == "", err
    assert out["sent"] == 3
    assert out["chat_ids"] == [10, 20, 30]
    assert out["errors"] == []
    assert len(_capture_send_text) == 3
    # Tokens + recipients line up; we don't assert order strictly
    # because the scheduler doesn't guarantee it (sequential here,
    # could become parallel later).
    chat_ids_sent = sorted(c for _, c, _ in _capture_send_text)
    assert chat_ids_sent == [10, 20, 30]


@pytest.mark.asyncio
async def test_outbound_scheduler_collects_per_recipient_errors(
    isolated_db, monkeypatch
):
    """One bad chat_id (e.g. bot kicked from the chat) should NOT
    abort the rest of the batch. The bad ones land in `errors`."""
    sends: list = []

    async def _flaky(token, chat_id, text):
        if chat_id == 20:
            raise RuntimeError("chat 20 said no")
        sends.append((token, chat_id, text))

    import openvox.telephony.telegram as tg
    monkeypatch.setattr(tg, "send_text", _flaky)

    await _create_agent("agent-blast3", with_tg_token="333:fake")
    from openvox.scheduler.runner import _dispatch

    out, err = await _dispatch(
        kind="outbound_telegram",
        agent_id="agent-blast3",
        payload={"chat_ids": [10, 20, 30], "message": "ok", "preview": False},
    )
    assert err == ""
    assert out["sent"] == 2          # 10 + 30 succeeded
    assert sorted(out["chat_ids"]) == [10, 30]
    assert len(out["errors"]) == 1
    assert "20: " in out["errors"][0]
    assert "chat 20 said no" in out["errors"][0]


@pytest.mark.asyncio
async def test_outbound_scheduler_rejects_no_recipients(isolated_db):
    """Empty chat_ids + no skill = nothing to do. Return cleanly,
    NOT an error — a cron firing on an empty subscriber list is
    legit (just no one to message this week)."""
    from openvox.scheduler.runner import _dispatch

    await _create_agent("agent-empty", with_tg_token="444:fake")
    out, err = await _dispatch(
        kind="outbound_telegram",
        agent_id="agent-empty",
        payload={"chat_ids": [], "message": "x"},
    )
    assert err == ""
    assert out["sent"] == 0
    assert "no chat_ids" in out["message"].lower()


@pytest.mark.asyncio
async def test_outbound_scheduler_rejects_no_body(isolated_db):
    """A payload with recipients but neither `message` nor
    `agent_query` is misconfigured — fail loudly rather than
    silently no-op (which would look like delivery succeeded)."""
    from openvox.scheduler.runner import _dispatch

    await _create_agent("agent-x", with_tg_token="555:fake")
    out, err = await _dispatch(
        kind="outbound_telegram",
        agent_id="agent-x",
        payload={"chat_ids": [1, 2]},   # no message, no agent_query
    )
    assert "message" in err.lower() and "agent_query" in err.lower()


@pytest.mark.asyncio
async def test_outbound_scheduler_requires_agent_id(isolated_db):
    """Empty agent_id is rejected — no agent, no bot token, no send."""
    from openvox.scheduler.runner import _dispatch

    out, err = await _dispatch(
        kind="outbound_telegram",
        agent_id="",
        payload={"chat_ids": [1], "message": "x"},
    )
    assert "agent_id required" in err
