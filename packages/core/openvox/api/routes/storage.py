"""Stream files from the local-storage backend (dev convenience)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from openvox.storage import get_storage

router = APIRouter()


@router.get("/{path:path}")
async def download(path: str) -> Response:
    storage = get_storage()
    if not await storage.exists(path):
        raise HTTPException(404, "not found")
    data = await storage.download(path)
    return Response(content=data, media_type="application/octet-stream")
