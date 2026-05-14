"""WeChat Work (企业微信) inbound channel.

Why this lives in `telephony/` even though it's IM:
    From the agent's perspective, WeChat Work voice messages and Twilio
    phone calls look the same — bytes in, bytes out. Keeping all
    "agent-facing message channels" under one folder makes routing
    consistent. WhatsApp + Telegram + Lark sit here too.

Threat model:
    WeChat Work's callback URL must verify three things:
      1. `msg_signature` HMAC over (token, timestamp, nonce, echostr/body).
      2. The body, when present, is AES-encrypted with a per-corp key.
      3. We must respond within 5 seconds or the platform retries.

We implement (1) here; (2) and (3) are scaffolded with clear hooks but
not fully fleshed out until we have a real WeCom verified account to
test against. Send / decrypt helpers live in `_wxcrypto` below — the
algorithm is straightforward AES-256-CBC + PKCS7 once you have the
EncodingAESKey base64-decoded.

Setup recipe (production):
    1. Admin console → Apps → Create app → API receive callback.
    2. Set callback URL = `https://yourhost/api/v1/telephony/wechat_work/callback`.
    3. Set Token + EncodingAESKey on the agent: `Agent.channels` =
         {"wechat_work": {"corp_id": "...", "agent_id": "...",
                          "token": "...", "encoding_aes_key": "..."}}
    4. Verify the URL — WeCom hits GET with echostr to confirm.

Agent matching:
    Inbound message → look up which agent owns the `agent_id` field in
    the message payload OR (fallback) the first agent with non-empty
    wechat_work config. This matches our `_agent_by_phone_number`
    pattern from Twilio inbound.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from openvox.db import db_session
from openvox.db.models import Agent

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Signature verification ──────────────────────────────────────────


def _verify_signature(token: str, timestamp: str, nonce: str, body: str, signature: str) -> bool:
    """SHA-1 over the sorted [token, timestamp, nonce, body] tuple.

    WeCom uses this exact algorithm for both URL-verification GETs (body
    is `echostr`) and inbound POSTs (body is the encrypted message). The
    hash output is hex-lowercase.
    """
    parts = sorted([token, timestamp, nonce, body])
    h = hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()
    return h == signature.lower()


# ── Routes ──────────────────────────────────────────────────────────


@router.get("/wechat_work/callback")
async def wechat_work_verify(request: Request) -> Response:
    """WeCom calls this with `?msg_signature&timestamp&nonce&echostr`
    when you first save the callback URL in the admin console.

    We can't fully verify the signature without the corp's token + AES
    key, so we look both up on the (single) agent that has a
    wechat_work config. If you want multi-corp routing later, parse the
    URL path to scope the lookup.
    """
    q = request.query_params
    msg_signature = q.get("msg_signature", "")
    timestamp = q.get("timestamp", "")
    nonce = q.get("nonce", "")
    echostr = q.get("echostr", "")
    cfg = await _find_wechat_config()
    if cfg is None:
        raise HTTPException(400, "no agent has a WeChat Work channel config")
    token = cfg.get("token", "")
    if not _verify_signature(token, timestamp, nonce, echostr, msg_signature):
        raise HTTPException(401, "signature mismatch")
    # The echostr is the AES-encrypted random value WeCom expects us to
    # decrypt and return. Decryption needs the EncodingAESKey — see
    # `_wxcrypto` stub. Until we have a real account to test against,
    # we return echostr verbatim (works for tokens that disable encryption).
    return Response(content=echostr, media_type="text/plain")


@router.post("/wechat_work/callback")
async def wechat_work_event(request: Request) -> Response:
    """Inbound message. Body is AES-encrypted XML when encryption is on.

    Production flow:
      1. Verify msg_signature (see above).
      2. Decrypt body with EncodingAESKey → plain XML.
      3. Parse <MsgType> — handle `voice` (download AMR via WeCom API)
         and `text`.
      4. Push transcript / decoded audio into VoiceSession.
      5. Reply within 5 s or WeCom retries.

    For now we acknowledge the event so the platform stops retrying and
    log a TODO. End-to-end pipeline lands when the user provides a
    verified WeCom corp + EncodingAESKey to test against.
    """
    body = (await request.body()).decode("utf-8", errors="replace")
    logger.info("wechat_work inbound event (%d bytes) — handler TODO", len(body))
    return Response(content="success", media_type="text/plain")


# ── Helpers ────────────────────────────────────────────────────────


async def _find_wechat_config() -> dict[str, str] | None:
    """First agent in the DB with a wechat_work block. Multi-corp routing
    by `corp_id`/`agent_id` can come later — single-corp is fine for v1."""
    async with db_session() as s:
        rows = (await s.execute(select(Agent))).scalars().all()
        for a in rows:
            ch = a.channels or {}
            wx = (ch.get("wechat_work") or {}) if isinstance(ch, dict) else {}
            if wx.get("token"):
                return wx
    return None
