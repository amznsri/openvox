"""Telephony webhooks — Twilio Voice + WhatsApp Business + Telegram.

These endpoints are placeholders that wire up the right URLs and payload
shapes; full call-handling needs media-streaming hooks (Twilio Media
Streams) which we accept on the WebSocket side.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response

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
