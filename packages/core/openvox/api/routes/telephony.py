"""Telephony — Twilio (inbound + outbound), WhatsApp, Telegram.

Inbound:  Twilio voice webhook returns TwiML opening a Media Stream to
          our WS pipeline. WhatsApp/Telegram webhooks are scaffolded.
Outbound: `POST /api/v1/telephony/twilio/place_call` initiates a call
          that — once answered — hits the inbound TwiML route above and
          flows into the same WS pipeline as a browser session.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


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


@router.post("/telegram/webhook")
async def telegram_event(request: Request) -> dict:
    body = await request.json()
    return {"received": True, "update_id": body.get("update_id")}


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
