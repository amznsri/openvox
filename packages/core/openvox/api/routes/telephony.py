"""Telephony — Twilio (inbound + outbound), WhatsApp, Telegram.

Inbound:  Twilio voice webhook returns TwiML opening a Media Stream to
          our WS pipeline. WhatsApp/Telegram webhooks are scaffolded.
Outbound: `POST /api/v1/telephony/twilio/place_call` initiates a call
          that — once answered — hits the inbound TwiML route above and
          flows into the same WS pipeline as a browser session.

Public-URL discovery:
    `GET /api/v1/telephony/public_url` asks the ngrok sidecar (if
    running) for its current public HTTPS URL. The dashboard wizard
    uses this to auto-fill webhook URLs across Twilio/Telegram/WeCom/
    Lark — no copy-paste from a terminal needed.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Public URL discovery (ngrok sidecar) ────────────────────────────


@router.get("/public_url")
async def public_url() -> dict[str, Any]:
    """Return the current public webhook base URL, plus its source.

    Resolution order:
      1. `OPENVOX_PUBLIC_URL` env override (for static / custom tunnels).
      2. ngrok sidecar at http://ngrok:4040 (the docker-compose tunnel
         profile starts this; queries `api/tunnels` for the active
         HTTPS forward).
      3. `null` if nothing's reachable — dashboard shows a "set up
         a tunnel first" hint instead of a broken URL.
    """
    import os

    override = (os.environ.get("OPENVOX_PUBLIC_URL") or "").strip()
    if override:
        return {"url": override.rstrip("/"), "source": "env", "available": True}

    # ngrok sidecar lives at the docker-compose service name `ngrok`.
    # Connection refused / DNS failure both just mean "tunnel isn't up",
    # which is the normal case for non-telephony users — log nothing
    # noisier than DEBUG so we don't spam.
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get("http://ngrok:4040/api/tunnels")
        if r.status_code == 200:
            data = r.json()
            for t in data.get("tunnels") or []:
                if t.get("proto") == "https" and t.get("public_url"):
                    return {
                        "url": t["public_url"].rstrip("/"),
                        "source": "ngrok",
                        "available": True,
                        "name": t.get("name"),
                    }
    except Exception as e:
        logger.debug("ngrok lookup failed: %s", e)

    return {
        "url": None,
        "source": None,
        "available": False,
        "hint": (
            "No public tunnel detected. Set OPENVOX_PUBLIC_URL in .env "
            "for a static URL, or run `docker compose --profile tunnel up` "
            "after putting your free NGROK_AUTHTOKEN in .env."
        ),
    }


@router.post("/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    """Twilio voice webhook — return TwiML that opens a Media Stream to our WS.

    Resolution order for agent_id:
      1. Explicit `?agent_id=…` query param (we set this on outbound
         dial-out so the placed call routes back to the right agent).
      2. The To/From number in the Twilio form-encoded body matched
         against any agent's `channels.twilio.phone_numbers` list.
      3. None — TwiML stream opens but the WS handler closes immediately.

    The agent_id is passed to the stream via TwiML `<Parameter>` so
    Twilio echoes it back to us inside the `start` frame.
    """
    # Twilio webhook is application/x-www-form-urlencoded.
    form = await request.form()
    agent_id = request.query_params.get("agent_id", "") or ""
    to_number = (form.get("To") or "").strip()
    from_number = (form.get("From") or "").strip()

    if not agent_id and to_number:
        agent_id = await _agent_by_phone_number(to_number)

    base = str(request.base_url).rstrip("/").replace("http", "ws", 1)
    stream_url = f"{base}/ws/twilio"
    twiml_params = ""
    if agent_id:
        twiml_params = f'<Parameter name="agent_id" value="{agent_id}" />'
    if from_number:
        twiml_params += f'<Parameter name="from" value="{from_number}" />'
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'  <Connect><Stream url="{stream_url}">{twiml_params}</Stream></Connect>'
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


async def _agent_by_phone_number(phone: str) -> str:
    """Return the agent_id that owns this Twilio number, or empty string.

    Lookup convention: agent.channels = {"twilio": {"phone_numbers": ["+1…", ...]}}
    """
    from openvox.db import db_session
    from openvox.db.models import Agent
    from sqlalchemy import select

    async with db_session() as s:
        rows = (await s.execute(select(Agent))).scalars().all()
        for a in rows:
            channels = a.channels or {}
            twilio_cfg = (channels.get("twilio") or {}) if isinstance(channels, dict) else {}
            numbers = twilio_cfg.get("phone_numbers") or []
            if phone in numbers:
                return a.id
    return ""


@router.get("/whatsapp/webhook")
async def whatsapp_verify(
    hub_mode: str = "", hub_challenge: str = "", hub_verify_token: str = ""
) -> Response:
    """Meta webhook verification handshake."""
    from openvox.config import get_settings

    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post("/whatsapp/webhook")
async def whatsapp_event(request: Request) -> dict:
    """Stub: accept inbound WhatsApp message events and route to an agent."""
    body = await request.json()
    return {"received": True, "object": body.get("object")}


# ── WhatsApp Personal (unofficial QR-scan via whatsapp-web.js) ──────
# Separate from WhatsApp Business above. Personal mode uses the
# whatsapp-web.js library running in a sibling Docker service (the
# `whatsapp-bridge` profile). No public URL needed. Account ban risk
# is real — surfaced prominently in the dashboard UI.


class WhatsappPersonalConnectRequest(BaseModel):
    agent_id: str


@router.post("/whatsapp_personal/connect")
async def whatsapp_personal_connect(
    req: WhatsappPersonalConnectRequest,
) -> dict[str, Any]:
    """Spin up the whatsapp client for this agent and mark it enabled.

    The actual QR code arrives a few seconds later — dashboard polls
    ``/whatsapp_personal/status`` until ``status == "qr"`` or ``ready``.
    """
    from openvox.db import db_session
    from openvox.db.models import Agent
    from openvox.telephony import whatsapp_personal as wpp

    if not await wpp.is_bridge_reachable():
        raise HTTPException(
            502,
            "WhatsApp bridge container is not running. Run "
            "`docker compose --profile whatsapp up -d whatsapp-bridge` "
            "to enable WhatsApp Personal mode.",
        )

    # Persist the enabled flag BEFORE telling the bridge to connect, so
    # a crash mid-connect leaves a consistent record we can resume from.
    async with db_session() as s:
        a = await s.get(Agent, req.agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        channels = dict(a.channels or {})
        channels["whatsapp_personal"] = {"enabled": True}
        a.channels = channels

    try:
        result = await wpp.connect(req.agent_id)
    except Exception as e:
        raise HTTPException(502, f"bridge /start failed: {e}") from e

    return {"connected": True, **result}


@router.get("/whatsapp_personal/status/{agent_id}")
async def whatsapp_personal_status(agent_id: str) -> dict[str, Any]:
    """Polled by the dashboard QR view (every ~2s) until ready."""
    from openvox.telephony import whatsapp_personal as wpp

    if not await wpp.is_bridge_reachable():
        return {
            "status": "bridge_offline",
            "hint": "Run `docker compose --profile whatsapp up -d whatsapp-bridge`.",
        }
    try:
        return await wpp.status(agent_id)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.delete("/whatsapp_personal/connect/{agent_id}")
async def whatsapp_personal_disconnect(agent_id: str) -> dict[str, Any]:
    """Tear down + wipe persisted auth. Next connect needs a fresh QR scan."""
    from openvox.db import db_session
    from openvox.db.models import Agent
    from openvox.telephony import whatsapp_personal as wpp

    # Best-effort bridge cleanup — proceed with DB clear even if the
    # bridge is unreachable (operator may have already killed the
    # bridge container; we still want to clear our local record).
    try:
        await wpp.disconnect(agent_id)
    except Exception as e:
        logger.warning("whatsapp_personal bridge disconnect failed: %s", e)

    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        channels = dict(a.channels or {})
        channels.pop("whatsapp_personal", None)
        a.channels = channels

    return {"disconnected": True}


@router.post("/whatsapp_personal/inbound")
async def whatsapp_personal_inbound(request: Request) -> dict[str, Any]:
    """Webhook called by the Node bridge when a message arrives.

    Payload shape (from bridge index.js):
        {
            "agent_id": "<uuid>",
            "from":     "15551234567@c.us",
            "body":     "Hello there",
            "type":     "chat" | "audio" | "image" | ...,
            "timestamp": <unix-seconds>,
            "has_media": true | false
        }

    We dispatch in a background task so the bridge gets a fast ack
    and isn't held by long LLM turns.
    """
    body = await request.json()
    agent_id = body.get("agent_id")
    if not agent_id:
        return {"ok": True, "ignored": True, "reason": "no agent_id"}

    asyncio.create_task(_handle_whatsapp_personal_update(agent_id, body))
    return {"ok": True}


async def _handle_whatsapp_personal_update(
    agent_id: str, msg: dict[str, Any]
) -> None:
    """Run an inbound WhatsApp message through the agent's LLM + reply.

    Text-only for v1: media handling (voice notes / images) deferred
    to a follow-up. Most WhatsApp Personal traffic is text anyway.
    """
    from openvox.db import db_session
    from openvox.db.models import Agent
    from openvox.telephony import whatsapp_personal as wpp

    body_text = (msg.get("body") or "").strip()
    sender = msg.get("from") or ""
    if not body_text:
        logger.info(
            "whatsapp_personal: empty body from %s (type=%s) — ignoring",
            sender, msg.get("type"),
        )
        return

    # Look up the agent for its system_prompt + model + skills.
    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            logger.warning(
                "whatsapp_personal: inbound for unknown agent=%s", agent_id
            )
            return
        # Snapshot fields we need outside the session.
        system_prompt = a.system_prompt
        greeting = a.greeting
        llm_model = a.llm_model

    # Run the same text-turn path the dashboard playground uses, so we
    # get the full skill loop (web_search, lookup_order, etc.) for free.
    from openvox.api.routes.agents import TurnRequest, agent_text_turn

    try:
        result = await agent_text_turn(
            agent_id, TurnRequest(user_text=body_text, history=[])
        )
        reply_text = (result.get("text") or "").strip()
    except Exception:
        logger.exception("whatsapp_personal: agent turn failed")
        reply_text = "Sorry, something went wrong on my end. Please try again."

    if not reply_text:
        # Some agent turns end with no text (pure skill calls, etc.).
        # Don't spam an empty message back; just no-op.
        return

    try:
        await wpp.send_text(agent_id, sender, reply_text)
    except Exception:
        logger.exception("whatsapp_personal: send_text failed")


# ── Telegram ────────────────────────────────────────────────────────
# Full pipeline: verify token / set webhook on connect, then handle
# inbound text + voice Updates by feeding them through a VoiceSession
# and replying with text + optional TTS audio.


class TelegramVerifyRequest(BaseModel):
    bot_token: str


@router.post("/telegram/verify")
async def telegram_verify(req: TelegramVerifyRequest) -> dict[str, Any]:
    """Check a bot token is valid before the user commits to it.

    Mirror of Telegram's `getMe` — returns the bot's identity fields
    so the dashboard can show "Connecting to @acme_voice_bot" in the
    wizard before the user clicks Connect.
    """
    from openvox.telephony import telegram as tg
    try:
        me = await tg.verify_bot(req.bot_token.strip())
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {
        "id": me.get("id"),
        "username": me.get("username"),
        "first_name": me.get("first_name"),
        "can_join_groups": me.get("can_join_groups"),
        "can_read_all_group_messages": me.get("can_read_all_group_messages"),
    }


class TelegramConnectRequest(BaseModel):
    agent_id: str
    bot_token: str
    reply_mode: str = "voice"  # "text" | "voice" | "both"
    # NEW: ingestion mode. "polling" (default) = no public URL needed,
    # bot polls getUpdates from inside OpenVox. "webhook" = the legacy
    # path that requires ngrok / a public HTTPS URL.
    mode: str = "polling"


@router.post("/telegram/connect")
async def telegram_connect(req: TelegramConnectRequest, request: Request) -> dict[str, Any]:
    """Wire a bot to an agent.

    Two ingestion modes are supported, selected by ``req.mode``:

    **polling** (default — no public URL needed)
      1. Verify the token (getMe).
      2. Persist ``Agent.channels.telegram = {mode: "polling", ...}``.
      3. Start a background poller via ``telegram_polling.start_polling``.

    **webhook** (production / opt-in)
      1. Verify the token.
      2. Discover our public URL (env override → ngrok → 502 if neither).
      3. Mint a webhook secret, call ``setWebhook`` with it.
      4. Persist config with ``mode: "webhook"``.

    Both modes share the same downstream ``_handle_telegram_update``
    handler — only the ingestion path differs.
    """
    from openvox.db import db_session
    from openvox.db.models import Agent
    from openvox.telephony import telegram as tg
    from openvox.telephony import telegram_polling as tg_poll

    bot_token = req.bot_token.strip()
    mode = req.mode if req.mode in {"polling", "webhook"} else "polling"

    # Both modes verify the token first — fast fail if it's invalid.
    # Wrap so an invalid token returns 400 (client error) rather than
    # 500. verify_bot itself raises RuntimeError on Telegram-side
    # rejection (e.g., revoked token, wrong format).
    try:
        me = await tg.verify_bot(bot_token)
    except Exception as e:
        raise HTTPException(400, f"invalid bot token: {e}") from e
    reply_mode = req.reply_mode if req.reply_mode in {"text", "voice", "both"} else "voice"

    # Common config fragment for the Agent.channels.telegram dict.
    base_cfg: dict[str, Any] = {
        "mode": mode,
        "bot_token": bot_token,
        "bot_username": me.get("username"),
        "bot_id": me.get("id"),
        "reply_mode": reply_mode,
    }

    if mode == "webhook":
        # Legacy production path — requires a public URL.
        pu = await public_url()
        if not pu.get("available"):
            raise HTTPException(
                502,
                "No public webhook URL — start the ngrok sidecar "
                "(`docker compose --profile tunnel up`) or set "
                "OPENVOX_PUBLIC_URL. Or use polling mode (no URL needed).",
            )
        webhook_url = f"{pu['url']}/api/v1/telephony/telegram/webhook"
        secret = tg.generate_webhook_secret()
        try:
            await tg.set_webhook(bot_token, url=webhook_url, secret_token=secret)
        except Exception as e:
            raise HTTPException(400, f"setWebhook failed: {e}") from e
        base_cfg["webhook_secret"] = secret
        base_cfg["webhook_url"] = webhook_url

    # If the agent was previously in webhook mode, clear that registration
    # so Telegram stops POSTing to a stale URL. Safe no-op if no webhook
    # was ever set.
    if mode == "polling":
        try:
            await tg.delete_webhook(bot_token)
        except Exception as e:
            # Don't fail connect if Telegram is unreachable; the polling
            # loop will retry anyway.
            logger.warning("delete_webhook (during polling-connect) failed: %s", e)

    async with db_session() as s:
        a = await s.get(Agent, req.agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        channels = dict(a.channels or {})
        channels["telegram"] = base_cfg
        a.channels = channels

    # Polling mode: kick off the background loop. Idempotent — if the
    # user reconnects with a new token, this stops + restarts the task.
    if mode == "polling":
        try:
            await tg_poll.start_polling(req.agent_id, bot_token)
        except Exception:
            logger.exception("could not start telegram polling for %s", req.agent_id)

    return {
        "connected": True,
        "mode": mode,
        "bot_username": me.get("username"),
        "webhook_url": base_cfg.get("webhook_url"),
        "reply_mode": reply_mode,
    }


@router.delete("/telegram/connect/{agent_id}")
async def telegram_disconnect(agent_id: str) -> dict[str, Any]:
    """Tear down: stop polling task (if any) + call deleteWebhook +
    clear the per-agent config. Idempotent."""
    from openvox.db import db_session
    from openvox.db.models import Agent
    from openvox.telephony import telegram as tg
    from openvox.telephony import telegram_polling as tg_poll

    # Always try to stop any polling task — safe no-op if there isn't one.
    await tg_poll.stop_polling(agent_id)

    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            raise HTTPException(404, "agent not found")
        channels = dict(a.channels or {})
        tg_cfg = channels.pop("telegram", None)
        a.channels = channels
        if tg_cfg and tg_cfg.get("bot_token"):
            try:
                # deleteWebhook is a no-op if the bot is in polling mode
                # (no webhook was ever set), so it's safe to call always.
                await tg.delete_webhook(tg_cfg["bot_token"])
            except Exception as e:
                # Don't fail the disconnect if Telegram is unreachable —
                # we still want the local record cleared.
                logger.warning("deleteWebhook failed: %s", e)
    return {"disconnected": True}


@router.post("/telegram/webhook")
async def telegram_event(request: Request) -> dict[str, Any]:
    """Receive an Update from Telegram and route it through an agent.

    Authentication: Telegram sends our chosen `secret_token` value in
    the `X-Telegram-Bot-Api-Secret-Token` header on every legitimate
    delivery. We look up the agent by header (constant-time prefix +
    DB scan) — supports having multiple bots per OpenVox install.

    Update kinds we handle:
      - `message.text`  → text-only LLM round-trip (skip STT entirely)
      - `message.voice` → download OGG-Opus, transcribe, LLM, reply
      - everything else → ack and ignore so Telegram doesn't retry
    """
    sent_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    body = await request.json()

    # Find the agent whose stored secret matches.
    from openvox.db import db_session
    from openvox.db.models import Agent
    from sqlalchemy import select

    matched_agent: Agent | None = None
    matched_cfg: dict[str, Any] | None = None
    async with db_session() as s:
        rows = (await s.execute(select(Agent))).scalars().all()
        for a in rows:
            cfg = ((a.channels or {}).get("telegram") or {})
            if cfg.get("webhook_secret") and cfg["webhook_secret"] == sent_secret:
                # Detach from the session so we can use it outside the with-block.
                matched_agent = a
                matched_cfg = cfg
                # Snapshot the fields we need now (lazy-load won't work after exit).
                _ = (a.id, a.system_prompt, a.greeting, a.llm_model, a.llm_provider,
                     a.stt_provider, a.tts_provider, a.voice_id, a.voice_language,
                     a.voice_speed, a.temperature, a.max_tokens, list(a.skills or []),
                     dict(a.voice_map or {}))
                break

    if matched_agent is None or matched_cfg is None:
        # Wrong secret or no agent has Telegram wired up. Don't 401 —
        # Telegram retries 401s aggressively. Just ack quietly.
        logger.info("telegram: unmatched webhook (secret prefix=%r)", sent_secret[:6])
        return {"ok": True, "ignored": True}

    # Run the actual handler in the background so we can ack to Telegram
    # immediately. Telegram cuts the connection after ~60s; long LLM
    # turns would otherwise time out and cause retries.
    import asyncio
    asyncio.create_task(_handle_telegram_update(matched_agent.id, matched_cfg, body))
    return {"ok": True}


async def _handle_telegram_update(
    agent_id: str,
    tg_cfg: dict[str, Any],
    update: dict[str, Any],
) -> None:
    """Process one Telegram Update — runs in a background task.

    Three phases:
      1. Parse the Update → either user_text (text path) or audio_bytes
         (voice path); ignore anything else.
      2. Build a one-shot VoiceSession-equivalent: load the agent's
         providers, run the LLM (and STT first if voice).
      3. Send reply: text always, audio if reply_mode in {"voice","both"}.
    """
    from openvox.db import db_session
    from openvox.db.models import Agent
    from openvox.providers import ProviderType, get_registry
    from openvox.providers.base import (
        AudioChunk, LLMConfig, LLMMessage, LLMProvider,
        STTConfig, STTProvider, TTSConfig, TTSProvider,
    )
    from openvox.telephony import telegram as tg

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    bot_token = tg_cfg.get("bot_token", "")
    reply_mode = tg_cfg.get("reply_mode", "voice")
    user_text = ""

    # ── Resolve user content ────────────────────────────────────────
    if "voice" in message:
        # Voice note path — show "typing" indicator immediately so the
        # user doesn't think the bot died while we download + STT.
        await tg.send_chat_action(bot_token, chat_id, "record_voice")
        voice = message["voice"]
        file_id = voice.get("file_id")
        if not file_id:
            return
        try:
            audio_bytes, ext = await tg.download_file(bot_token, file_id)
        except Exception as e:
            logger.exception("telegram voice download failed")
            await tg.send_text(bot_token, chat_id, f"(couldn't download your voice: {e})")
            return
        # Decode OGG-Opus → 16 kHz PCM s16le, feed through STT.
        user_text = await _telegram_transcribe(audio_bytes, ext)
        # ASR sanitiser — `looks_like_real_speech` rejects empty
        # results, single-char noise, and pure punctuation. Saves an
        # LLM round-trip on background coughs / button-bumps.
        from openvox.utils.text import looks_like_real_speech
        if not looks_like_real_speech(user_text):
            await tg.send_text(bot_token, chat_id, "(I couldn't make out what you said — try again?)")
            return
    elif "text" in message:
        user_text = (message["text"] or "").strip()
        if not user_text:
            return
        # Ignore bot commands like /start /help unless we want to add a
        # custom handler. For now: greet on /start.
        if user_text in {"/start", "/help"}:
            greet = "👋 Send me a text or voice message to start a conversation."
            await tg.send_text(bot_token, chat_id, greet)
            return
        await tg.send_chat_action(bot_token, chat_id, "typing")
    else:
        # photo / sticker / document / etc. — politely defer.
        return

    # ── Run agent (with the FULL skill loop) ────────────────────────
    # The original cut here used a plain `llm.chat()` without `tools=`,
    # which meant the model had no real tool surface — it would
    # hallucinate function calls in plain text in the reply ("Function
    # call begins, query_documents parameters ..."). That text would
    # then get TTS-synthesized and read back to the user. Awful.
    # Fix: run the same skill-loop the orchestrator runs in voice
    # mode, with `tools=runner.tool_specs()` set. Agents that don't
    # have any skills still work — runner.tool_specs() returns an
    # empty list and the loop completes in one iteration.
    import json as _json
    from datetime import datetime, timezone

    from openvox.db.models import Session as DBSession, Transcript
    from openvox.skills import SkillContext
    from openvox.skills.runner import SkillRunner

    # Persist a Session row + user Transcript so this conversation shows
    # up in Observability AND is replayable via the eval framework's
    # "Save as recording" → Replay flow. Without this, Telegram
    # conversations would be invisible to the rest of the platform.
    # Best-effort: DB hiccups never kill the chat reply itself.
    db_session_id = ""
    session_started = datetime.now(timezone.utc)
    try:
        async with db_session() as s:
            row = DBSession(
                agent_id=agent_id,
                channel="telegram",
                caller_id=str((message.get("from") or {}).get("id") or "telegram-user"),
                started_at=session_started,
                status="active",
            )
            s.add(row)
            await s.flush()
            db_session_id = row.id
            s.add(Transcript(session_id=db_session_id, role="user", text=user_text[:8000]))
    except Exception:
        logger.exception("telegram: could not create session row")

    reg = get_registry()
    async with db_session() as s:
        a = await s.get(Agent, agent_id)
        if a is None:
            return
        llm = reg.get(ProviderType.LLM, a.llm_provider)
        tts = reg.get(ProviderType.TTS, a.tts_provider)
        if not isinstance(llm, LLMProvider) or not llm.is_available():
            await tg.send_text(bot_token, chat_id, "(LLM is offline — admin needs to configure provider keys.)")
            return
        system_prompt = a.system_prompt
        llm_model = a.llm_model
        temperature = a.temperature
        max_tokens = a.max_tokens
        voice_id = a.voice_id
        voice_lang = a.voice_language
        skill_ids = list(a.skills or [])
        # Capture mcp_servers as a plain list-of-dicts before the
        # session closes — the JSON column becomes a detached
        # lazy-load proxy otherwise. See the open_agent_mcp docstring
        # for why this transport (and every other text-mode transport)
        # now boots MCP per-turn the same way the voice WS already did.
        mcp_servers = list(a.mcp_servers or [])

    # Bootstrap any MCP servers this agent declared so the LLM
    # actually sees Gmail / Calendar / Notion / etc. tools — without
    # this, the SkillRunner gets only the built-in `a.skills` list,
    # which for the productivity templates is just `["get_time"]`
    # and the LLM dutifully tells the user "configure your Google
    # OAuth on the MCP tab" (the system prompt's fallback for
    # missing tools).
    from openvox.mcp import open_agent_mcp

    answer = ""
    async with open_agent_mcp(mcp_servers) as mcp_extras:
        runner = SkillRunner(
            skill_ids=skill_ids,
            ctx=SkillContext(
                agent_id=agent_id,
                metadata={"source": "telegram", "caller_id": str((message.get("from") or {}).get("id") or "")},
            ),
            extra_skills=mcp_extras,
        )

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_text),
        ]
        cfg = LLMConfig(
            model=llm_model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            tools=runner.tool_specs() or None,
        )

        # Skill loop — same shape as orchestrator._llm_turn / agent_text_turn.
        # Bounded at 6 iterations (CLAUDE.md §8 #46).
        try:
            for _iter in range(6):
                last_chunk = None
                async for chunk in llm.chat_stream(messages, cfg):
                    last_chunk = chunk
                if last_chunk is None:
                    break
                delta = last_chunk.delta or ""
                answer += delta
                tool_calls = last_chunk.tool_calls or []
                if not tool_calls:
                    break
                # Append the assistant tool_calls message (Ark / OpenAI
                # contract — see CLAUDE.md §8 #18).
                messages.append(
                    LLMMessage(role="assistant", content=delta, tool_calls=tool_calls)
                )
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed_args = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except _json.JSONDecodeError:
                        parsed_args = {"_raw": raw_args}
                    if not isinstance(parsed_args, dict):
                        parsed_args = {"_value": parsed_args}
                    logger.info("telegram skill_call: %s args=%s", name, parsed_args)
                    result = await runner.invoke(name, parsed_args)
                    messages.append(
                        LLMMessage(
                            role="tool",
                            tool_call_id=tc.get("id") or "",
                            name=name,
                            content=_json.dumps(result, ensure_ascii=False),
                        )
                    )
            else:
                answer = (answer or "") + (
                    "\n\n(I had to abandon a tool loop — please try a simpler question.)"
                )
        except Exception as e:
            logger.exception("telegram llm call failed")
            await tg.send_text(bot_token, chat_id, f"(model error: {e})")
            return
    answer = (answer or "").strip()

    # Persist the assistant reply + finalize the Session row. Even when
    # the answer is empty we close out the row so it shows up cleanly in
    # Observability (just with turn_count=0).
    if db_session_id:
        try:
            ended = datetime.now(timezone.utc)
            async with db_session() as s:
                if answer:
                    s.add(Transcript(session_id=db_session_id, role="assistant", text=answer[:8000]))
                row = await s.get(DBSession, db_session_id)
                if row is not None:
                    row.ended_at = ended
                    row.duration_ms = int((ended - session_started).total_seconds() * 1000)
                    row.turn_count = 1 if answer else 0
                    # Pricing telemetry — per-char STT/TTS providers
                    # (BytePlus Seed ASR/Speech) bill on these counters.
                    row.stt_chars = len(user_text or "")
                    row.tts_chars = len(answer or "")
                    row.status = "completed"
        except Exception:
            logger.exception("telegram: could not finalize session row")

    if not answer:
        await tg.send_text(bot_token, chat_id, "(no response from the model)")
        return

    # ── Reply ───────────────────────────────────────────────────────
    if reply_mode in {"text", "both"}:
        await tg.send_text(bot_token, chat_id, answer)

    if reply_mode in {"voice", "both"} and isinstance(tts, TTSProvider) and tts.is_available():
        try:
            ogg_bytes = await _telegram_synthesize_ogg(
                tts, answer, voice_id=voice_id, language=voice_lang
            )
            if ogg_bytes:
                await tg.send_voice(bot_token, chat_id, ogg_bytes)
        except Exception as e:
            logger.exception("telegram tts encode failed")
            # If we already sent text via "both" mode, we're done. If
            # we were "voice" only, fall back to text.
            if reply_mode == "voice":
                await tg.send_text(bot_token, chat_id, answer)
                await tg.send_text(bot_token, chat_id, f"(voice reply failed: {e})")


async def _telegram_transcribe(audio_bytes: bytes, ext: str) -> str:
    """OGG-Opus / mp3 / m4a → text via the streaming STT path.

    Reuses the playground's PCM-via-pydub + push-frames-to-WS pattern,
    which already handles every format the BytePlus streaming endpoint
    can't consume directly. Returns "" on any failure.

    Format-hint normalisation:
        Telegram's voice-note files come back as `<id>.oga` — OGG
        container, Opus codec. `_decode_to_pcm16k` (the playground
        decoder) sniffs format from the *file extension*, and its
        recognised list doesn't include `oga`. Without a format hint
        ffmpeg refuses to guess and we get `code 183: Invalid data`.
        Coerce `oga → ogg` here so the decoder picks the right path.
    """
    from openvox.api.routes.playground import _decode_to_pcm16k, _stream_pcm_to_stt
    from openvox.providers import ProviderType, get_registry
    from openvox.providers.base import STTProvider
    import asyncio

    stt = get_registry().get(ProviderType.STT, "byteplus")
    if not isinstance(stt, STTProvider) or not stt.is_available():
        return ""

    # Map Telegram-isms onto the decoder's recognised extensions.
    ext_norm = (ext or "").lower().lstrip(".")
    if ext_norm in {"oga", "opus"}:
        ext_norm = "ogg"
    elif ext_norm == "":
        ext_norm = "ogg"  # voice messages are OGG by default

    try:
        pcm, duration_ms = await asyncio.to_thread(
            _decode_to_pcm16k, audio_bytes, None, f"voice.{ext_norm}"
        )
    except Exception:
        logger.exception("telegram: pcm decode failed (ext=%s normalized=%s)", ext, ext_norm)
        return ""
    try:
        transcript, _ = await _stream_pcm_to_stt(pcm, duration_ms, stt, language="en-US")
    except Exception:
        logger.exception("telegram: stt stream failed")
        return ""
    return (transcript or "").strip()


async def _telegram_synthesize_ogg(
    tts, text: str, *, voice_id: str, language: str
) -> bytes:
    """TTS → OGG-Opus voice-note bytes for `sendVoice`.

    Telegram requires OGG-Opus for voice notes (renders the waveform
    bubble). Our TTS providers emit raw PCM s16le, so we shell out to
    ffmpeg (already in the core image) for the codec swap. Doing it
    here in-process keeps the round-trip serialised and easy to debug.

    TTS sanitisation before synthesis:
        LLMs emit text optimised for reading, not listening. Markdown
        emphasis, URLs, emoji, repeated punctuation, hyphens in
        compound words all read terribly when spoken. Centralised
        cleanup in `clean_for_tts` so every TTS-emitting code path
        gets the same treatment. See openvox/utils/text.py for the
        full list of patterns + rationale per pattern.
    """
    from openvox.providers.base import TTSConfig
    from openvox.utils.text import clean_for_tts
    import asyncio
    import subprocess
    import tempfile

    spoken = clean_for_tts(text)

    # 1. Synthesize full PCM stream.
    cfg = TTSConfig(voice_id=voice_id, language=language, sample_rate=24000, encoding="pcm16")
    pcm = await tts.synthesize(spoken, cfg)
    if not pcm:
        return b""

    # 2. Pipe PCM → ffmpeg → OGG-Opus.
    def _encode() -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as out:
            out_path = out.name
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error",
                    "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0",
                    "-c:a", "libopus", "-b:a", "32k", "-application", "voip",
                    "-y", out_path,
                ],
                input=pcm,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            with open(out_path, "rb") as f:
                return f.read()
        finally:
            import os
            try:
                os.unlink(out_path)
            except Exception:
                pass

    return await asyncio.to_thread(_encode)


# ── WeChat Work / Lark webhook delegators ───────────────────────────
# The actual handlers live in openvox/telephony/{wechat_work,lark}.py
# so the channel-specific signature-verification and event-parsing
# logic stays out of this router file. We register them under the same
# /api/v1/telephony prefix for consistency.

from openvox.telephony.wechat_work import router as _wechat_router
from openvox.telephony.lark import router as _lark_router

router.include_router(_wechat_router)
router.include_router(_lark_router)


# ── Outbound dial-out ────────────────────────────────────────────


class PlaceCallRequest(BaseModel):
    to: str  # E.164 phone number, e.g. "+14155550101"
    agent_id: str
    lead_id: str | None = None
    callback_url: str | None = None
    from_number: str | None = None


@router.post("/twilio/place_call")
async def twilio_place_call(req: PlaceCallRequest, request: Request) -> dict[str, Any]:
    """Initiate an outbound Twilio call. Returns Twilio's call resource.

    `callback_url` defaults to this host's `/api/v1/telephony/twilio/voice`.
    For local development, expose your machine via ngrok and pass the
    public ngrok URL so Twilio can actually reach it.
    """
    from openvox.telephony import place_call

    callback = req.callback_url or (
        str(request.base_url).rstrip("/") + "/api/v1/telephony/twilio/voice"
    )
    try:
        result = await place_call(
            to=req.to,
            agent_id=req.agent_id,
            callback_url=callback,
            lead_id=req.lead_id,
            from_number=req.from_number,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "sid": result.get("sid"),
        "status": result.get("status"),
        "to": result.get("to"),
        "from": result.get("from"),
        "callback_url": callback,
    }
