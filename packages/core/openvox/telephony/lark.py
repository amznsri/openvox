"""Lark / 飞书 inbound channel.

Lark uses the standard webhook + event_v2 protocol — much friendlier
than WeCom's encrypted XML. Setup recipe:

    1. Open Platform → your app → Event Subscriptions → enable.
    2. Set Request URL = `https://yourhost/api/v1/telephony/lark/callback`.
    3. First request from Lark is `url_verification` with a `challenge`
       token — we echo it back.
    4. Subscribe to event types like `im.message.receive_v1`.
    5. Configure on the agent:
         Agent.channels = {"lark": {"app_id": "...", "verification_token": "...",
                                    "encrypt_key": "optional"}}

Event handling pipeline:
    Lark POSTs JSON with `{"schema": "2.0", "header": {...}, "event": {...}}`.
    For voice messages (event.message.message_type == "audio") we
    download the audio via Lark's resource API, transcribe via STT, push
    into VoiceSession, then reply with TTS audio uploaded back to Lark.

Internal vs external apps:
    Internal apps (built by an org for its own use) skip OAuth and use
    `tenant_access_token` minted with app_id + app_secret. External apps
    need the full OAuth dance. v1 here covers internal — extending to
    external is straightforward but unnecessary until we have a tenant
    asking for it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from openvox.db import db_session
from openvox.db.models import Agent

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/lark/callback")
async def lark_event(request: Request) -> dict[str, Any]:
    """Lark event_v2 webhook. Handles url_verification + im.message.receive_v1.

    Returns the challenge token on URL verification; ack-only on inbound
    messages for now (full audio bridge lands once we have a Lark tenant
    to test against).
    """
    body = await request.json()
    typ = body.get("type")  # url_verification | event_callback

    # 1. URL verification: just echo the challenge back so Lark accepts
    # the webhook. We still check the verification_token if the agent
    # has one configured — defence in depth.
    if typ == "url_verification":
        token = body.get("token", "")
        cfg = await _find_lark_config()
        expected = (cfg or {}).get("verification_token", "")
        if expected and expected != token:
            raise HTTPException(401, "verification token mismatch")
        return {"challenge": body.get("challenge", "")}

    # 2. Event callback. The event_v2 envelope wraps everything under
    # `header` + `event`. For `im.message.receive_v1`, event.message
    # has message_type (text/audio/...), content, etc.
    if typ == "event_callback" or body.get("schema") == "2.0":
        header = body.get("header") or {}
        event_type = header.get("event_type", "")
        if event_type == "im.message.receive_v1":
            msg = ((body.get("event") or {}).get("message") or {})
            mtype = msg.get("message_type", "")
            logger.info(
                "lark: inbound %s message (id=%s) — full handler TODO",
                mtype, msg.get("message_id", "")[:12],
            )
        return {"ok": True}

    return {"ok": True}


async def _find_lark_config() -> dict[str, str] | None:
    """First agent with a Lark channel config — single-tenant for v1."""
    async with db_session() as s:
        rows = (await s.execute(select(Agent))).scalars().all()
        for a in rows:
            ch = a.channels or {}
            lk = (ch.get("lark") or {}) if isinstance(ch, dict) else {}
            if lk.get("app_id"):
                return lk
    return None
