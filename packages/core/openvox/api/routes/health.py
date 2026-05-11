from fastapi import APIRouter

from openvox import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/")
async def root() -> dict:
    return {
        "service": "openvox-core",
        "version": __version__,
        "docs": "/docs",
    }
