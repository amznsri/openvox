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
    """Twilio voice webhook — return TwiML that opens a Media Stream to our WS."""
    base = str(request.base_url).rstrip("/").replace("http", "ws", 1)
    twiml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        f"  <Connect><Stream url=\"{base}/ws/voice/twilio\" /></Connect>"
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")


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
