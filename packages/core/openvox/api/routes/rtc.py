"""RTC token issuance for the browser SDK."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from openvox.providers import ProviderType, get_registry
from openvox.providers.base import RTCProvider

router = APIRouter()


class TokenRequest(BaseModel):
    room_id: str
    user_id: str
    role: str = "publisher"
    provider: str = "byteplus"


@router.post("/token")
async def issue_token(req: TokenRequest) -> dict[str, Any]:
    p = get_registry().get(ProviderType.RTC, req.provider)
    if p is None or not isinstance(p, RTCProvider):
        raise HTTPException(404, "rtc provider not registered")
    return await p.issue_token(req.room_id, req.user_id, role=req.role)  # type: ignore[arg-type]
