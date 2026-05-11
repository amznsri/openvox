"""Twilio outbound dial-out.

We use Twilio's REST API directly (POST /Calls.json) — no need for the
heavy `twilio` SDK for a single endpoint. Twilio dials the number,
connects, and POSTs a webhook to `callback_url` for TwiML. Our existing
`/api/v1/telephony/twilio/voice` route returns TwiML that opens a Media
Stream back to the core WS pipeline — so once we initiate the call, the
agent talks normally.

The dialled call carries `agent_id` and an `outbound_lead_id` as TwiML
parameters so the WS handler can route to the right agent + record the
disposition under the right lead.
"""

from __future__ import annotations

import logging
from typing import Any

from openvox.config import get_settings
from openvox.utils.http import make_async_client

logger = logging.getLogger(__name__)


def _account_url(account_sid: str) -> str:
    return f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"


async def place_call(
    *,
    to: str,
    agent_id: str,
    callback_url: str,
    lead_id: str | None = None,
    from_number: str | None = None,
) -> dict[str, Any]:
    """Initiate an outbound Twilio call.

    - `to`           E.164 number to dial (e.g. "+14155550101").
    - `agent_id`     Which OpenVox agent picks up the connection.
    - `callback_url` Publicly reachable URL Twilio will hit for TwiML. Usually
                     `https://<your-host>/api/v1/telephony/twilio/voice
                     ?agent_id=<id>&lead_id=<lead>`.
    - `lead_id`      Optional — surfaced to skills via the WS start payload.
    - `from_number`  Defaults to `TWILIO_PHONE_NUMBER` from .env.

    Returns Twilio's full call resource on success; raises on failure.
    """
    s = get_settings()
    sid = s.twilio_account_sid
    token = s.twilio_auth_token
    sender = from_number or s.twilio_phone_number
    if not (sid and token and sender):
        raise RuntimeError(
            "Twilio not configured. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
            "and TWILIO_PHONE_NUMBER in .env."
        )

    # Pass our routing info as query params on the TwiML URL so the inbound
    # webhook can pull agent_id / lead_id back out.
    sep = "&" if "?" in callback_url else "?"
    url = (
        f"{callback_url}{sep}agent_id={agent_id}"
        + (f"&lead_id={lead_id}" if lead_id else "")
    )

    payload = {
        "To": to,
        "From": sender,
        "Url": url,
        "Method": "POST",
        # Useful for retries / status tracking; pointed at the same host.
        "StatusCallback": url,
        "StatusCallbackMethod": "POST",
        "Record": "false",
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    # Basic auth with sid:token. httpx handles this via `auth=`.
    async with make_async_client(timeout=30.0, headers=headers) as c:
        r = await c.post(_account_url(sid), data=payload, auth=(sid, token))
    if r.status_code >= 400:
        raise RuntimeError(f"Twilio dial-out {r.status_code}: {r.text[:300]}")
    data = r.json()
    logger.info("twilio: placed call sid=%s to=%s agent=%s lead=%s",
                data.get("sid"), to, agent_id, lead_id)
    return data
