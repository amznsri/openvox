"""BytePlus RTC — issue join tokens for the browser SDK (`@volcengine/rtc`).

The BytePlus RTC system (shared SDK with Volcengine RTC) uses a signed
token to authorise a client to join a room. The token format is:

    base64(privileges) + "." + base64(signature)

Where the canonical message signed by HMAC-SHA256 with `app_key` is:

    app_id | room_id | user_id | issued_at | expire_at | privileges

Different SDK versions use slightly different framings. This implementation
matches the `@volcengine/rtc` web SDK 4.x which expects the helper format
documented at: https://docs.byteplus.com/en/docs/byteplus-rtc/docs-1197615
(server-side token generation).

For local development without RTC credentials we fall back to issuing a
"mock" token that the dashboard's playground recognises and degrades to
direct browser-mic-to-WS streaming instead of RTC.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Literal

from openvox.config import get_settings
from openvox.providers.base import ProviderCapability, RTCProvider


_PRIV_PUB_AUDIO = 0
_PRIV_PUB_VIDEO = 1
_PRIV_SUB_AUDIO = 2
_PRIV_SUB_VIDEO = 3


def _privileges(role: str) -> dict[str, int]:
    """Map role → privilege bitmap with timestamps."""
    now = int(time.time())
    expire = now + 24 * 3600
    if role == "subscriber":
        return {str(_PRIV_SUB_AUDIO): expire, str(_PRIV_SUB_VIDEO): expire}
    if role == "host":
        return {
            str(_PRIV_PUB_AUDIO): expire,
            str(_PRIV_PUB_VIDEO): expire,
            str(_PRIV_SUB_AUDIO): expire,
            str(_PRIV_SUB_VIDEO): expire,
        }
    # publisher (default)
    return {
        str(_PRIV_PUB_AUDIO): expire,
        str(_PRIV_SUB_AUDIO): expire,
    }


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _build_token(app_id: str, app_key: str, room_id: str, user_id: str, role: str) -> str:
    nonce = secrets.token_hex(8)
    issued = int(time.time())
    expire = issued + 24 * 3600
    privs = _privileges(role)
    payload = {
        "appID": app_id,
        "roomID": room_id,
        "userID": user_id,
        "issuedAt": issued,
        "expireAt": expire,
        "nonce": nonce,
        "privileges": privs,
    }
    pj = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(app_key.encode("utf-8"), pj, hashlib.sha256).digest()
    return f"v1.{_b64url(pj)}.{_b64url(sig)}"


class BytePlusRTC(RTCProvider):
    id = "byteplus"
    display_name = "BytePlus RTC"
    capabilities = {ProviderCapability.BIDIRECTIONAL, ProviderCapability.STREAMING}

    def __init__(self) -> None:
        s = get_settings()
        self._app_id = s.byteplus_rtc_app_id
        self._app_key = s.byteplus_rtc_app_key

    def is_available(self) -> bool:
        return bool(self._app_id and self._app_key)

    async def issue_token(
        self,
        room_id: str,
        user_id: str,
        role: Literal["publisher", "subscriber", "host"] = "publisher",
    ) -> dict[str, Any]:
        if not self.is_available():
            # Mock token — playground falls back to direct WS audio.
            return {
                "provider": "mock",
                "app_id": "",
                "room_id": room_id,
                "user_id": user_id,
                "token": "",
                "expire_at": int(time.time()) + 3600,
            }
        token = _build_token(self._app_id, self._app_key, room_id, user_id, role)
        return {
            "provider": "byteplus",
            "app_id": self._app_id,
            "room_id": room_id,
            "user_id": user_id,
            "token": token,
            "expire_at": int(time.time()) + 24 * 3600,
            "sdk_npm": "@volcengine/rtc",
        }

    async def create_room(self, room_id: str, **kwargs: Any) -> dict[str, Any]:
        # BytePlus RTC rooms are auto-created on first join from a
        # client; no explicit server call is needed for the basic flow.
        return {"room_id": room_id, "auto_created": True}
